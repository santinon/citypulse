"""Black-box fixtures: synthetic snapshots and loopback-only HTTP responses."""

import hashlib
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = 1_700_000_000
STAMP = "2023-11-14T22:13:20Z"


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


@pytest.fixture
def run_audit():
    def run(*args):
        return subprocess.run(
            [sys.executable, "scripts/audit_gbfs.py", *map(str, args)],
            cwd=ROOT, capture_output=True, text=True, timeout=45,
        )
    return run


@pytest.fixture
def http_server():
    servers = []

    def start(routes):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                response = routes.get(self.path, (404, {}, b"not found"))
                if callable(response):
                    response = response()
                status, headers, body = response if isinstance(response, tuple) else (
                    200, {"Content-Type": "application/json"}, response
                )
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # Expected when the client stops an oversized download.

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}", requests

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def snapshot(tmp_path):
    def make(information=(), status=(), alerts=None):
        directory = tmp_path / "snapshot"
        directory.mkdir()
        feeds = {
            "station_information": {"stations": list(information)},
            "station_status": {"stations": list(status)},
        }
        if alerts is not None:
            feeds["system_alerts"] = {"alerts": alerts}
        discovery = {"data": {"en": {"feeds": [
            {"name": name, "url": f"https://example.invalid/{name}"} for name in feeds
        ]}}}
        payloads = {"gbfs.json": discovery}
        payloads.update({f"en/{name}.json": {"ttl": 10, "last_updated": REFERENCE,
                                              "data": data} for name, data in feeds.items()})
        entries = []
        for path, payload in payloads.items():
            # 1e400 is valid JSON numeric syntax that overflows a Python float;
            # it exercises non_finite without the forbidden NaN/Infinity tokens.
            body = json_bytes(payload).replace(b"Infinity", b"1e400")
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            discovery_file = path == "gbfs.json"
            entries.append({
                "path": path, "role": "discovery" if discovery_file else "feed",
                "lang": None if discovery_file else "en",
                "feed": None if discovery_file else Path(path).stem,
                "url": f"https://example.invalid/{path}",
                "final_url": f"https://example.invalid/{path}", "http_status": 200,
                "content_type": "application/json", "last_modified": None,
                "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                "collected_at": STAMP, "error": None,
            })
        manifest = {
            "schema": "citypulse.gbfs_audit.manifest/1", "script_version": "0.1.0",
            "discovery_url": "https://example.invalid/gbfs.json", "user_agent": "test",
            "started_at": STAMP, "finished_at": STAMP, "languages": ["en"],
            "errors": [], "files": entries,
        }
        (directory / "manifest.json").write_bytes(json_bytes(manifest))
        return directory
    return make


@pytest.fixture
def profile(run_audit):
    def read(directory):
        result = run_audit("profile", directory)
        assert result.returncode == 0, result.stdout + result.stderr
        body = (directory / "profile.json").read_bytes()
        value = json.loads(body)
        assert body == json_bytes(value)
        return value
    return read
