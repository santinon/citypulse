#!/bin/sh
# Black-box tests for .githooks. Run from the repository root: sh tests/policy/test_githooks.sh
# Uses a temporary folder, a temporary index and dangling commits: the real index, branches
# and remote are untouched (the dangling objects are removed by git gc later).
set -u
cd "$(git rev-parse --show-toplevel)" || exit 1
. "${GUARDS_SH:-.githooks/guards.sh}"  # GUARDS_SH: test a proposed copy (section 1 only)
tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT
pass=0; fail=0
check() {  # label expected actual
    if [ "$2" = "$3" ]; then pass=$((pass + 1)); echo "OK   $1"
    else fail=$((fail + 1)); echo "FAIL $1 (want [$2] got [$3])"; fi
}

# 1. private_paths classification (positive and negative cases)
input='docs/internal/PROJECT_BRIEF.md
docs/data-source-audit.md
.firecrawl/npj_sg_2026-10-13.md
.collab/HANDOFF.md
.agent-policy/local_roots.txt
.agent-policy/guard.py
.agents/WORKLOG.md
.tmp/x.txt
.claude/settings.local.json
.claude/settings.json
.claude/hooks/guard_writes.py
.env
backend/.env
.env.local
.env.example
backend/.env.example
README.md
src/environment.py
docs/internal.md
.tmp/.env.example
docs/internal/.env.example
.firecrawl/site/.env.example
.collab/.env.example'
want='docs/internal/PROJECT_BRIEF.md
.firecrawl/npj_sg_2026-10-13.md
.collab/HANDOFF.md
.agent-policy/local_roots.txt
.agents/WORKLOG.md
.tmp/x.txt
.claude/settings.local.json
.claude/hooks/guard_writes.py
.env
backend/.env
.env.local
.tmp/.env.example
docs/internal/.env.example
.firecrawl/site/.env.example
.collab/.env.example'
check "private_paths classification" "$want" "$(printf '%s\n' "$input" | private_paths)"

# 2. commit-msg
m="$tmp/msg.txt"
msg() { printf "$1" > "$m"; sh .githooks/commit-msg "$m" 2>/dev/null; echo $?; }
check "commit-msg: clean message" 0 "$(msg 'feat: x\n\nBody.\n\nCloses #2\n')"
check "commit-msg: Claude Opus trailer" 1 "$(msg 'feat: x\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n')"
check "commit-msg: Codex trailer, lowercase" 1 "$(msg 'feat: x\n\nco-authored-by: Codex <codex@openai.com>\n')"
check "commit-msg: Claude-Session trailer" 1 "$(msg 'feat: x\n\nClaude-Session: https://claude.ai/code/session_x\n')"
check "commit-msg: Generated with Claude Code" 1 "$(msg 'feat: x\n\nGenerated with [Claude Code](https://claude.com/claude-code)\n')"
check "commit-msg: robot emoji + Generated with" 1 "$(msg 'feat: x\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n')"
check "commit-msg: prose quoting the trailer" 0 "$(msg 'chore: x\n\n- AI trailers: Co-authored-by lines, Claude-Session lines and\n  \"Generated with Claude Code\"; a human co-author is allowed.\n')"
check "commit-msg: prose mid-line Co-Authored-By mention" 0 "$(msg 'docs: refuse Co-Authored-By: Claude Opus trailers\n')"
check "commit-msg: human co-author named Claude" 0 "$(msg 'feat: x\n\nCo-authored-by: Claude Dupont <claude.dupont@example.org>\n')"
check "commit-msg: git comment lines ignored" 0 "$(msg 'feat: x\n# Co-Authored-By: Claude Code\n')"
check "commit-msg: mentions CLAUDE.md file" 0 "$(msg 'docs: CLAUDE.md imports AGENTS.md\n')"

# 3. pre-commit on a temporary index (index entries only, no file is written in the worktree)
GIT_INDEX_FILE="$tmp/test.index"; export GIT_INDEX_FILE
git read-tree HEAD
sh .githooks/pre-commit 2>/dev/null; check "pre-commit: clean index" 0 $?
blob=$(printf 'probe\n' | git hash-object -w --stdin)
git update-index --add --cacheinfo "100644,$blob,.tmp/hook_probe_ok.txt"
sh .githooks/pre-commit 2>/dev/null; check "pre-commit: .tmp file added to the index" 1 $?
private_tree=$(git write-tree)
git read-tree HEAD
clean_tree=$(git write-tree)
unset GIT_INDEX_FILE

# 4. pre-push on dangling commits (no ref is created)
head=$(git rev-parse HEAD)
zero=0000000000000000000000000000000000000000
c_private=$(git commit-tree "$private_tree" -p "$head" -m "test: private file")
c_trailer=$(printf 'test: trailer\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n' | git commit-tree "$clean_tree" -p "$head")
c_clean=$(git commit-tree "$clean_tree" -p "$head" -m "test: clean")
push() { printf 'refs/heads/zz %s refs/heads/zz %s\n' "$1" "$2" | sh .githooks/pre-push origin url 2>/dev/null; echo $?; }
check "pre-push: new branch, private file" 1 "$(push "$c_private" "$zero")"
check "pre-push: new branch, AI trailer" 1 "$(push "$c_trailer" "$zero")"
check "pre-push: new branch, clean commit" 0 "$(push "$c_clean" "$zero")"
check "pre-push: known remote tip, private file" 1 "$(push "$c_private" "$head")"
# Multi-file commit where the private path is NOT first in sorted order (regression test:
# NUL-separated names stored in a shell variable lose their separators and get glued together).
GIT_INDEX_FILE="$tmp/test.index"; export GIT_INDEX_FILE
git read-tree HEAD
git update-index --add --cacheinfo "100644,$blob,aaa_public_probe.txt"
git update-index --add --cacheinfo "100644,$blob,docs/internal/zz_probe.md"
sh .githooks/pre-commit 2>/dev/null; check "pre-commit: multi-file index, private file not first" 1 $?
multi_tree=$(git write-tree)
unset GIT_INDEX_FILE
c_multi=$(git commit-tree "$multi_tree" -p "$head" -m "test: public file then private file")
check "pre-push: multi-file commit, private file not first" 1 "$(push "$c_multi" "$zero")"
check "pre-push: branch deletion is ignored" 0 "$(push "$zero" "$head")"

echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
