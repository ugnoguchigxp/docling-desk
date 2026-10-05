"""Import path for the browser fixture builder. The fixture directory name contains a hyphen."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_path = Path(__file__).with_name("frontend-migration") / "synthetic_data.py"
_spec = importlib.util.spec_from_file_location("synthetic_data", _path)
if _spec is None or _spec.loader is None:
    raise ImportError("synthetic fixture module is missing")
synthetic_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(synthetic_data)
