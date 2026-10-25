"""Discovery controls requests; untrusted names, URLs and bodies stay contained."""

import hashlib
import json
import re
import socket
import threading

import pytest

from conftest import json_bytes


def discovery(feeds, lang="en"):
    return json_bytes({"data": {lang: {"feeds": feeds}}})


def fetch(run_audit, tmp_path, url, *args):
    directory = tmp_path / "download"
    result = run_audit("fetch", "--out", directory, "--discovery-url", url, *args)
    return result, directory, json.loads((directory / "manifest.json").read_bytes())


def test_discovery_exact_urls_raw_bytes_and_manifest(http_server, run_audit, tmp_path):
    routes = {}
    base, requests = http_server(routes)
    paths = ["/odd/a?token=public&x=2", "/other/deep/b", "/fr-only"]
    feeds = [{"name": name, "url": base + path} for name, path in zip(
        ["station_information", "station_status", "system_information"], paths)]
    raw = b'{ "data": {}, "unknown": "kept" }\r\n'
    routes["/discovery"] = json.dumps({"data": {
        "fr": {"feeds": feeds[2:]}, "en": {"feeds": feeds[:2]},
    }}).encode()
    modified = "Tue, 14 Nov 2023 22:13:20 GMT"
    for path in paths:
        routes[path] = (200, {"Content-Type": "application/json",
                              "Last-Modified": modified}, raw)
    result, directory, manifest = fetch(run_audit, tmp_path, base + "/discovery")
    assert result.returncode == 0, result.stderr
    assert requests == ["/discovery", paths[2], *paths[:2]]
    assert manifest["schema"] == "citypulse.gbfs_audit.manifest/1"
    assert manifest["script_version"] == "0.1.0"
    assert manifest["discovery_url"] == base + "/discovery"
    assert manifest["languages"] == ["fr", "en"]
    assert manifest["errors"] == [] and manifest["user_agent"]
    for key in ("started_at", "finished_at"):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", manifest[key])
    expected = [("gbfs.json", None, None, "/discovery"),
                ("fr/system_information.json", "fr", "system_information", paths[2]),
                ("en/station_information.json", "en", "station_information", paths[0]),
                ("en/station_status.json", "en", "station_status", paths[1])]
    assert len(manifest["files"]) == len(expected)
    for entry, (path, lang, name, requested) in zip(manifest["files"], expected):
        body = routes[requested] if name is None else raw
        assert (directory / path).read_bytes() == body
        assert entry == {
            "path": path, "role": "discovery" if name is None else "feed",
            "lang": lang, "feed": name, "url": base + requested,
            "final_url": base + requested, "http_status": 200,
            "content_type": "application/json", "last_modified": None if name is None else modified,
            "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
            "collected_at": entry["collected_at"], "error": None,
        }
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", entry["collected_at"])
    assert (directory / "manifest.json").read_bytes() == json_bytes(manifest)


@pytest.mark.parametrize("name", ["../escape", "Upper", "1abc", "a" * 65,
                                        "con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"])
def test_invalid_feed_names_not_requested(name, http_server, run_audit, tmp_path):
    routes = {}
    base, requests = http_server(routes)
    routes["/d"] = discovery([{"name": name, "url": base + "/forbidden"}])
    result, _, manifest = fetch(run_audit, tmp_path, base + "/d")
    assert result.returncode == 1 and requests == ["/d"]
    assert manifest["files"][1]["error"]["code"] == "invalid_name"
    assert manifest["files"][1]["path"] is None


@pytest.mark.parametrize("lang", ["../en", "english", "EN"])
def test_invalid_languages(lang, http_server, run_audit, tmp_path):
    routes = {}
    base, requests = http_server(routes)
    routes["/d"] = discovery([{"name": "a", "url": base + "/no"}], lang)
    result, _, manifest = fetch(run_audit, tmp_path, base + "/d")
    assert result.returncode == 1 and requests == ["/d"]
    assert any(entry["error"] and entry["error"]["code"] == "invalid_name"
               for entry in manifest["files"])


@pytest.mark.parametrize("kind,code", [("file", "invalid_url"), ("credentials", "invalid_url"),
    ("malformed", "invalid_url"),
    ("host", "origin_not_allowed"), ("scheme", "origin_not_allowed"), ("port", "origin_not_allowed")])
@pytest.mark.parametrize("redirect", [False, True])
def test_url_safety(kind, code, redirect, http_server, run_audit, tmp_path):
    routes = {}
    base, requests = http_server(routes)
    other, other_requests = http_server({"/secret": b"{}"})
    targets = {"file": "file:///forbidden.json", "credentials": base.replace("://", "://user:pass@") + "/secret",
               "malformed": "http://[",
               "host": base.replace("127.0.0.1", "localhost") + "/secret",
               "scheme": base.replace("http:", "https:") + "/secret", "port": other + "/secret"}
    target = targets[kind]
    routes["/d"] = discovery([{"name": "a", "url": base + "/redirect" if redirect else target}])
    routes["/redirect"] = (302, {"Location": target}, b"")
    result, _, manifest = fetch(run_audit, tmp_path, base + "/d")
    assert result.returncode == 1
    assert requests == (["/d", "/redirect"] if redirect else ["/d"])
    assert other_requests == []
    entry = manifest["files"][1]
    assert entry["error"]["code"] == code and entry["path"] is None


@pytest.mark.parametrize("status,body,code", [(404, b"missing", "http_status"),
    (200, b"{broken", "invalid_json"), (200, b"\xff", "invalid_json"),
    (200, b'{"x":NaN}', "invalid_json"), (200, b"\xef\xbb\xbf{}", "invalid_json"),
    (200, b"x" * (10_485_760 + 1), "too_large")],
    ids=["http-404", "broken-json", "invalid-utf8", "nan", "bom", "above-10mib"])
def test_bad_bodies(status, body, code, http_server, run_audit, tmp_path):
    routes = {}
    base, requests = http_server(routes)
    routes["/d"] = discovery([{"name": "a", "url": base + "/body"}])
    routes["/body"] = (status, {"Content-Type": "application/json"}, body)
    result, directory, manifest = fetch(run_audit, tmp_path, base + "/d")
    assert result.returncode == 1 and requests == ["/d", "/body"]
    entry = manifest["files"][1]
    assert entry["error"]["code"] == code
    if code == "too_large":
        assert entry["path"] is None and not (directory / "en/a.json").exists()
    else:
        assert (directory / entry["path"]).read_bytes() == body
        assert entry["sha256"] == hashlib.sha256(body).hexdigest()


def test_duplicate_feed_and_language_selection(http_server, run_audit, tmp_path):
    routes = {}
    base, requests = http_server(routes)
    routes["/d"] = json_bytes({"data": {"fr": {"feeds": [{"name": "a", "url": base + "/fr"}]},
        "en": {"feeds": [{"name": "a", "url": base + "/first"},
                          {"name": "a", "url": base + "/second"}]}}})
    routes["/first"] = b"{}"
    result, _, manifest = fetch(run_audit, tmp_path, base + "/d", "--lang", "en")
    assert result.returncode == 1 and requests == ["/d", "/first"]
    assert manifest["languages"] == ["en"]
    assert manifest["files"][2]["error"]["code"] == "duplicate_feed"


def test_same_origin_redirect_exact_size_cap_and_repeated_languages(http_server, run_audit, tmp_path):
    routes = {}
    base, requests = http_server(routes)
    name = "a" * 64
    routes["/d"] = json.dumps({"data": {"fr": {"feeds": []}, "en": {"feeds": [
        {"name": name, "url": base + "/redirect"}]}}}).encode()
    routes["/redirect"] = (302, {"Location": "/final?x=1"}, b"")
    body = b"{}" + b" " * (10_485_760 - 2)
    routes["/final?x=1"] = body
    result, directory, manifest = fetch(run_audit, tmp_path, base + "/d",
                                         "--lang", "en", "--lang", "fr")
    assert result.returncode == 0 and requests == ["/d", "/redirect", "/final?x=1"]
    assert manifest["languages"] == ["fr", "en"]
    entry = manifest["files"][1]
    assert entry["error"] is None and entry["final_url"] == base + "/final?x=1"
    assert entry["bytes"] == 10_485_760 and entry["sha256"] == hashlib.sha256(body).hexdigest()
    assert (directory / "en" / f"{name}.json").read_bytes() == body


@pytest.mark.parametrize("body,lang,code", [(b"{}", None, "invalid_discovery"),
    (b'{"data":[]}', None, "invalid_discovery"), (discovery([]), "fr", "lang_not_declared")])
def test_discovery_errors(body, lang, code, http_server, run_audit, tmp_path):
    base, requests = http_server({"/d": body})
    result, _, manifest = fetch(run_audit, tmp_path, base + "/d", *(["--lang", lang] if lang else []))
    assert result.returncode == 1 and requests == ["/d"]
    errors = manifest["errors"] if lang else [manifest["files"][0]["error"]]
    assert any(error["code"] == code for error in errors)


@pytest.mark.parametrize("bad", ["output", "output_file", "scheme", "credentials", "argument"])
def test_argument_errors_request_nothing(bad, http_server, run_audit, tmp_path):
    base, requests = http_server({"/d": discovery([])})
    directory = tmp_path / "out"
    url = base + "/d"
    extra = []
    if bad == "output":
        directory.mkdir()
        (directory / "keep").write_bytes(b"unchanged")
    elif bad == "output_file":
        directory.write_bytes(b"unchanged")
    elif bad == "scheme":
        url = "file:///forbidden"
    elif bad == "credentials":
        url = base.replace("://", "://user:password@") + "/d"
    else:
        extra = ["--unknown-option"]
    result = run_audit("fetch", "--out", directory, "--discovery-url", url, *extra)
    assert result.returncode == 2 and requests == []
    assert not (directory / "manifest.json").exists()
    if bad == "output":
        assert (directory / "keep").read_bytes() == b"unchanged"
    elif bad == "output_file":
        assert directory.read_bytes() == b"unchanged"


def test_network_failure_still_writes_manifest(run_audit, tmp_path):
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))  # Bound but not listening: connection refused.
        result, _, manifest = fetch(run_audit, tmp_path,
                                    f"http://127.0.0.1:{unavailable.getsockname()[1]}/d")
    assert result.returncode == 1
    assert manifest["files"][0]["error"]["code"] == "network"
    assert manifest["files"][0]["path"] is None


def test_timeout_has_no_body(http_server, run_audit, tmp_path):
    release = threading.Event()
    routes = {}
    base, requests = http_server(routes)
    routes["/d"] = discovery([{"name": "a", "url": base + "/slow"}])

    def slow_response():
        release.wait(35)  # The contracted client timeout is 30 seconds.
        return b"{}"

    routes["/slow"] = slow_response
    try:
        result, directory, manifest = fetch(run_audit, tmp_path, base + "/d")
    finally:
        release.set()
    assert result.returncode == 1 and requests == ["/d", "/slow"]
    assert manifest["files"][1]["error"]["code"] == "timeout"
    assert manifest["files"][1]["path"] is None
    assert not (directory / "en/a.json").exists()
