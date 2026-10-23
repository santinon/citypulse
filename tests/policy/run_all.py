"""Run every agent policy test and report pass, fail or skip.

Run from the repository root, in Git Bash on Windows: python tests/policy/run_all.py
Each test works on temporary copies and never changes the real agent settings. A test exits
0 (pass), 77 (skipped: tool or platform missing) or anything else (fail); a failing test's
full output is printed. Exit code: 0 when nothing failed, 1 otherwise.
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SKIP = 77
TESTS = [
    ("guard", [sys.executable, "-I", str(HERE / "test_guard.py")]),
    ("guard.sh wrapper", [sys.executable, "-I", str(HERE / "test_guard_sh.py")]),
    ("git hooks", ["sh", str(HERE / "test_githooks.sh")]),
    ("Codex hook command", [sys.executable, "-I", str(HERE / "test_codex_hook_cmd.py")]),
    ("Codex rules", [sys.executable, "-I", str(HERE / "test_codex_rules.py")]),
]

failed = 0
for name, argv in TESTS:
    if shutil.which(argv[0]) is None:
        failed += 1
        print(f"FAIL  {name}: {argv[0]} not found on PATH")
        continue
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    lines = proc.stdout.strip().splitlines()
    summary = lines[-1] if lines else "(no output)"
    status = "PASS" if proc.returncode == 0 else "SKIP" if proc.returncode == SKIP else "FAIL"
    print(f"{status}  {name}: {summary}")
    if status == "FAIL":
        failed += 1
        print("\n".join("      " + line for line in (proc.stdout + proc.stderr).splitlines()))

print(f"\npolicy tests: {len(TESTS) - failed}/{len(TESTS)} without failure")
sys.exit(1 if failed else 0)
