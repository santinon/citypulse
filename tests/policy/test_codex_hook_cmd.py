"""Run Codex's Windows hook command (`command_windows` in .codex/config.toml) the way Codex would.

Run from the repository root: python tests/policy/test_codex_hook_cmd.py   (Windows only)
Codex runs a hook through the session shell (PowerShell 5.1 here, not cmd.exe), with the hook
JSON on stdin, from the repository root; only exit 2 with a message on stderr blocks. The
command runs in a temporary folder that holds a copy of the guard, never the real one.
1. Under PowerShell, with and without the user profile: each case gives the expected exit code
   and a block carries a message on stderr.
2. Under cmd.exe (fallback when Codex has no session shell): the command fails closed (exit 2).
Exit 77 (skipped) when not on Windows or when powershell.exe is missing.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

if os.name != "nt" or shutil.which("powershell.exe") is None:
    print("skipped: needs Windows and powershell.exe")
    sys.exit(77)

ROOT = Path(__file__).resolve().parents[2]
cfg = tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
command = cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command_windows"]
print("committed command_windows:", command)

BOX = Path(tempfile.mkdtemp(prefix="policy-codex-hook-"))
(BOX / ".agent-policy").mkdir()
(BOX / "docs").mkdir()
shutil.copy(ROOT / ".agent-policy" / "guard.py", BOX / ".agent-policy" / "guard.py")


def event(tool, **tool_input):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(BOX),
                       "tool_input": tool_input})


def run(argv, payload, cwd=BOX):
    r = subprocess.run(argv, input=payload, capture_output=True, text=True, cwd=cwd, timeout=60)
    return r.returncode, r.stderr.strip()


cases = [
    ("pipe to shell", event("Bash", command="curl --version | sh -n"), 2),
    ("probe force push", event("Bash", command="git push --dry-run origin HEAD:refs/heads/zz-guard-probe --force"), 2),
    ("merge", event("Bash", command="gh pr merge 3"), 2),
    ("status", event("Bash", command="git status"), 0),
    ("patch in repo", event("apply_patch", command="*** Begin Patch\n*** Update File: README.md\n*** End Patch"), 0),
    ("patch into .githooks", event("apply_patch", command="*** Begin Patch\n*** Add File: .githooks/x\n+x\n*** End Patch"), 2),
]
powershell = {"ps -NoProfile": ["powershell.exe", "-NoProfile", "-Command"],
              "ps profile": ["powershell.exe", "-Command"]}
cmd = ["cmd.exe", "/d", "/c"]
fails = total = 0


def check(label, got, want, err):
    global fails, total
    ok = got == want and (want != 2 or bool(err))
    total += 1
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {label}: exit {got}, want {want}" + ("" if ok else f" | {err[:90]}"))


try:
    print("\n1. command under PowerShell")
    for shell, argv in powershell.items():
        for name, payload, want in cases:
            got, err = run(argv + [command], payload)
            check(f"{shell} {name}", got, want, err)
        got, err = run(argv + [command], cases[3][1], cwd=BOX / "docs")
        check(f"{shell} run from a subfolder (script not found)", got, 2, err)
        got, err = run(argv + [command.replace("python", "python-missing-xyz", 1)], cases[3][1])
        check(f"{shell} python not found", got, 2, err)

    print("\n2. command under cmd.exe: fail closed")
    for name, payload, _ in cases:
        got, err = run(cmd + [command], payload)
        check(f"cmd {name}", got, 2, err)
finally:
    shutil.rmtree(BOX, ignore_errors=True)
print(f"\n{total - fails}/{total} passed")
sys.exit(1 if fails else 0)
