"""Console-safe logging for Windows cp1252 consoles."""

from __future__ import annotations

import sys


def safe_print(*args, **kwargs) -> None:
    """print() that never crashes scrapers on non-UTF-8 stdout."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        file = kwargs.get("file", sys.stdout)
        text = sep.join(str(a) for a in args) + end
        raw = text.encode(encoding, errors="replace")
        try:
            file.buffer.write(raw)
            file.buffer.flush()
        except Exception:
            try:
                file.write(raw.decode(encoding, errors="replace"))
            except Exception:
                pass
