# Shared rules for the git hooks in this folder. Sourced, not executed.
#
# Two-tier design: .gitignore keeps private files out of `git add .`, and these
# hooks fail loudly if one is committed anyway (for example with `git add -f`)
# or if a commit message carries an AI attribution trailer. The pre-push hook
# re-checks everything about to leave the machine, so `git commit --no-verify`
# does not get past it.
#
# Enable once per clone:  git config core.hooksPath .githooks

# Paths that must never enter the history: internal planning notes, the local
# agent coordination folder (.collab/, formerly .agents/), scratch files,
# machine-local agent settings, hooks and extra write roots, and environment
# files (except the committed template).
PRIVATE_RE='^docs/internal/|^\.collab/|^\.agents/|^\.tmp/|^\.claude/settings\.local\.json$|^\.claude/hooks/|^\.agent-policy/local_roots\.txt$|(^|/)\.env$|(^|/)\.env\.[^/]*$'
PUBLIC_EXCEPTION_RE='(^|/)\.env\.example$'

# AI attribution in commit messages (project rule: none in commits or code).
# A human co-author is allowed; only known AI tools and their trailers are refused.
# Patterns are anchored at the start of a line so that prose quoting a trailer
# (for example a commit that documents this rule) is not refused.
AI_TRAILER_RE='^co-authored-by:.*(anthropic|openai|codex|chatgpt|copilot|gemini|claude (opus|sonnet|haiku|fable|code))|^claude-session:|^(🤖)?[[:space:]]*generated with \[?claude code'

# stdin: one repository-relative path per line; stdout: the private ones.
private_paths() {
    while IFS= read -r path; do
        [ -z "$path" ] && continue
        printf '%s\n' "$path" | grep -Eq "$PUBLIC_EXCEPTION_RE" && continue
        printf '%s\n' "$path" | grep -Eq "$PRIVATE_RE" && printf '%s\n' "$path"
    done
    return 0
}

# stdin: a commit message; stdout: the offending lines.
ai_trailers() {
    grep -v '^#' | grep -Ei "$AI_TRAILER_RE" || true
}
