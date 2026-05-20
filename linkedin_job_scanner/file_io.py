from __future__ import annotations

import time
from pathlib import Path


def read_text_with_retries(path: Path, encoding: str = "utf-8", attempts: int = 6) -> str:
    last_exc: OSError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return path.read_text(encoding=encoding)
        except OSError as exc:
            last_exc = exc
            if attempt == attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
    raise last_exc or OSError(f"Could not read {path}")


def write_text_atomic_with_retries(path: Path, text: str, encoding: str = "utf-8", attempts: int = 6) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.stem}.tmp{path.suffix}")
    last_exc: OSError | None = None
    for attempt in range(1, attempts + 1):
        try:
            tmp_path.write_text(text, encoding=encoding)
            tmp_path.replace(path)
            return
        except OSError as exc:
            last_exc = exc
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt == attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
    raise last_exc or OSError(f"Could not write {path}")
