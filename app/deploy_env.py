"""Load optional local .env into os.environ before Flask reads SECRET_KEY / paths.

Does not add a dependency: KEY=VALUE lines only; no shell-style export keyword.
Existing environment variables are never overwritten.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env() -> None:
    path = Path(__file__).resolve().parent / ".env"
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key not in os.environ:
            os.environ[key] = val
