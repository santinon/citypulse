#!/usr/bin/env python3
"""Shared pre-tool guard for every coding agent in this repository.

Claude Code and Codex both call this script from their PreToolUse hooks, so the
same rules apply to both agents:

1. File writes stay inside this repository, plus any extra absolute roots
   listed one per line in the gitignored `.agent-policy/local_roots.txt`.
2. No agent edits agent permissions, hooks, the git hooks or git internals:
   `.claude/settings*`, `.claude/hooks/`, `.codex/`, `.agent-policy/`,
   `.githooks/`, `.git`. Humans change these; agents propose changes in `.tmp/`.
3. Shell commands are refused when they would write to those paths, or when
   they are destructive or outward-facing (merge, GitHub API calls, force
   push, hard reset, global installs, launching desktop applications...).

Input: the hook JSON on stdin (Claude Code: Edit/Write/NotebookEdit/Bash/
PowerShell; Codex: apply_patch/Bash). Exit 0 allows, exit 2 blocks with the
reason on stderr. Unreadable input blocks (fail closed).

Limits: shell checks read the command text. A script that writes files itself
is not detected. This makes agent actions visible and reviewable; it is not a
sandbox.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCAL_ROOTS_FILE = REPO / ".agent-policy" / "local_roots.txt"

# Repository-relative prefixes no agent may write.
PROTECTED_PREFIXES = (
    ".claude/settings",
    ".claude/hooks/",
    ".codex/",
    ".agent-policy/",
    ".githooks/",
    ".git/",
)
# The same configuration folders inside any other project under an extra root.
PROTECTED_SEGMENTS = ("/.claude/settings", "/.claude/hooks/", "/.codex/", "/.git/")

FILE_TOOLS = {"edit", "write", "notebookedit", "multiedit"}
PATCH_TOOLS = {"apply_patch"}
SHELL_TOOLS = {"bash", "powershell", "shell"}

PATCH_HEADER = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.M)

PROTECTED_REF = re.compile(
    r"\.claude[/\\](?:settings|hooks)|\.codex(?:[/\\]|$|\s)|\.agent-policy|\.githooks|(?:^|[\s\"'/\\])\.git[/\\]",
    re.I,
)
WRITE_HINT = re.compile(
    r">|\b(?:tee|cp|mv|rm|rmdir|touch|truncate|chmod|ln|dd)\b|\bsed\s+-i|"
    r"\b(?:Set-Content|Add-Content|Out-File|Remove-Item|Copy-Item|Move-Item|New-Item|Rename-Item)\b|"
    r"\bgit\s+(?:add|rm|mv|checkout|restore|apply|stash|reset|clean|update-index)\b|"
    r"\b(?:python3?|py|node|perl|ruby|powershell|pwsh|sh|bash)\b",
    re.I,
)
# A protected path; inside it, a .py or .sh extension (guard.py, guards.sh) is no interpreter.
PROTECTED_TOKEN = re.compile(r"[\w.:/\\-]*(?:" + PROTECTED_REF.pattern + r")[\w.:/\\-]*", re.I)
# Destructive or outward-facing commands, refused wherever they appear in a command.
FORBIDDEN_COMMANDS = [
    (r"\bgh\s+pr\s+merge\b", "merging is done by a human"),
    (r"\bgh\s+api\b", "GitHub API calls are done by a human (use gh pr/issue view to read)"),
    (r"\bgh\s+repo\s+(?:delete|edit|rename|archive)\b", "repository settings are changed by a human"),
    (r"\bgh\s+(?:secret|release|workflow)\b", "secrets, releases and workflows are handled by a human"),
    (r"\bgh\s+auth\s+token\b", "credentials must not be read"),
    (r"\bgit\s+credential\b", "credentials must not be read"),
    (r"\bgit\s+push\b[^|;&]*\s(?:--force\S*|-f|--mirror|--all|--delete|-d|--prune)(?:\s|$)", "destructive push"),
    (r"\bgit\s+push\b[^|;&]*\s\+\S", "force push via +refspec"),
    (r"\bgit\s+push\b[^|;&]*\s[\"']?:\S", "deleting a remote branch (empty source refspec)"),
    (r"\bgit\s+reset\s+--hard\b", "hard reset discards work"),
    (r"\bgit\s+clean\b", "git clean deletes untracked files"),
    (r"\bgit\s+branch\s+(?-i:-D)\b", "forced branch deletion"),
    (r"\bgit\b[^|;&]*\s--no-verify\b", "--no-verify skips the git hooks"),
    (r"\bgit\s+commit\b[^|;&]*\s-[A-Za-z]*n[A-Za-z]*(?:\s|$)", "commit -n skips the git hooks"),
    (r"\bgit\b[^|;&]*\s--git-dir\b|\bGIT_DIR=", "redirecting git internals"),
    # curl options are case-sensitive: -d data vs -D dump-header, -F form vs -f fail, -T upload.
    (r"\bcurl\b[^|;&]*\s(?-i:-d|--data\S*|-F|--form\S*|-T|--upload-file|--json)(?:\s|=|$)", "sending data out needs the user"),
    (r"\bcurl\b[^|;&]*\s(?:(?-i:-X)\s*|--request[\s=]+)(?:POST|PUT|PATCH|DELETE)\b", "sending data out needs the user"),
    (r"\bwget\b[^|;&]*\s--(?:post-data|post-file|body-data|body-file|method)\b", "sending data out needs the user"),
    (r"\b(?:Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b[^|;&]*\s-(?:Method\s+(?:Post|Put|Patch|Delete)|Body|InFile)\b", "sending data out needs the user"),
    (r"\b(?:curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^;&]*\|\s*(?:sh|bash|zsh|node|iex|Invoke-Expression)\b", "download-and-execute needs the user"),
    (r"\bdocker\s+(?:volume\s+rm|system\s+prune|\S+\s+prune)\b", "deletes Docker data"),
    (r"\bdocker\s+compose\b[^|;&]*\s(?:-v|--volumes)(?:\s|$)", "deletes Docker volumes"),
    (r"\bnpm\s+(?:install|i|add)\b[^|;&]*\s(?:-g|--global)\b", "global installs are done by a human"),
    (r"\buv\s+tool\s+install\b|\bpip3?\s+install\b|\bwinget\b|\bchoco\b|\bInstall-Module\b", "installs are done by a human"),
    (r"\bStart-Process\b|\bInvoke-Item\b|(?:^|[;&|]\s*)(?:start|explorer(?:\.exe)?)\s", "launching applications needs the user"),
]


def norm(path: str) -> str:
    p = path.strip().strip('"').replace("\\", "/")
    if len(p) > 2 and p[0] == "/" and p[2] == "/" and p[1].isalpha():  # /c/Users -> c:/Users
        p = f"{p[1]}:{p[2:]}"
    return os.path.normpath(p).replace("\\", "/").lower()


def block(reason: str) -> None:
    print(f"BLOCKED by .agent-policy/guard.py: {reason}", file=sys.stderr)
    sys.exit(2)


def allowed_roots() -> list[str]:
    roots = [norm(str(REPO)).rstrip("/") + "/"]
    if LOCAL_ROOTS_FILE.is_file():
        for line in LOCAL_ROOTS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                roots.append(norm(line).rstrip("/") + "/")
    return roots


def check_path(raw: str, cwd: str) -> None:
    target = raw if os.path.isabs(raw) or raw.startswith("/") else os.path.join(cwd, raw)
    path = norm(target)
    repo = norm(str(REPO)).rstrip("/") + "/"
    if not any(path.startswith(root) or path + "/" == root for root in allowed_roots()):
        block(f"{raw} is outside the allowed write roots; ask the user first.")
    rel = path[len(repo):] if path.startswith(repo) else None
    if rel is not None and (rel == ".git" or rel.startswith(PROTECTED_PREFIXES)):
        block(f"{raw} is protected; propose the change in .tmp/ and let the user apply it.")
    if rel is None and any(seg in path for seg in PROTECTED_SEGMENTS):
        block(f"{raw} is another project's agent or git configuration.")


def check_shell(command: str) -> None:
    for pattern, reason in FORBIDDEN_COMMANDS:
        if re.search(pattern, command, re.I):
            block(f"`{command[:120]}`: {reason}.")
    # Downloaded content piped into a Python that runs its stdin as code (no -c, no script file).
    # `curl ... | python -c "...json..."` only parses data and stays allowed.
    if re.search(r"\b(?:curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b", command, re.I):
        for stage in command.split("|")[1:]:
            words = stage.split()
            while words and "=" in words[0]:  # skip VAR=value prefixes
                words = words[1:]
            if words and re.fullmatch(r"(?:python3?|py)(?:\.exe)?", words[0], re.I):
                rest = " ".join(words[1:])
                if not re.search(r"(?:^|\s)-c\b|\.py\b", rest):
                    block(f"`{command[:120]}`: download-and-execute needs the user.")
    # Any command segment touching core.hooksPath, except a plain read, could disable the git hooks.
    for segment in re.split(r"[;&|\n]", command):
        if re.search(r"core\.hookspath", segment, re.I) and not re.search(r"\bconfig\s+--get\b", segment, re.I):
            block(f"`{segment.strip()[:120]}`: changing core.hooksPath can disable the git hooks.")
    hints = PROTECTED_TOKEN.sub(lambda m: re.sub(r"\.(?:py|sh)\b", "", m.group(), flags=re.I), command)
    if PROTECTED_REF.search(command) and WRITE_HINT.search(hints):
        block(f"`{command[:120]}` may write to protected agent or git configuration.")


def main() -> None:
    try:
        data = json.load(sys.stdin)
        tool = str(data.get("tool_name") or "").lower()
        if not tool:
            raise ValueError("no tool_name")
        tool_input = data.get("tool_input") or {}
        cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or str(REPO)
        command = tool_input.get("command", "")
        if isinstance(command, list):
            command = "\n".join(str(part) for part in command)
        if not isinstance(command, str):
            raise TypeError("command is not text")
    except Exception as exc:  # fail closed
        block(f"could not parse hook input ({exc!r})")
        return

    if tool in FILE_TOOLS:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not raw:
            block("no target path in tool input")
        check_path(raw, cwd)
    elif tool in PATCH_TOOLS or (tool in SHELL_TOOLS and "*** Begin Patch" in command):
        paths = PATCH_HEADER.findall(command)
        if not paths:
            block("patch without file headers")
        for raw in paths:
            check_path(raw, cwd)
    elif tool in SHELL_TOOLS:
        if not command.strip():
            block("no command in shell tool input")
        check_shell(command)
    sys.exit(0)


if __name__ == "__main__":
    main()
