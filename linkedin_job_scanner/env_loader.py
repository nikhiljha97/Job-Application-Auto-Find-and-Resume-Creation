from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, resolve_path
from .file_io import read_text_with_retries


def load_env_file(config: dict[str, Any]) -> None:
    env_file = str(config.get("env_file", "")).strip()
    if not env_file:
        return
    path = resolve_path(env_file, PROJECT_ROOT)
    if not path.exists():
        return
    for raw_line in read_text_with_retries(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
