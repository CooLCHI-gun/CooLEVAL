"""pytest conftest — load the dashed CLI modules (eval-metrics / eval-etl).

The repo's scripts live in scripts/ and are named with dashes
(eval-metrics.py), which Python cannot import directly. This registers each
module under an import-safe name at session start, so tests can
`import eval_metrics` / `import eval_etl`.
"""
import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    safe = name.replace("-", "_")
    if safe in sys.modules:
        return sys.modules[safe]
    spec = importlib.util.spec_from_file_location(safe, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[safe] = mod
    spec.loader.exec_module(mod)
    return mod


# Imported for test modules to reference. Importing only imports (no DB / network
# side effects — evaluation happens in main(), not at module import).
eval_metrics = _load("eval-metrics")
eval_etl = _load("eval-etl")
