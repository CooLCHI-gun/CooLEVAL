"""Unit tests for the pure / deterministic parts of eval-metrics.py."""
import sqlite3

import eval_metrics as m


# ── Wilson 95% CI ──────────────────────────────────────────────────────────
def test_wilson_95_zero():
    assert m.wilson_95(0, 0) == (0.0, 0.0)


def test_wilson_95_contains_rate():
    lo, hi = m.wilson_95(505, 498)
    p = 498 / 505
    assert 0.94 < lo < p < hi < 1.0


def test_fmt_pct_na():
    assert m.fmt_pct(0, 0) == "n/a"


def test_fmt_pct_shapes():
    s = m.fmt_pct(497, 505)
    assert s.startswith("98.4%") and "n=505" in s and "CI" in s


# ── Failure risk ratio (n-gated, success-ratio vs risk-ratio distinction) ──
def test_failure_risk_ratio_empty():
    assert m.failure_risk_ratio([]) is None


def test_failure_risk_ratio_ngate():
    # short bucket below MIN_N -> nothing to divide, must return None
    curve = [{"bucket": "<15m", "lo": 0.0, "n": 5, "success": 4}]
    assert m.failure_risk_ratio(curve) is None


def test_failure_risk_ratio_readme_shape():
    # Rebuild the README's pooled contrast (497/505 short vs 2/27 long).
    curve = [
        {"bucket": "<15m", "lo": 0.0, "n": 505, "success": 497},
        {"bucket": "1-4h", "lo": 1.0, "n": 8, "success": 2},
        {"bucket": "4-24h", "lo": 4.0, "n": 7, "success": 0},
        {"bucket": ">24h", "lo": 24.0, "n": 12, "success": 0},
    ]
    rr = m.failure_risk_ratio(curve)
    assert rr is not None
    expected = (25 / 27) / (8 / 505)  # ~58x
    assert abs(rr["rr"] - expected) < 1e-6
    assert rr["ci_lo"] < rr["rr"] < rr["ci_hi"]


# ── Time horizon (no post-hoc bucket picking) ──────────────────────────────
def test_time_horizon_returns_longest_meeting_bucket():
    curve = [
        {"bucket": "<15m", "lo": 0.0, "n": 30, "success": 28},
        {"bucket": "1-4h", "lo": 1.0, "n": 6, "success": 1},
    ]
    th = m.time_horizon(curve)
    assert th["bucket"] == "<15m"


def test_time_horizon_none_when_all_exploratory():
    curve = [{"bucket": "<15m", "lo": 0.0, "n": 5, "success": 5}]
    th = m.time_horizon(curve)
    assert th["bucket"] is None and th["rate"] is None


# ── Taxonomy validation ────────────────────────────────────────────────────
def test_validate_taxonomy_flags_missing_class():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE task_events (status TEXT, failure_class TEXT)")
    con.execute("INSERT INTO task_events VALUES ('completed', NULL)")   # ok
    con.execute("INSERT INTO task_events VALUES ('failed', 'tool')")    # ok
    con.execute("INSERT INTO task_events VALUES ('failed', NULL)")      # bad
    issues = m.validate_taxonomy(con)
    assert any("not in taxonomy" in i or "missing failure_class" in i for i in issues)
    assert not any("status='completed'" in i for i in issues)


def test_validate_taxonomy_clean():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE task_events (status TEXT, failure_class TEXT)")
    con.execute("INSERT INTO task_events VALUES ('completed', NULL)")
    con.execute("INSERT INTO task_events VALUES ('failed', 'timeout')")
    assert m.validate_taxonomy(con) == []
