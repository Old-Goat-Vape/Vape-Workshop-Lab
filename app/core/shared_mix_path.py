"""Resolve shared_mix_core for release zips without fixed parents[N] hacks."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _find_spec() -> bool:
    try:
        return importlib.util.find_spec("shared_mix_core") is not None
    except (ValueError, ImportError):
        return False


def _is_shared_mix_package_dir(d: Path) -> bool:
    init_f = d / "__init__.py"
    return d.is_dir() and init_f.is_file()


def _candidate_roots_from_anchor(anchor: Path) -> list[Path]:
    """Ancestors of anchor, deepest first then toward root."""
    roots: list[Path] = []
    cur = anchor.resolve()
    for _ in range(24):
        roots.append(cur)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return roots


def ensure_shared_mix_core_on_sys_path() -> None:
    """Prefer normal imports; else discover shared_mix_core next to app or upward.

    Supported layouts (parent = directory on sys.path, child folder named shared_mix_core):
      app/shared_mix_core/           (zip contains app + package)
      sibling/shared_mix_core/       (repo-style: parent of app/)
      any ancestor/parent/shared_mix_core/

    Does not use fixed parents[3]. If still missing, exit with a clear message.
    """
    if _find_spec():
        return

    here = Path(__file__).resolve()
    core_dir = here.parent
    app_dir = core_dir.parent

    seen: set[Path] = set()
    ordered_roots: list[Path] = []

    for anchor in (app_dir,):
        for r in _candidate_roots_from_anchor(anchor):
            rp = r.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            ordered_roots.append(rp)

    try:
        cwd = Path.cwd().resolve()
        if cwd not in seen:
            for r in _candidate_roots_from_anchor(cwd):
                rp = r.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                ordered_roots.append(rp)
    except OSError:
        pass

    for root in ordered_roots:
        pkg_dir = root / "shared_mix_core"
        if not _is_shared_mix_package_dir(pkg_dir):
            continue
        sroot = str(root)
        if sroot not in sys.path:
            sys.path.insert(0, sroot)
        if _find_spec():
            return
        try:
            sys.path.remove(sroot)
        except ValueError:
            pass

    sys.stderr.write(
        "shared_mix_core folder is missing. Re-extract the full release zip.\n"
    )
    raise SystemExit(1)
