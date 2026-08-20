"""CooLEVAL memory-eval providers (Phase 1 protocol).

Abstract MemoryProvider + implementations:
  - UHMAProvider (S1): reads Hermes UHMA warm tier (memory-unified.db) FTS5+LIKECJK.
  - OpenVikingProvider (S3): HTTP client to a local openviking-server (viking://).
  - HolographicProvider (S2): declared, unavailable until holographic package present.

Read-only w.r.t. production Hermes: UHMA provider only SELECTs.
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

DB_PATH = os.path.expanduser("~/.hermes/memory-unified.db")


@dataclass
class MemoryChunk:
    text: str
    domain: str = ""
    importance: float = 0.0
    meta: str = ""  # observable trace (which db/query produced it)


@dataclass
class RecallResult:
    provider: str
    query: str
    chunks: list = field(default_factory=list)
    latency_ms: float = 0.0
    trace: str = ""  # observable retrieval path


class MemoryProvider(ABC):
    name = "abstract"

    @abstractmethod
    def pre_llm_retrieve(self, query: str) -> RecallResult:
        """Return chunks recalled for query. Must fill trace (observable)."""

    def post_llm_write(self) -> None:
        return None  # default no-op; ADD-only systems don't write on eval read


class UHMAProvider(MemoryProvider):
    """S1 — Hermes UHMA warm tier (SQLite FTS5 trigram, CJK LIKE fallback)."""

    name = "uhma"

    def __init__(self, db_path: str = DB_PATH, limit: int = 5):
        self.db_path = db_path
        self.limit = limit

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect("file:" + self.db_path + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def pre_llm_retrieve(self, query: str) -> RecallResult:
        import re as _re
        import time
        t0 = time.time()
        db = self._connect()
        seen, chunks = set(), []
        steps = []

        def add(row):
            if row["id"] in seen:
                return
            seen.add(row["id"])
            chunks.append(MemoryChunk(row["content"], row["domain"],
                                      row["importance"], row["meta"]))

        def fts(q):
            q = q.replace("-", " ").replace("—", " ").strip()
            fts_q = f'"{q}"' if " " in q else q
            try:
                return db.execute(
                    "SELECT w.id,w.domain,w.content,w.importance,'fts' AS meta "
                    "FROM warm_fts f JOIN warm_facts w ON f.rowid=w.id "
                    "WHERE warm_fts MATCH ? AND w.archived_at IS NULL "
                    "ORDER BY rank LIMIT ?", (fts_q, self.limit)).fetchall()
            except sqlite3.OperationalError:
                return []

        def like(kw):
            try:
                return db.execute(
                    "SELECT id,domain,content,importance,'like' AS meta FROM warm_facts "
                    "WHERE (content LIKE ? OR summary LIKE ?) AND archived_at IS NULL "
                    "ORDER BY importance DESC LIMIT ?", (f"%{kw}%", f"%{kw}%", self.limit)
                ).fetchall()
            except sqlite3.OperationalError:
                return []

        # Layer 1: whole-query trigram FTS
        for r in fts(query):
            add(r); steps.append("fts:whole")
        # Layer 2: keyword extraction -> trigram + per-keyword LIKE
        cjk_run = _re.findall(r"[\u4e00-\u9fff]+", query)
        words = [w for w in _re.findall(r"[a-zA-Z0-9_]+", query) if len(w) >= 2]
        kws = list(words)
        for run in cjk_run:
            if len(run) >= 2:
                kws.append(run)
            if len(run) >= 3:  # overlapping 2-3 char CJK substrings
                for i in range(len(run) - 1):
                    kws.append(run[i:i + 2])
                    if i + 2 < len(run):
                        kws.append(run[i:i + 3])
        for kw in kws:
            if len(chunks) >= self.limit:
                break
            rows = fts(kw)
            steps.append(f"fts:{kw}")
            rows = rows or like(kw)
            if rows:
                steps.append(f"like:{kw}")
            for r in rows[:1]:
                add(r)
        db.close()
        return RecallResult(self.name, query, chunks[:self.limit],
                            round((time.time() - t0) * 1000, 2),
                            "uhma " + "; ".join(dict.fromkeys(steps)))


class OpenVikingProvider(MemoryProvider):
    """S3 — openviking-server via its HTTP/REST bridge (viking://).

    Gated: if the server is not reachable, every recall returns [] with a
    trace marking it "server-down". The full benchmark is disabled until a
    server is running (RAM/ops heavy — see plan Phase 4 risks).
    """

    name = "openviking"

    def __init__(self, base: str = "http://127.0.0.1:1933", api_key: str = ""):
        self.base = base.rstrip("/")
        self.api_key = api_key

    def _get(self, path: str):
        req = urllib.request.Request(self.base + path)
        if self.api_key:
            req.add_header("Authorization", "Bearer " + self.api_key)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())

    def pre_llm_retrieve(self, query: str) -> RecallResult:
        try:
            data = self._get("/search?q=" + urllib.parse.quote(query))
            chunks = [MemoryChunk(item.get("text", ""), item.get("uri", ""),
                                  float(item.get("score", 0.0) or 0),
                                  f"viking://{item.get('uri','')}")
                      for item in (data.get("results", []))]
            return RecallResult(self.name, query, chunks, 0.0, "openviking /search")
        except Exception as e:  # noqa: BLE001 — gate failure is expected when down
            return RecallResult(self.name, query, [], 0.0, f"openviking server-down ({e})")


class HolographicProvider(MemoryProvider):
    """S2 — lightweight local SQLite provider (Hermes holographic plugin).

    Uses the plugin's MemoryStore + FactRetriever (FTS5 + Jaccard + HRR) on a
    throwaway store so a controlled S1-vs-S2 corpus can be seeded via add_fact.
    """

    name = "holographic"

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.path.join(
            os.path.expanduser("~/.hermes"), "memory-eval-holo.db")
        try:
            from plugins.memory.holographic.store import MemoryStore
            from plugins.memory.holographic.retrieval import FactRetriever
            self.store = MemoryStore(db_path=self.db_path)
            self.retriever = FactRetriever(self.store)
            self._ok = True
        except Exception as e:  # noqa: BLE001
            self._ok = False
            self._err = str(e)
            self.store = None
            self.retriever = None

    def add_fact(self, content: str, category: str = "general") -> None:
        if self._ok:
            self.store.add_fact(content, category=category)

    def pre_llm_retrieve(self, query: str) -> RecallResult:
        if not self._ok:
            return RecallResult(self.name, query, [], 0.0,
                                f"holographic unavailable ({getattr(self, '_err', '?')})")
        import time
        t0 = time.time()
        try:
            rows = self.retriever.search(query)
            chunks = [MemoryChunk(r.get("content", ""), "holographic",
                                  float(r.get("score", 0.0) or 0),
                                  f"hrr score={r.get('score', 0):.3f}")
                      for r in rows]
            return RecallResult(self.name, query, chunks,
                                round((time.time() - t0) * 1000, 2),
                                "holographic hybrid FTS+Jaccard+HRR")
        except Exception as e:  # noqa: BLE001
            return RecallResult(self.name, query, [], 0.0, f"holographic error {e}")


def get_provider(name: str) -> MemoryProvider:
    return {"uhma": UHMAProvider,
            "openviking": OpenVikingProvider,
            "holographic": HolographicProvider}[name]()
