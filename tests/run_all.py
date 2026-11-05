# -*- coding: utf-8 -*-
r"""Run every test suite.  Usage: .venv\Scripts\python.exe tests\run_all.py"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["run_tests.py", "test_pipeline.py", "test_daemon.py", "test_ui.py",
          "test_daemon_integration.py"]

def main():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    failed = []
    for suite in SUITES:
        print("=" * 70)
        print("running", suite)
        print("=" * 70)
        p = subprocess.run([sys.executable, os.path.join(HERE, suite)],
                           env=env, cwd=os.path.dirname(HERE))
        if p.returncode != 0:
            failed.append(suite)
    print()
    if failed:
        print("FAILED SUITES:", ", ".join(failed))
        return 1
    print("ALL SUITES PASSED")
    return 0

if __name__ == "__main__":
    sys.exit(main())
