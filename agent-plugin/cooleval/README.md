# CooLEVAL agent plugin (Hermes)

A self-contained, **read-only**, **self-hosted** Hermes plugin that lets an agent
read CooLEVAL eval results that exist on the SAME machine — no network, no
phone-home, no shared server.

## What it gives you

Two functions an agent can call directly:

- `get_metrics()` — top-level task success counts from your local `eval.db`
- `get_battery(limit)` — recent battery runs (read-only)

It also registers a lightweight observer on `pre_tool_call` that only confirms
the local `eval.db` is reachable — it never blocks a call and never writes.

## How it's wired (and how it is NOT)

- **Self-hosted:** it opens a local SQLite file in read-only mode
  (`PRAGMA query_only=ON`, `mode=ro`) — it cannot write, and it makes no HTTP /
  network request of any kind.
- **Not connected to anyone's server:** there is no remote URL, no telemetry,
  no shared backend. You point `EVAL_DB` at an eval.db **you built yourself**
  (run `scripts/eval-etl.py` locally). By default it reads a repo-relative
  `./eval.db`; override with `export EVAL_DB=/abs/path/to/your-eval.db`.
- **Not the MCP server:** the MCP server (also self-hosted) lives at
  `scripts/cooleval_mcp.py`. This plugin is a callable-function alternative.

## Install

```bash
git clone https://github.com/CooLCHI-gun/CooLEVAL.git
cd CooLEVAL
python3 scripts/eval-etl.py            # build YOUR eval.db from YOUR telemetry
# tell the plugin where your local db is:
export EVAL_DB="$PWD/eval.db"
# register the plugin (Hermes):
mkdir -p ~/.hermes/plugins && cp -r agent-plugin/cooleval ~/.hermes/plugins/
hermes plugins enable cooleval          # then restart/next session
```

Every number an agent reads through this plugin is from the local file on the
box it runs on — never from a server you don't control.
