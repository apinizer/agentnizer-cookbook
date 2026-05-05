"""Pytest config: load pipeline-daemon.py as a module so tests can import it."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_PATH = REPO_ROOT / ".claude" / "scripts" / "pipeline-daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("pipeline_daemon", DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pipeline_daemon"] = module
    spec.loader.exec_module(module)
    return module


# Eagerly load so any test that imports `pipeline_daemon` gets the live module.
pipeline_daemon = _load_daemon()
