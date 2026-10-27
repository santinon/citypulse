#!/bin/sh
# Black-box tests for .githooks. Run from the repository root: sh tests/policy/test_githooks.sh
# Everything happens in a disposable repository inside a temporary folder, with copies of the
# hooks: the real repository (index, objects, branches, remote) is never written.
# GITHOOKS_DIR: test a proposed hooks folder instead of .githooks.
set -u
pass=0; fail=0
check() {  # label expected actual
    if [ "$2" = "$3" ]; then pass=$((pass + 1)); echo "OK   $1"
    else fail=$((fail + 1)); echo "FAIL $1 (want [$2] got [$3])"; fi
}
die() {  # a fixture could not be built: stop, never count a hook result built on it
    echo "FAIL fixture setup: $1"; echo "$pass passed, $((fail + 1)) failed"; exit 1
}
# Exit code of a hook run, followed by the refusal reasons found in its stderr, so that a
# refusal caused by a broken fixture (such as an unknown commit) is not taken for the
# refusal under test.
result() {  # exit_code stderr_file
    r=$1
    grep -q -e 'refusing to commit private files' -e '^  private files:' "$2" && r="$r private"
    grep -q -e 'refusing AI attribution' -e '^  AI attribution in commit messages:' "$2" && r="$r trailer"
    grep -q -e 'could not list the commits' "$2" && r="$r cannot-list"
    printf '%s' "$r"
}

root=$(git rev-parse --show-toplevel) || exit 1
hooks="${GITHOOKS_DIR:-$root/.githooks}"
policy="$(dirname "$hooks")/.agent-policy/privacy"
tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT
repo="$tmp/repo"; err="$tmp/stderr.txt"; m="$tmp/msg.txt"

git init -q "$repo" || die "git init"
mkdir "$repo/.githooks" || die "hooks folder"
for f in guards.sh commit-msg pre-commit pre-push; do
    cp "$hooks/$f" "$repo/.githooks/$f" || die "copy $f"
done
mkdir -p "$repo/.agent-policy/privacy" || die "privacy folder"
for f in scan.py patterns.json allow.json; do
    cp "$policy/$f" "$repo/.agent-policy/privacy/$f" || die "copy privacy $f"
done
AGENT_PRIVACY_PRIVATE="$tmp/private.txt"
export AGENT_PRIVACY_PRIVATE
printf '%s\n' 'legacy-privacy-fixture-sentinel' > "$AGENT_PRIVACY_PRIVATE" || die "private policy"
cd "$repo" || die "cd into the temporary repository"
GIT_AUTHOR_NAME=policy-test; GIT_AUTHOR_EMAIL=policy-test@example.invalid
GIT_COMMITTER_NAME=$GIT_AUTHOR_NAME; GIT_COMMITTER_EMAIL=$GIT_AUTHOR_EMAIL
export GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
. .githooks/guards.sh

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
msg() { printf "$1" > "$m" || die "write message"; sh .githooks/commit-msg "$m" 2>"$err"; result $? "$err"; }
check "commit-msg: clean message" 0 "$(msg 'feat: x\n\nBody.\n\nCloses #2\n')"
check "commit-msg: Claude Opus trailer" "1 trailer" "$(msg 'feat: x\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n')"
check "commit-msg: Codex trailer, lowercase" "1 trailer" "$(msg 'feat: x\n\nco-authored-by: Codex <codex@openai.com>\n')"
check "commit-msg: Claude-Session trailer" "1 trailer" "$(msg 'feat: x\n\nClaude-Session: https://claude.ai/code/session_x\n')"
check "commit-msg: Generated with Claude Code" "1 trailer" "$(msg 'feat: x\n\nGenerated with [Claude Code](https://claude.com/claude-code)\n')"
check "commit-msg: robot emoji + Generated with" "1 trailer" "$(msg 'feat: x\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n')"
check "commit-msg: prose quoting the trailer" 0 "$(msg 'chore: x\n\n- AI trailers: Co-authored-by lines, Claude-Session lines and\n  \"Generated with Claude Code\"; a human co-author is allowed.\n')"
check "commit-msg: prose mid-line Co-Authored-By mention" 0 "$(msg 'docs: refuse Co-Authored-By: Claude Opus trailers\n')"
check "commit-msg: human co-author named Claude" 0 "$(msg 'feat: x\n\nCo-authored-by: Claude Dupont <claude.dupont@example.org>\n')"
check "commit-msg: git comment lines ignored" 0 "$(msg 'feat: x\n# Co-Authored-By: Claude Code\n')"
check "commit-msg: mentions CLAUDE.md file" 0 "$(msg 'docs: CLAUDE.md imports AGENTS.md\n')"

# 3. pre-commit, on a clean base commit
blob=$(printf 'probe\n' | git hash-object -w --stdin) || die "hash-object"
git update-index --add --cacheinfo "100644,$blob,README.md" || die "base index"
base_tree=$(git write-tree) || die "base tree"
head=$(printf 'test: base\n' | git commit-tree "$base_tree") || die "base commit"
git update-ref HEAD "$head" || die "HEAD"
sh .githooks/pre-commit 2>"$err"; rc=$?
check "pre-commit: clean index" 0 "$(result $rc "$err")"
git update-index --add --cacheinfo "100644,$blob,.tmp/hook_probe_ok.txt" || die "index .tmp file"
sh .githooks/pre-commit 2>"$err"; rc=$?
check "pre-commit: .tmp file added to the index" "1 private" "$(result $rc "$err")"
private_tree=$(git write-tree) || die "private tree"
git read-tree "$head" || die "reset index"
clean_tree=$(git write-tree) || die "clean tree"

# 4. pre-push on commits that no ref points to
zero=0000000000000000000000000000000000000000
commit() {  # tree parent < message: prints the new commit id; non-zero if it was not built
    c=$(git commit-tree "$1" -p "$2") && git cat-file -e "$c^{commit}" && printf '%s' "$c"
}
push() { printf 'refs/heads/zz %s refs/heads/zz %s\n' "$1" "$2" | sh .githooks/pre-push origin url 2>"$err"; result $? "$err"; }
c_private=$(printf 'test: private file\n' | commit "$private_tree" "$head") || die "private commit"
c_trailer=$(printf 'test: trailer\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n' | commit "$clean_tree" "$head") || die "trailer commit"
c_clean=$(printf 'test: clean\n' | commit "$clean_tree" "$head") || die "clean commit"
check "pre-push: new branch, private file" "1 private" "$(push "$c_private" "$zero")"
check "pre-push: new branch, AI trailer" "1 trailer" "$(push "$c_trailer" "$zero")"
check "pre-push: new branch, clean commit" 0 "$(push "$c_clean" "$zero")"
check "pre-push: known remote tip, private file" "1 private" "$(push "$c_private" "$head")"
# Multi-file commit where the private path is NOT first in sorted order (regression test:
# NUL-separated names stored in a shell variable lose their separators and get glued together).
git update-index --add --cacheinfo "100644,$blob,aaa_public_probe.txt" || die "index public file"
git update-index --add --cacheinfo "100644,$blob,docs/internal/zz_probe.md" || die "index private file"
sh .githooks/pre-commit 2>"$err"; rc=$?
check "pre-commit: multi-file index, private file not first" "1 private" "$(result $rc "$err")"
multi_tree=$(git write-tree) || die "multi-file tree"
git read-tree "$head" || die "reset index"
c_multi=$(printf 'test: public file then private file\n' | commit "$multi_tree" "$head") || die "multi-file commit"
check "pre-push: multi-file commit, private file not first" "1 private" "$(push "$c_multi" "$zero")"
check "pre-push: branch deletion is ignored" 0 "$(push "$zero" "$head")"
# Control of the reason check: a commit that does not exist is refused, but for that reason.
check "control: unknown commit refused as cannot-list, not as private" "1 cannot-list" \
    "$(push 1111111111111111111111111111111111111111 "$zero")"

echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
