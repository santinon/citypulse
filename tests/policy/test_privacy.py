"""Stdlib privacy controls. All writes stay in disposable tempfile directories."""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / ".agent-policy" / "privacy"
TERM = "Synthetic" + "PrivateSentinel"
ZERO = "0" * 40

spec = importlib.util.spec_from_file_location("privacy_under_test", POLICY / "scan.py")
if spec is None or spec.loader is None:
    print("FAIL privacy module unavailable")
    sys.exit(1)
privacy = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(privacy)
except Exception:
    print("FAIL privacy module could not load")
    sys.exit(1)


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="privacy-test-")
        self.addCleanup(self.temp.cleanup)
        self.box = Path(self.temp.name)
        self.repo = self.box / "repo"
        self.repo.mkdir()
        self.private = self.box / "private.txt"
        self.private.write_text("# temporary test policy\n" + TERM + "\n", encoding="utf-8")
        self.env = dict(
            os.environ,
            AGENT_PRIVACY_PRIVATE=str(self.private),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="privacy-test",
            GIT_AUTHOR_EMAIL="privacy-test" + "@" + "example.invalid",
            GIT_COMMITTER_NAME="privacy-test",
            GIT_COMMITTER_EMAIL="privacy-test" + "@" + "example.invalid",
            GIT_TERMINAL_PROMPT="0",
        )
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.git("init", "-q")
        shutil.copytree(POLICY, self.repo / ".agent-policy" / "privacy")
        shutil.copytree(ROOT / ".githooks", self.repo / ".githooks")
        self.scanner = self.repo / ".agent-policy" / "privacy" / "scan.py"
        self.patterns = json.loads((POLICY / "patterns.json").read_text(encoding="utf-8"))

        self.stage("README.md", "safe\n")
        self.base = self.snapshot()

    def process(self, argv, data=None, env=None):
        return subprocess.run(
            argv,
            cwd=self.repo,
            env=self.env if env is None else env,
            input=data,
            capture_output=True,
            timeout=120,
        )

    def git(self, *args, data=None):
        result = self.process(["git", *args], data)
        self.assertEqual(result.returncode, 0)
        return result.stdout.decode("utf-8").strip()

    def stage(self, name, content):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        self.git("add", "--", name)

    def snapshot(self, message="test: fixture\n", parents=None):
        tree = self.git("write-tree")
        if parents is None:
            current = self.process(["git", "rev-parse", "--verify", "HEAD"])
            parents = (
                [current.stdout.decode("ascii").strip()]
                if current.returncode == 0 else []
            )
        args = ["commit-tree", tree]
        for parent in parents:
            args += ["-p", parent]
        oid = self.git(*args, data=message.encode("utf-8"))
        self.git("update-ref", "HEAD", oid)
        return oid

    def scan(self, *args, env=None):
        return self.process(
            [sys.executable, "-I", "-B", str(self.scanner), *args],
            env=env,
        )

    def hook(self, name, data=None, message=None):
        args = ["sh", str(self.repo / ".githooks" / name)]
        if message is not None:
            filename = self.box / "message.txt"
            filename.write_text(message, encoding="utf-8")
            args.append(str(filename))
        return self.process(args, data)

    def push_hook(self, tip, base=None):
        remote = ZERO if base is None else base
        data = f"refs/heads/topic {tip} refs/heads/topic {remote}\n".encode("ascii")
        return self.hook("pre-push", data)

    def quiet(self, function):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            value = function()
        return value, output.getvalue()

    def assert_no_leak(self, result):
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        self.assertNotIn(TERM.casefold(), output.casefold())
        self.assertNotIn("Traceback", output)

    def test_every_public_pattern_positive_then_negative(self):
        for record in self.patterns:
            for key, expected in (("positive", 1), ("negative", 0)):
                with self.subTest(rule=record["id"], control=key):
                    rules = privacy.Rules([record], [], [])
                    self.quiet(lambda: rules.text("".join(record[key]), "control"))
                    self.assertEqual(bool(rules.hits), bool(expected))

    def test_public_variants(self):
        cases = {
            "windows-home": [
                "C:" + chr(92) + "Users" + chr(92) + "fixture",
                "/c/" + "Users/fixture",
            ],
            "posix-home": ["/Users/" + "fixture/file"],
            "github-token": [
                "github_pat_" + "A" * 30,
                "ghs_" + "A" * 30,
            ],
            "openai-token": [
                "sk-proj-" + "A" * 30,
                "sk-svcacct-" + "A" * 30,
            ],
            "aws-key": ["ASIA" + "A" * 16],
            "private-key": [
                "-----BEGIN " + "RSA PRIVATE KEY-----",
                "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
            ],
            "slack-token": ["xoxp-" + "A" * 30],
        }
        for name, values in cases.items():
            record = next(item for item in self.patterns if item["id"] == name)
            for value in values:
                rules = privacy.Rules([record], [], [])
                self.quiet(lambda: rules.text(value, "control"))
                self.assertGreater(rules.hits, 0)

    def test_private_normalization_literals_comments_and_negative(self):
        literal = "Alpha" + "Private.[x]"
        self.private.write_text(
            "# ignored comment\n\n" + literal + "\nStraße\n",
            encoding="utf-8",
        )
        for value in (literal.swapcase(), "\uff21" + literal[1:], "STRASSE"):
            rules = privacy.load()
            self.quiet(lambda: rules.text(value, "control"))
            self.assertEqual(rules.hits, 1)
        rules = privacy.load()
        self.quiet(lambda: rules.text("AlphaPublicZx", "control"))
        self.assertEqual(rules.hits, 0)

    def test_allowlist_exact_path_and_private_cannot_be_allowed(self):
        record = next(item for item in self.patterns if item["id"] == "email")
        value = "".join(record["positive"])
        entry = {
            "rule": "email",
            "text": value,
            "paths": ["approved.txt"],
            "why": "Synthetic exact control.",
        }
        for path, text, expected in (
            ("approved.txt", value, 0),
            ("other.txt", value, 1),
            ("approved.txt", "x" + value, 1),
            ("approved.txt", value + "x", 1),
        ):
            rules = privacy.Rules([record], [], [entry])
            self.quiet(lambda: rules.text(text, "control", path))
            self.assertEqual(bool(rules.hits), bool(expected))
        rules = privacy.Rules([record], [], [{**entry, "paths": ["*.txt"]}])
        self.quiet(lambda: rules.text(value, "control", "approved.txt"))
        self.assertEqual(rules.hits, 1)
        rules = privacy.Rules([record], [value], [entry])
        self.quiet(lambda: rules.text(value, "control", "approved.txt"))
        self.assertEqual(rules.hits, 1)
        with self.assertRaises(privacy.Incomplete):
            privacy.Rules(
                [record], [TERM], [{**entry, "rule": "private-term"}]
            )

    def test_allowlist_can_scan_its_own_public_text(self):
        rules = privacy.load(public_only=True)
        value = (POLICY / "allow.json").read_bytes()
        self.quiet(
            lambda: rules.data(
                value, "policy", ".agent-policy/privacy/allow.json"
            )
        )
        self.assertEqual(rules.hits, 0)

    def test_missing_unreadable_and_repository_private_lists(self):
        missing = dict(self.env, AGENT_PRIVACY_PRIVATE=str(self.box / "missing.txt"))
        result = self.scan("--staged", env=missing)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"AGENT_PRIVACY_PRIVATE", result.stdout)
        self.assert_no_leak(result)

        unreadable = dict(self.env, AGENT_PRIVACY_PRIVATE=str(self.box))
        self.assertEqual(self.scan("--staged", env=unreadable).returncode, 2)

        inside = self.repo / "private.txt"
        inside.write_text(TERM, encoding="utf-8")
        local = dict(self.env, AGENT_PRIVACY_PRIVATE=str(inside))
        self.assertEqual(self.scan("--staged", env=local).returncode, 2)

    def test_default_private_list_is_optional(self):
        local = self.box / "local"
        base = {k: v for k, v in self.env.items() if k != "AGENT_PRIVACY_PRIVATE"}
        env = dict(base, LOCALAPPDATA=str(local))
        message = self.box / "message.txt"
        message.write_text(TERM, encoding="utf-8")
        # no default list: public rules only, so the private term passes
        self.assertEqual(self.scan("--message", str(message), env=env).returncode, 0)
        email = next(item for item in self.patterns if item["id"] == "email")
        message.write_text("".join(email["positive"]), encoding="utf-8")
        self.assertEqual(self.scan("--message", str(message), env=env).returncode, 1)
        # with a default list, its terms apply
        (local / "AgentPrivacy").mkdir(parents=True)
        (local / "AgentPrivacy" / "private.txt").write_text(TERM + "\n", encoding="utf-8")
        message.write_text(TERM, encoding="utf-8")
        result = self.scan("--message", str(message), env=env)
        self.assertEqual(result.returncode, 1)
        self.assert_no_leak(result)
        # no LOCALAPPDATA at all (Linux, CI): public rules only
        self.assertEqual(self.scan("--message", str(message), env=base).returncode, 0)

    def test_bare_repository_private_list_is_refused(self):
        bare = self.box / "bare"
        self.git("init", "--bare", "-q", str(bare))
        inside = bare / "private.txt"
        inside.write_text(TERM, encoding="utf-8")
        env = dict(self.env, AGENT_PRIVACY_PRIVATE=str(inside))
        self.assertEqual(self.scan("--staged", env=env).returncode, 2)

    def test_public_only_works_without_private_policy_but_keeps_public_checks(self):
        missing = dict(self.env, AGENT_PRIVACY_PRIVATE=str(self.box / "missing.txt"))
        message = self.box / "message.txt"
        message.write_text(TERM, encoding="utf-8")
        self.assertEqual(
            self.scan("--public-only", "--message", str(message), env=missing).returncode,
            0,
        )
        email = next(item for item in self.patterns if item["id"] == "email")
        message.write_text("".join(email["positive"]), encoding="utf-8")
        self.assertEqual(
            self.scan("--public-only", "--message", str(message), env=missing).returncode,
            1,
        )

    def test_precommit_staged_content_and_line_number(self):
        self.stage("notes.txt", "safe\n" + TERM + "\n")
        # The worktree differs from the index; the staged secret must still block.
        (self.repo / "notes.txt").write_text("safe\n", encoding="utf-8")
        result = self.hook("pre-commit")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"line=2 rule=private-term", result.stdout)
        self.assert_no_leak(result)

        self.stage("notes.txt", "safe\n")
        self.assertEqual(self.hook("pre-commit").returncode, 0)

    def test_precommit_public_pattern_is_refused_without_disclosure(self):
        record = next(item for item in self.patterns if item["id"] == "github-token")
        value = "".join(record["positive"])
        self.stage("notes.txt", value + "\n")
        result = self.hook("pre-commit")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"rule=github-token", result.stdout)
        self.assertNotIn(value.encode("utf-8"), result.stdout + result.stderr)

    def test_literal_pathspec_filename(self):
        self.stage("data[1].txt", TERM + "\n")
        result = self.hook("pre-commit")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"rule=private-term", result.stdout)
        self.assert_no_leak(result)

    def test_precommit_scans_only_added_lines(self):
        self.stage("notes.txt", TERM + "\n")
        self.snapshot()
        self.stage("notes.txt", TERM + "\nsafe\n")
        self.assertEqual(self.hook("pre-commit").returncode, 0)

    def test_private_filename_and_source_are_masked(self):
        self.stage("folder/" + TERM + " file.txt", TERM + "\n")
        result = self.hook("pre-commit")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"rule=private-term", result.stdout)
        self.assert_no_leak(result)

    def test_binary_non_utf8_and_symlink_fail_closed(self):
        for content in (b"a\0b", b"\xff"):
            self.stage("data.bin", content)
            result = self.hook("pre-commit")
            self.assertEqual(result.returncode, 1)
            self.assertIn(b"rule=cannot-scan", result.stdout)
        self.git("rm", "--cached", "--", "data.bin")
        oid = self.git("hash-object", "-w", "--stdin", data=b"target")
        self.git("update-index", "--add", "--cacheinfo", f"120000,{oid},link.txt")
        result = self.hook("pre-commit")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"rule=cannot-scan", result.stdout)

    def test_commit_message_hook(self):
        result = self.hook("commit-msg", message="test: " + TERM + "\n")
        self.assertEqual(result.returncode, 1)
        self.assert_no_leak(result)
        record = next(item for item in self.patterns if item["id"] == "github-token")
        value = "".join(record["positive"])
        result = self.hook("commit-msg", message="test: " + value + "\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"rule=github-token", result.stdout)
        self.assertNotIn(value.encode("utf-8"), result.stdout + result.stderr)
        self.assertEqual(
            self.hook("commit-msg", message="test: safe\n\nRefs #1\n").returncode,
            0,
        )

    def test_prepush_finds_removed_secret_in_an_earlier_commit(self):
        self.stage("notes.txt", TERM + "\n")
        self.snapshot()
        self.stage("notes.txt", "safe\n")
        tip = self.snapshot()
        result = self.push_hook(tip, self.base)
        self.assertEqual(result.returncode, 1)
        self.assert_no_leak(result)

    def test_prepush_commit_messages_and_filenames(self):
        tip = self.snapshot("test: " + TERM + "\n")
        result = self.push_hook(tip, self.base)
        self.assertEqual(result.returncode, 1)
        self.assert_no_leak(result)

        self.git("update-ref", "HEAD", self.base)
        self.stage(TERM + ".txt", "safe\n")
        tip = self.snapshot()
        result = self.push_hook(tip, self.base)
        self.assertEqual(result.returncode, 1)
        self.assert_no_leak(result)

    def test_prepush_new_branch_clean_deletion_and_multiple_refs(self):
        self.git("update-ref", "refs/remotes/origin/main", self.base)
        tip = self.snapshot()
        self.assertEqual(self.push_hook(tip).returncode, 0)
        self.assertEqual(self.push_hook(ZERO, tip).returncode, 0)

        bad = self.snapshot("test: " + TERM + "\n")
        data = (
            f"refs/heads/one {tip} refs/heads/one {self.base}\n"
            f"refs/heads/two {bad} refs/heads/two {self.base}\n"
        ).encode("ascii")
        result = self.hook("pre-push", data)
        self.assertEqual(result.returncode, 1)
        self.assert_no_leak(result)

    def test_new_branch_scans_remote_tracking_ancestry_and_unknown_base(self):
        tip = self.snapshot("test: " + TERM + "\n")
        self.git("update-ref", "refs/remotes/other/topic", tip)
        for base in (ZERO, "1" * 40):
            result = self.push_hook(tip, base)
            self.assertEqual(result.returncode, 1)
            self.assert_no_leak(result)

    def test_deletion_ignores_unavailable_private_policy(self):
        self.private.unlink()
        self.assertEqual(self.push_hook(ZERO, self.base).returncode, 0)

    def test_prepush_merge_resolution_is_scanned(self):
        left = self.snapshot("test: left\n")
        right = self.snapshot("test: right\n", parents=[self.base])
        self.stage("resolution.txt", TERM + "\n")
        merged = self.snapshot("test: merge\n", parents=[left, right])
        result = self.push_hook(merged, self.base)
        self.assertEqual(result.returncode, 1)
        self.assert_no_leak(result)

    def test_range_scans_public_added_lines_and_messages(self):
        record = next(item for item in self.patterns if item["id"] == "github-token")
        self.stage("notes.txt", "".join(record["positive"]) + "\n")
        tip = self.snapshot()
        result = self.scan("--public-only", "--range", self.base + ".." + tip)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"rule=github-token", result.stdout)
        self.stage("notes.txt", "safe\n")
        message_tip = self.snapshot("test: " + "".join(record["positive"]) + "\n")
        result = self.scan("--public-only", "--range", tip + ".." + message_tip)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"commit message", result.stdout)

    def test_exceptions_arguments_and_sources_do_not_leak(self):
        def broken():
            raise OSError(TERM)

        result, output = self.quiet(lambda: privacy.entry(broken))
        self.assertEqual(result, 2)
        self.assertNotIn(TERM, output)
        self.assertNotIn("Traceback", output)

        with mock.patch.object(sys, "argv", ["privacy", "--" + TERM]):
            result, output = self.quiet(privacy.entry)
        self.assertEqual(result, 2)
        self.assertNotIn(TERM, output)

        rules = privacy.load()
        _, output = self.quiet(lambda: rules.text(TERM, TERM + ".txt"))
        self.assertIn("[MASKED]", output)
        self.assertNotIn(TERM, output)


class Results(unittest.TestResult):
    def startTest(self, test):
        super().startTest(test)
        self.before = len(self.failures) + len(self.errors)

    def stopTest(self, test):
        failed = len(self.failures) + len(self.errors) != self.before
        print(("FAIL " if failed else "OK   ") + test._testMethodName)
        super().stopTest(test)


if __name__ == "__main__":
    if shutil.which("git") is None or shutil.which("sh") is None:
        print("FAIL git and sh are required; use Git Bash on Windows")
        sys.exit(1)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PrivacyTests)
    result = Results()
    suite.run(result)
    failed = len(result.failures) + len(result.errors)
    print(f"privacy tests: {result.testsRun - failed} passed, {failed} failed")
    sys.exit(0 if result.wasSuccessful() else 1)
