#!/usr/bin/env python3
"""Privacy checks. Python 3.12 stdlib. Exit 0: checked, 1: findings, 2: incomplete."""
import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
OID = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"


class Incomplete(Exception):
    pass


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold()


def decode(data):
    if b"\0" in data:
        raise Incomplete()
    return data.decode("utf-8-sig", errors="strict")


def run(repo, *args, ok=(0,)):
    result = subprocess.run(
        ["git", "--no-pager", "--no-replace-objects", "--literal-pathspecs", *args],
        cwd=repo, input=b"", capture_output=True, timeout=120,
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat"),
    )
    if result.returncode not in ok:
        raise Incomplete()
    return result


def git(repo, *args):
    return run(repo, *args).stdout


class Rules:
    def __init__(self, patterns, terms, allow):
        if not isinstance(patterns, list) or not patterns or not isinstance(allow, list):
            raise Incomplete()
        self.patterns = []
        ids = set()
        for record in patterns:
            name = record["id"]
            if not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name in ids:
                raise Incomplete()
            ids.add(name)
            regex = re.compile(record["regex"], re.I)
            for key in ("positive", "negative"):
                parts = record[key]
                if not isinstance(parts, list) or not parts:
                    raise Incomplete()
                if not all(isinstance(part, str) for part in parts):
                    raise Incomplete()
            if not regex.search("".join(record["positive"])):
                raise Incomplete()
            if regex.search("".join(record["negative"])):
                raise Incomplete()
            self.patterns.append((name, regex))
        self.terms = [normalize(term) for term in terms]
        if any(not term for term in self.terms):
            raise Incomplete()
        for entry in allow:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"rule", "text", "paths", "why"}
                or entry["rule"] not in ids
                or not isinstance(entry["text"], str) or not entry["text"]
                or not isinstance(entry["why"], str) or not entry["why"].strip()
                or not isinstance(entry["paths"], list) or not entry["paths"]
                or not all(isinstance(path, str) and path for path in entry["paths"])
            ):
                raise Incomplete()
        self.allow = allow
        self.hits = 0

    def sensitive(self, text):
        return any(term in normalize(text) for term in self.terms) or any(
            regex.search(text) for _, regex in self.patterns
        )

    def report(self, source, number, rule):
        self.hits += 1
        source = "source [MASKED]" if self.sensitive(source) else source
        print(f"source={json.dumps(source, ensure_ascii=True)} line={number} rule={rule}")

    def text(self, text, source, path="", start=1):
        if "\0" in text:
            raise Incomplete()
        for number, line in enumerate(text.split("\n"), start):
            if any(term in normalize(line) for term in self.terms):
                self.report(source, number, "private-term")
            for name, regex in self.patterns:
                for match in regex.finditer(line):
                    if not any(
                        entry["rule"] == name and entry["text"] == match.group()
                        and path in entry["paths"] for entry in self.allow
                    ):
                        self.report(source, number, name)
                        break

    def data(self, data, source, path=""):
        try:
            self.text(decode(data), source, path)
        except (UnicodeError, Incomplete):
            self.report(source, 1, "cannot-scan")
            raise Incomplete() from None

    def finish(self):
        if self.hits:
            print("privacy: remove the finding or ask the owner to review a public exception.")
        return int(bool(self.hits))


def load(public_only=False):
    patterns = json.loads((HERE / "patterns.json").read_text(encoding="utf-8"))
    allow = json.loads((HERE / "allow.json").read_text(encoding="utf-8"))
    terms = []
    if not public_only:
        try:
            override = os.environ.get("AGENT_PRIVACY_PRIVATE")
            if override is None:
                local = os.environ.get("LOCALAPPDATA")
                private = Path(local) / "AgentPrivacy" / "private.txt" if local else None
                if private is None or not private.exists():
                    # The private list is optional: without one, only the public rules apply.
                    return Rules(patterns, terms, allow)
            elif override:
                private = Path(override).expanduser()
            else:
                raise Incomplete()
            private = private.resolve()
            # Reject normal repositories, linked worktrees and bare repositories.
            if any(
                (parent / ".git").exists()
                or ((parent / "HEAD").is_file() and (parent / "objects").is_dir())
                for parent in private.parents
            ):
                raise Incomplete()
            for line in decode(private.read_bytes()).splitlines():
                term = line.strip()
                if term and not term.startswith("#"):
                    terms.append(term)
        except Exception:
            print(
                "privacy: private list unreadable or inside a repository. "
                "Owner: fix or remove %LOCALAPPDATA%/AgentPrivacy/private.txt (optional; "
                "outside all repositories, one literal term per line), or AGENT_PRIVACY_PRIVATE."
            )
            raise Incomplete() from None
    return Rules(patterns, terms, allow)


class Scanner:
    def __init__(self, rules):
        self.rules = rules
        self.repo = Path.cwd()

    def changes(self, commit=None, parent=None):
        if commit is None:
            prefix = ["diff", "--cached"]
        else:
            prefix = ["diff-tree", "--no-commit-id", "-r", "--root"]
            prefix += [parent, commit] if parent else [commit]
        options = [
            "--no-renames", "--no-ext-diff", "--no-textconv", "--no-color",
            "--no-relative", "--ignore-submodules=none",
        ]
        rows = git(self.repo, *prefix, "--raw", "-z", "--no-abbrev", *options).split(b"\0")
        index = 0
        while index < len(rows) and rows[index]:
            meta = rows[index].split()
            if len(meta) != 5 or not meta[0].startswith(b":") or index + 1 >= len(rows):
                raise Incomplete()
            _, mode, _, oid, status = meta
            path = decode(rows[index + 1])
            index += 2
            self.rules.text(path, f"filename #{index // 2}", "@filename")
            if status == b"D":
                continue
            source = path if commit is None else f"{path} (commit {commit[:7]})"
            if mode not in (b"100644", b"100755") or not re.fullmatch(OID, oid.decode("ascii")):
                self.rules.report(source, 1, "cannot-scan")
                raise Incomplete()
            blob = git(self.repo, "cat-file", "blob", oid.decode("ascii"))
            try:
                decode(blob)
                if blob.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    raise Incomplete()
            except (UnicodeError, Incomplete):
                self.rules.report(source, 1, "cannot-scan")
                raise Incomplete() from None
            # Validate the entire new blob above, but match only added lines.
            patch = git(
                self.repo, *prefix, "--patch", "--unified=0", "--text", *options, "--", path
            )
            number = None
            for line in patch.split(b"\n"):
                hunk = re.match(rb"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if hunk:
                    number = int(hunk[1])
                elif number is not None:
                    if line.startswith(b"+"):
                        self.rules.text(decode(line[1:]), source, path, number)
                        number += 1
                    elif line.startswith(b" "):
                        number += 1

    def history(self, revisions):
        for oid in decode(git(self.repo, "rev-list", *revisions)).splitlines():
            if not re.fullmatch(OID, oid):
                raise Incomplete()
            parts = git(self.repo, "cat-file", "commit", oid).split(b"\n\n", 1)
            if len(parts) != 2:
                raise Incomplete()
            self.rules.data(parts[1], f"commit message {oid[:7]}", "@commit-message")
            parents = decode(git(self.repo, "rev-list", "--parents", "-n", "1", oid)).split()[1:]
            # Scan merge resolutions against every parent.
            for parent in parents or [None]:
                self.changes(oid, parent)

    def pushed(self, tip, base):
        if not re.fullmatch(OID, tip) or not re.fullmatch(OID, base):
            raise Incomplete()
        if set(tip) == {"0"}:
            return
        known = run(self.repo, "cat-file", "-e", base + "^{commit}", ok=(0, 1, 128))
        # New/unknown bases: scan full ancestry, including remote-tracking history.
        revisions = [base + ".." + tip] if known.returncode == 0 else [tip]
        self.history(revisions)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Do not reproduce untrusted arguments.
        raise Incomplete()


def main():
    parser = Parser(description=__doc__)
    parser.add_argument("--public-only", action="store_true")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--staged", action="store_true")
    modes.add_argument("--message")
    modes.add_argument("--range")
    modes.add_argument("--push-tip")
    parser.add_argument("--push-base")
    args = parser.parse_args()
    if bool(args.push_tip) != bool(args.push_base):
        raise Incomplete()
    rules = load(args.public_only)
    scanner = Scanner(rules)
    if args.staged:
        scanner.changes()
    elif args.message is not None:
        rules.data(Path(args.message).read_bytes(), "commit message", "@commit-message")
    elif args.range is not None:
        # CI supplies full object IDs, never arbitrary revision expressions.
        if not re.fullmatch(OID + r"(?:\.\." + OID + r")?", args.range):
            raise Incomplete()
        scanner.history([args.range])
    elif args.push_tip:
        scanner.pushed(args.push_tip, args.push_base)
    return rules.finish()


def entry(function=main):
    try:
        return function()
    except (Exception, KeyboardInterrupt):
        print("privacy: cannot scan; check policy, input, files and command availability. No text shown.")
        return 2


if __name__ == "__main__":
    sys.exit(entry())
