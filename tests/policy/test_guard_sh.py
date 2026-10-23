"""Black-box tests for .agent-policy/guard.sh (the wrapper Claude Code's hook calls).

Run from the repository root: python tests/policy/test_guard_sh.py [path/to/guard.sh] [path/to/guard.py]
Defaults: the committed files, copied into a temporary folder. guard.sh passes guard.py's exit
code through: 0 allows, 2 blocks, and a crash gives 1. Claude Code blocks on 1 only because the
hook sets `"onFailure": "block"` (hooks reference, needs Claude Code >= 2.1.295; decision R3 of
the PR #20 recheck triage), so this test also checks that setting in the committed settings.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SH = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / ".agent-policy" / "guard.sh"
PY = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT / ".agent-policy" / "guard.py"
print(f"wrapper under test: {SH}\nguard under test:   {PY}")
BOX = Path(tempfile.mkdtemp(prefix="policy-guard-sh-"))

ALLOWED = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(BOX)})
BLOCKED = json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh pr merge 3"}, "cwd": str(BOX)})
BROKEN = {
    "crash": "raise RuntimeError('boom')\n",
    "exit 1": "import sys\nsys.exit(1)\n",
    "syntax error": "def (:\n",
}
real = PY.read_text(encoding="utf-8")
cases = [("real guard, allowed call", real, ALLOWED, 0),
         ("real guard, refused call", real, BLOCKED, 2)]
cases += [(f"broken guard ({k}): non-zero, not 0", v, ALLOWED, "nonzero") for k, v in BROKEN.items()]

fails = 0
try:
    for label, guard_src, payload, want in cases:
        shutil.rmtree(BOX / ".agent-policy", ignore_errors=True)
        (BOX / ".agent-policy").mkdir()
        shutil.copy(SH, BOX / ".agent-policy" / "guard.sh")
        (BOX / ".agent-policy" / "guard.py").write_text(guard_src, encoding="utf-8")
        proc = subprocess.run(["sh", str(BOX / ".agent-policy" / "guard.sh")], input=payload,
                              text=True, capture_output=True, cwd=BOX)
        ok = proc.returncode != 0 if want == "nonzero" else proc.returncode == want
        fails += not ok
        print(f"{'OK  ' if ok else 'FAIL'} want={want} got={proc.returncode}  {label}")
finally:
    shutil.rmtree(BOX, ignore_errors=True)

settings = json.loads(subprocess.run(["git", "show", "HEAD:.claude/settings.json"], cwd=ROOT,
                                     capture_output=True, text=True, check=True).stdout)
hooks = [h for entry in settings["hooks"]["PreToolUse"] for h in entry["hooks"]]
on_failure = [h.get("onFailure") for h in hooks if "guard.sh" in h.get("command", "")]
ok = on_failure == ["block"]
fails += not ok
print(f"{'OK  ' if ok else 'FAIL'} committed Claude hook calling guard.sh has onFailure=block: {on_failure}")
total = len(cases) + 1
print(f"\n{total - fails}/{total} passed")
sys.exit(1 if fails else 0)
