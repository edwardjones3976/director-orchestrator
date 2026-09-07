# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | BEGIN]]
"""Real Linux kernel regression, runnable without provider dependencies.

Run explicitly as root on a disposable cgroup v2-capable Linux test host.
AST extraction executes the actual source helpers; it does not imitate them.
"""
import ast
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0,
                     "requires explicit root Linux cgroup test lane")
class ProcessLifetimeTests(unittest.TestCase):
    def test_setsid_descendant_dies_on_success_and_timeout(self):
        source = Path(__file__).resolve().parents[1] / "src/director_orchestrator/antigravity_team_worker.py"
        tree = ast.parse(source.read_text())
        names = {"protected_bytes", "protected_directory", "_limits", "require_cgroup_root", "run_unprivileged"}
        tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        scope = dict(os=os, sys=sys, ctypes=ctypes, Path=Path, subprocess=subprocess, tempfile=tempfile,
                     time=time, TeamAuthorizationError=RuntimeError)
        exec(compile(tree, str(source), "exec"), scope)
        root = Path("/sys/fs/cgroup") / ("director-test-" + os.urandom(8).hex())
        root.mkdir()
        try:
            for wait in (False, True):
                # Child leaves both original session and process group, then
                # persists without CPU consumption if containment is defective.
                code = "import os,time\nr,w=os.pipe()\np=os.fork()\nif p==0:\n os.setsid()\n print(os.getpid(),os.getsid(0),flush=True)\n os.write(w,b'x')\n time.sleep(60)\nelse:\n os.read(r,1)\n" + (" time.sleep(60)\n" if wait else " pass\n")
                start = time.monotonic()
                try:
                    _, output, _ = scope["run_unprivileged"]([sys.executable, "-c", code], uid=65534,
                        gid=65534, cwd="/", home="/nonexistent", timeout=1, cgroup_root=root)
                    self.assertFalse(wait)
                    child, session = output.decode().split()
                    self.assertEqual(child, session)
                except subprocess.TimeoutExpired:
                    self.assertTrue(wait)
                self.assertLess(time.monotonic()-start, 7)
                self.assertIn("populated 0", (root / "cgroup.events").read_text())
                self.assertFalse([p for p in root.iterdir() if p.is_dir()])
        finally:
            (root / "cgroup.kill").write_text("1")
            root.rmdir()


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:27 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 20:37 UTC | END]]
