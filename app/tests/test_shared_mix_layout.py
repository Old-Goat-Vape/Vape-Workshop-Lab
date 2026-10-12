"""Shallow zip layout: shared_mix_core folder next to app.py must be discoverable."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _reference_shared_mix_core() -> Path | None:
    """Developer tree: Desktop/shared_mix_core next to The_Vape_Workshop_Calculator/app."""
    here = Path(__file__).resolve()
    app_dir = here.parents[1]
    candidate = app_dir.parent.parent / "shared_mix_core"
    if (candidate / "__init__.py").is_file():
        return candidate
    candidate2 = app_dir / "shared_mix_core"
    if (candidate2 / "__init__.py").is_file():
        return candidate2
    return None


class SharedMixCoreLayoutTests(unittest.TestCase):
    def test_probe_imports_shared_mix_from_app_subfolder(self):
        src_pkg = _reference_shared_mix_core()
        if src_pkg is None:
            self.skipTest("shared_mix_core reference folder not found for layout test")

        sp_src = Path(__file__).resolve().parents[1] / "core" / "shared_mix_path.py"
        if not sp_src.is_file():
            self.skipTest("shared_mix_path.py missing")

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = base / "release" / "app"
            core = app / "core"
            core.mkdir(parents=True)
            shutil.copytree(src_pkg, app / "shared_mix_core")
            shutil.copy(sp_src, core / "shared_mix_path.py")
            probe = core / "_probe_shared.py"
            probe.write_text(
                "from shared_mix_path import ensure_shared_mix_core_on_sys_path\n"
                "ensure_shared_mix_core_on_sys_path()\n"
                "import shared_mix_core.policy as p\n"
                "print(p.tvw_mix_policy().name)\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            proc = subprocess.run(
                [sys.executable, str(probe)],
                cwd=str(core),
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("tvw_lenient", proc.stdout)
