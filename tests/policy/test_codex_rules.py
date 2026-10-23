"""Check .codex/rules/default.rules with Codex's own parser (`codex execpolicy check`).

Run from the repository root: python tests/policy/test_codex_rules.py
Commands are passed as tokens to the static checker; nothing is executed. Uses the Codex CLI
bundled in the VS Code extension (newest installed version) on a temporary copy of the rules.
Exit 77 (skipped) when that CLI is not installed.
"""
import glob
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

exts = sorted(glob.glob(str(Path.home() / ".vscode/extensions/openai.chatgpt-*-win32-x64")))
codex = Path(exts[-1]) / "bin/windows-x86_64/codex.exe" if exts else None
if codex is None or not codex.exists():
    print("skipped: Codex CLI of the VS Code extension not found")
    sys.exit(77)

ROOT = Path(__file__).resolve().parents[2]
BOX = Path(tempfile.mkdtemp(prefix="policy-codex-rules-"))
rules = BOX / "default.rules"
shutil.copy(ROOT / ".codex" / "rules" / "default.rules", rules)

cases = [
    ("gh pr merge 3 --squash", "forbidden"),
    ("gh api repos/x", "forbidden"),
    ("git push --force origin main", "forbidden"),
    ("git push --no-verify origin x", "forbidden"),
    ("git commit -n -m x", "forbidden"),
    ("git reset --hard", "forbidden"),
    ("git worktree add ../velo_project-codex -b 4-x main", "forbidden"),
    ("npm i -g x", "forbidden"),
    ("Start-Process notepad", "forbidden"),
    ("git push -u origin 4-x", "allow"),
    ("gh pr create --fill", "allow"),
    ("firecrawl scrape https://example.org", "allow"),
    ("uv add httpx", "allow"),
    ("gh issue create --title t", "prompt"),
    ("rm -rf x", "prompt"),
    ("git stash drop", "prompt"),
    ("git status", "none"),
    # Known prefix-rule limit: a trailing flag is invisible; the shared guard must refuse it.
    ("git push origin x --force", "allow"),
]

fails = 0
try:
    for i, (cmd, want) in enumerate(cases):
        out = subprocess.run([str(codex), "execpolicy", "check", "-r", str(rules), *cmd.split()],
                             capture_output=True, text=True)
        if i == 0:
            print("raw output sample:", out.stdout.strip()[:300], out.stderr.strip()[:200])
        try:
            got = json.loads(out.stdout).get("decision") or "none"
        except json.JSONDecodeError:
            got = f"unparsed: {out.stdout.strip()[:80]!r} {out.stderr.strip()[:80]!r}"
        ok = got == want
        fails += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {cmd!r}: got {got}, want {want}")
finally:
    shutil.rmtree(BOX, ignore_errors=True)
print(f"codex: {codex.parent.parent.parent.name}")
print(f"\n{len(cases) - fails}/{len(cases)} passed")
sys.exit(1 if fails else 0)
