"""AC2, AC3 and AC6: documentation and committed raw evidence agree."""

import hashlib
import json
import re
import shutil

import pytest

from conftest import ROOT


@pytest.fixture
def committed_snapshot():
    document = (ROOT / "docs/data-quality.md").read_text(encoding="utf-8")
    match = re.search(r"<!-- snapshot: (sample-data/gbfs/\d{8}T\d{6}Z) -->", document)
    assert match, "The documented snapshot reference is required"
    directory = ROOT / match[1]
    assert directory.is_dir(), "The documented snapshot must be committed"
    return directory, document


def test_committed_hashes_and_attribution(committed_snapshot):
    directory, _ = committed_snapshot
    manifest = json.loads((directory / "manifest.json").read_bytes())
    entries = [entry for entry in manifest["files"] if entry["path"] is not None]
    assert entries, "An empty manifest cannot establish integrity"
    paths = {entry["path"] for entry in entries}
    assert {"gbfs.json", "en/station_information.json", "en/station_status.json"} <= paths
    assert paths == {path.relative_to(directory).as_posix() for path in directory.rglob("*.json")
                     if path.name not in {"manifest.json", "profile.json"}}
    for entry in entries:
        assert entry["error"] is None
        body = (directory / entry["path"]).read_bytes()
        assert len(body) == entry["bytes"]
        assert hashlib.sha256(body).hexdigest() == entry["sha256"]
    attribution = (directory / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "BIXI Montréal" in attribution and "CC BY 4.0" in attribution
    assert "https://creativecommons.org/licenses/by/4.0" in attribution


def test_profile_reproduces_bytes_and_every_documented_figure(committed_snapshot, tmp_path, profile):
    directory, document = committed_snapshot
    original = (directory / "profile.json").read_bytes()
    copied = tmp_path / "snapshot"
    shutil.copytree(directory, copied)
    (copied / "profile.json").unlink()  # the script must write it again
    actual = profile(copied)
    assert (copied / "profile.json").read_bytes() == original
    assert document.count("<!-- figures:begin -->") == document.count("<!-- figures:end -->") == 1
    table = document.split("<!-- figures:begin -->")[1].split("<!-- figures:end -->")[0]
    assert "| Profile key | Value | Meaning |" in table
    rows = []
    for line in table.splitlines():
        if not line.strip():
            continue
        if line.strip() == "| Profile key | Value | Meaning |":
            continue
        if re.fullmatch(r"\s*\|[\s:|-]+\|\s*", line):
            continue
        match = re.fullmatch(r"\s*\| `([^`]+)` \| `([^`]+)` \| (.+) \|\s*", line)
        assert match, f"Malformed figures row: {line}"
        rows.append((match[1], json.loads(match[2])))
    assert rows, "An empty figures table must fail AC2"
    for pointer, expected in rows:
        assert pointer.startswith("/"), f"Expected a JSON Pointer: {pointer}"
        value = actual
        for token in pointer[1:].split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        assert value == expected, pointer
