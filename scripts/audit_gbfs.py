# /// script
# requires-python = ">=3.12"
# dependencies = ["geographiclib==2.0"]
# ///
"""Reproducible audit of a GBFS feed (issue #2): fetch a raw snapshot, then profile it.

    uv run scripts/audit_gbfs.py fetch --out DIR [--discovery-url URL] [--lang LANG]...
    uv run scripts/audit_gbfs.py profile DIR [--lang LANG]

fetch reads the discovery file and downloads exactly the feed URLs it declares (it never
builds a feed path), writes every body byte for byte, then DIR/manifest.json. Exit 0 when
every file was fetched, 1 when a file or the run has an error (the manifest is still
written), 2 when nothing was requested (bad arguments, DIR not empty, unusable discovery URL).

profile checks every file against the SHA-256 in the manifest, then writes DIR/profile.json.
The reference time is the fetch time of station_status, never the machine clock, so one
snapshot always gives the same bytes. Exit 1, without a profile, when the snapshot is
incomplete or altered. docs/data-quality.md defines every metric.

Downloaded data is untrusted: same scheme, host and port as the discovery URL only
(redirects included), no user:password in URLs, checked file names, at most 10 MiB per
body, parsed only with the json module.
"""
import argparse
import hashlib
import http.client
import json
import re
import ssl
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from geographiclib.geodesic import Geodesic

SCRIPT_VERSION = "0.1.0"
MANIFEST_SCHEMA = "citypulse.gbfs_audit.manifest/1"
PROFILE_SCHEMA = "citypulse.gbfs_audit.profile/1"
DEFAULT_DISCOVERY_URL = "https://gbfs.velobixi.com/gbfs/gbfs.json"
USER_AGENT = f"citypulse-audit/{SCRIPT_VERSION} (+https://github.com/santinon/citypulse)"
TIMEOUT_S = 30
MAX_BODY_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
FEED_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
LANGUAGE = re.compile(r"[a-z]{2}(-[A-Za-z]{2})?")
WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)}
SNAPSHOT_PATH = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TIME_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

TIMESTAMP_FLOOR = 946684800  # 2000-01-01T00:00:00Z: application cutoff, not a GBFS rule
FUTURE_TOLERANCE_S = 300
OLDER_THAN_S = (3600, 86400, 2592000)
FLAGS = ("is_installed", "is_renting", "is_returning")
CAPACITY_OPERANDS = ("num_bikes_available", "num_docks_available",
                     "num_bikes_disabled", "num_docks_disabled")
COORDINATE_CLASSES = ("non_numeric", "non_finite", "out_of_range", "zero_zero", "valid")
DISTANCE_BUCKETS_M = (("0-10", 10_000), ("10-50", 50_000), ("50-100", 100_000))

Origin = tuple[str, str, int]


class SnapshotError(Exception):
    """The snapshot is incomplete or altered: no profile is written."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(TIME_FORMAT)


def write_json(path: Path, value: Any) -> None:
    """UTF-8, sorted keys, indent 2, LF, final newline: the same value gives the same bytes."""
    text = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    path.write_bytes((text + "\n").encode("utf-8"))


def parse_json(body: bytes) -> Any:
    """Strict JSON from UTF-8 bytes; NaN and Infinity are refused. Raises ValueError."""
    def refuse(name: str) -> Any:
        raise ValueError(f"{name} is not JSON")
    return json.loads(body.decode("utf-8"), parse_constant=refuse)


def error(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def is_int(value: Any) -> bool:
    """A JSON integer: not a bool, not a float (3.0 is not an integer)."""
    return isinstance(value, int) and not isinstance(value, bool)


def json_type(value: Any) -> str:
    for name, kind in (("bool", bool), ("int", int), ("float", float), ("str", str),
                       ("list", list), ("dict", dict)):
        if isinstance(value, kind):
            return name
    return "null"


# ---------------------------------------------------------------- fetch

def origin_of(url: str) -> Origin:
    parts = urlsplit(url)
    default = 443 if parts.scheme == "https" else 80
    return parts.scheme, parts.hostname or "", default if parts.port is None else parts.port


def url_problem(url: str, allowed: Origin | None) -> tuple[str, str] | None:
    """(code, detail) when url must not be requested; allowed=None skips the origin check."""
    if any(not 0x21 <= ord(c) <= 0x7E for c in url):
        return "invalid_url", "URL has spaces, control or non-ASCII characters"
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError on a bad port
    except ValueError as exc:
        return "invalid_url", f"cannot parse URL: {exc}"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "invalid_url", "not an http or https URL with a host"
    if parts.username is not None or parts.password is not None:
        return "invalid_url", "URL carries user:password"
    if allowed is not None and origin_of(url) != allowed:
        return "origin_not_allowed", "scheme, host or port differ from the discovery URL"
    return None


def read_capped(response: http.client.HTTPResponse) -> bytes | None:
    """The body, or None when it is larger than MAX_BODY_BYTES (download stopped)."""
    length = response.getheader("Content-Length")
    if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return None
    chunks: list[bytes] = []
    size = 0
    while chunk := response.read(64 * 1024):
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def download(url: str, allowed: Origin) -> dict[str, Any]:
    """GET url, following same-origin redirects only. Network problems become an error."""
    got: dict[str, Any] = {"final_url": None, "http_status": None, "content_type": None,
                           "last_modified": None, "collected_at": None, "error": None, "body": None}
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            host, port = parts.hostname or "", origin_of(current)[2]
            conn = (http.client.HTTPSConnection(host, port, timeout=TIMEOUT_S,
                                                context=ssl.create_default_context())
                    if parts.scheme == "https"
                    else http.client.HTTPConnection(host, port, timeout=TIMEOUT_S))
            try:
                target = parts.path or "/"
                if "?" in current.split("#", 1)[0]:
                    target += "?" + parts.query
                conn.request("GET", target, headers={"User-Agent": USER_AGENT,
                                                     "Accept": "application/json"})
                response = conn.getresponse()
                got.update(final_url=current, http_status=response.status,
                           content_type=response.getheader("Content-Type"),
                           last_modified=response.getheader("Last-Modified"))
                location = response.getheader("Location")
                if response.status in REDIRECT_STATUSES and location is not None:
                    try:
                        next_url = urljoin(current, location)
                    except ValueError as exc:
                        got["error"] = error("invalid_url", f"redirect to {location!r}: {exc}")
                        return got
                    problem = url_problem(next_url, allowed)
                    if problem is not None:
                        got["error"] = error(problem[0], f"redirect to {next_url}: {problem[1]}")
                        return got
                    current = next_url
                    continue
                body = read_capped(response)
            finally:
                conn.close()
            if body is None:
                got["error"] = error("too_large", f"body above {MAX_BODY_BYTES} bytes")
            else:
                got.update(body=body, collected_at=utc_now())
            return got
        got["error"] = error("http_status", f"more than {MAX_REDIRECTS} redirects")
    except TimeoutError:
        got["error"] = error("timeout", f"no answer within {TIMEOUT_S} s")
    except (OSError, http.client.HTTPException) as exc:
        got["error"] = error("network", f"{type(exc).__name__}: {exc}")
    return got


def new_entry(role: str, lang: str | None, feed: str | None, url: str) -> dict[str, Any]:
    return {"path": None, "role": role, "lang": lang, "feed": feed, "url": url,
            "final_url": None, "http_status": None, "content_type": None,
            "last_modified": None, "bytes": None, "sha256": None, "collected_at": None,
            "error": None}


def fetch_into(entry: dict[str, Any], out: Path, path: str, allowed: Origin) -> Any:
    """Download entry["url"] to out/path and fill entry. Returns the parsed body or None."""
    problem = url_problem(entry["url"], allowed)
    if problem is not None:
        entry["error"] = error(*problem)
        return None
    got = download(entry["url"], allowed)
    body = got.pop("body")
    entry.update(got)
    if body is None:
        return None
    dest = out / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "xb") as handle:
        handle.write(body)
    entry.update(path=path, bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    if entry["http_status"] != 200:
        entry["error"] = error("http_status", f"HTTP {entry['http_status']}")
        return None
    try:
        return parse_json(body)
    except (ValueError, RecursionError) as exc:
        entry["error"] = error("invalid_json", str(exc))
        return None


def discovery_feeds(doc: Any) -> dict[str, list[tuple[str, str]]] | None:
    """{lang: [(name, url)]} in discovery order, or None when the structure is wrong."""
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        return None
    declared: dict[str, list[tuple[str, str]]] = {}
    for lang, value in data.items():
        feeds = value.get("feeds") if isinstance(value, dict) else None
        if not isinstance(feeds, list):
            return None
        declared[lang] = []
        for feed in feeds:
            if not (isinstance(feed, dict) and isinstance(feed.get("name"), str)
                    and isinstance(feed.get("url"), str)):
                return None
            declared[lang].append((feed["name"], feed["url"]))
    return declared


def cmd_fetch(args: argparse.Namespace) -> int:
    out = Path(args.out)
    problem = url_problem(args.discovery_url, None)
    if problem is not None:
        print(f"fetch: discovery URL refused: {problem[1]}", file=sys.stderr)
        return 2
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        print(f"fetch: {out} exists and is not an empty directory", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    allowed = origin_of(args.discovery_url)
    started_at = utc_now()

    discovery = new_entry("discovery", None, None, args.discovery_url)
    files = [discovery]
    run_errors: list[dict[str, str]] = []
    languages: list[str] = []
    doc = fetch_into(discovery, out, "gbfs.json", allowed)
    declared = discovery_feeds(doc) if discovery["error"] is None else None
    if discovery["error"] is None and declared is None:
        discovery["error"] = error("invalid_discovery",
                                   "no data object of {lang: {feeds: [{name, url}]}}")
    if declared is not None:
        wanted = args.lang or list(declared)
        run_errors = [error("lang_not_declared", lang)
                      for lang in dict.fromkeys(wanted) if lang not in declared]
        languages = [lang for lang in declared if lang in wanted]
        seen_languages: set[str] = set()
        for lang in languages:
            lang_problem = ("language name not allowed" if not LANGUAGE.fullmatch(lang)
                            else "language repeats another one, ignoring case"
                            if lang.casefold() in seen_languages else None)
            seen_languages.add(lang.casefold())
            seen_feeds: set[str] = set()
            for name, url in declared[lang]:
                entry = new_entry("feed", lang, name, url)
                files.append(entry)
                if lang_problem is not None:
                    entry["error"] = error("invalid_name", lang_problem)
                elif not FEED_NAME.fullmatch(name) or name in WINDOWS_RESERVED:
                    entry["error"] = error("invalid_name", "feed name not allowed")
                elif name in seen_feeds:
                    entry["error"] = error("duplicate_feed", f"feed {name} already declared")
                else:
                    seen_feeds.add(name)
                    fetch_into(entry, out, f"{lang}/{name}.json", allowed)

    write_json(out / "manifest.json", {
        "schema": MANIFEST_SCHEMA, "script_version": SCRIPT_VERSION,
        "discovery_url": args.discovery_url, "user_agent": USER_AGENT,
        "started_at": started_at, "finished_at": utc_now(), "languages": languages,
        "errors": run_errors, "files": files,
    })
    failed = sum(entry["error"] is not None for entry in files)
    print(f"fetch: {len(files)} files, {failed} with an error, {len(run_errors)} run errors -> {out}")
    return 1 if failed or run_errors else 0


# ---------------------------------------------------------------- profile

def load_snapshot(snapshot: Path) -> tuple[bytes, list[dict[str, Any]], dict[str, bytes]]:
    """Manifest bytes, its file entries and every listed body, checked against its SHA-256."""
    try:
        manifest_bytes = (snapshot / "manifest.json").read_bytes()
        entries = parse_json(manifest_bytes)["files"]
    except (OSError, ValueError, RecursionError, KeyError, TypeError) as exc:
        raise SnapshotError(f"manifest.json missing or not valid: {exc}") from exc
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise SnapshotError("manifest.json: files is not a list of objects")
    bodies: dict[str, bytes] = {}
    for entry in entries:
        path = entry.get("path")
        if path is None:
            continue
        if (not isinstance(path, str) or not SNAPSHOT_PATH.fullmatch(path)
                or ".." in path.split("/")):
            raise SnapshotError(f"manifest.json: path not allowed: {path!r}")
        try:
            body = (snapshot / path).read_bytes()
        except OSError as exc:
            raise SnapshotError(f"{path}: cannot read: {exc}") from exc
        if hashlib.sha256(body).hexdigest() != entry.get("sha256"):
            raise SnapshotError(f"{path}: SHA-256 differs from the manifest")
        bodies[path] = body
    return manifest_bytes, entries, bodies


def snapshot_doc(entries: list[dict[str, Any]], bodies: dict[str, bytes], role: str,
                 lang: str | None, feed: str | None, required: bool) -> tuple[dict[str, Any], Any] | None:
    """(entry, parsed body) of one file. Absent, or listed with an error: SnapshotError when
    required, else None."""
    matches = [e for e in entries
               if e.get("role") == role and e.get("lang") == lang and e.get("feed") == feed]
    if not matches or any(e.get("error") is not None or e.get("path") is None for e in matches):
        if required:
            raise SnapshotError(f"{feed or role} ({lang}) absent or with an error in the manifest")
        return None
    try:
        return matches[0], parse_json(bodies[matches[0]["path"]])
    except (ValueError, RecursionError) as exc:
        raise SnapshotError(f"{matches[0]['path']}: not valid JSON: {exc}") from exc


def data_list(doc: Any, key: str, name: str) -> list[dict[str, Any]]:
    """data.<key> as a list; records that are not objects count as empty objects."""
    data = doc.get("data") if isinstance(doc, dict) else None
    records = data.get(key) if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise SnapshotError(f"{name}: no data.{key} list")
    return [r if isinstance(r, dict) else {} for r in records]


def index_ids(records: list[dict[str, Any]]) -> tuple[int, set[str], dict[str, dict[str, Any]]]:
    """Invalid ids, duplicate ids (distinct) and the records of ids seen exactly once."""
    ids = [r.get("station_id") for r in records]
    counts = Counter(i for i in ids if isinstance(i, str) and i)
    unique = {r["station_id"]: r for r in records
              if isinstance(r.get("station_id"), str) and counts[r["station_id"]] == 1}
    return len(ids) - sum(counts.values()), {i for i, n in counts.items() if n > 1}, unique


def field_inventory(records: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for record in records:
        for key, value in record.items():
            slot = fields.setdefault(key, {"records": 0, "types": {}})
            slot["records"] += 1
            slot["types"][json_type(value)] = slot["types"].get(json_type(value), 0) + 1
    return fields


def flag_key(record: dict[str, Any], name: str) -> str:
    if name not in record:
        return "absent"
    value = record[name]
    if isinstance(value, bool):
        return f"bool:{str(value).lower()}"
    if is_int(value):
        return f"int:{value}" if value in (0, 1) else "int:other"
    return json_type(value)


def normalised_flag(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    if isinstance(value, bool) or (is_int(value) and value in (0, 1)):
        return str(int(value))
    return "x"


def coordinate_class(record: dict[str, Any]) -> str:
    lat, lon = record.get("lat"), record.get("lon")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lon)):
        return "non_numeric"
    if any(isinstance(v, float) and (v != v or v in (float("inf"), float("-inf")))
           for v in (lat, lon)):
        return "non_finite"
    if abs(lat) > 90 or abs(lon) > 180:
        return "out_of_range"
    if lat == 0 and lon == 0:
        return "zero_zero"
    return "valid"


def last_reported_invalid(record: dict[str, Any], reference: int) -> bool:
    value = record.get("last_reported")
    return not (is_int(value) and TIMESTAMP_FLOOR <= value <= reference + FUTURE_TOLERANCE_S)


def last_reported_metrics(status: list[dict[str, Any]], reference: int) -> dict[str, Any]:
    invalid_values: Counter[str] = Counter()
    ages: list[int] = []
    future = 0
    for record in status:
        if last_reported_invalid(record, reference):
            key = "absent" if "last_reported" not in record else json.dumps(record["last_reported"])
            invalid_values[key] += 1
        else:
            ages.append(reference - record["last_reported"])
            future += record["last_reported"] > reference
    ages.sort()
    n = len(ages)
    return {
        "records": len(status), "invalid": sum(invalid_values.values()),
        "invalid_values": dict(invalid_values), "future_tolerated": future,
        "age_seconds": {
            "n": n, "min": ages[0] if n else None,
            "median": statistics.median(ages) if n else None,
            "p90": ages[(9 * n + 9) // 10 - 1] if n else None,  # nearest rank: ceil(0.9 n)
            "max": ages[-1] if n else None,
        },
        "older_than": {str(s): sum(age > s for age in ages) for s in OLDER_THAN_S},
    }


def remote_station_metrics(information: list[dict[str, Any]]) -> dict[str, Any]:
    points = [(r["lat"], r["lon"]) for r in information if coordinate_class(r) == "valid"]
    buckets = {name: 0 for name, _ in DISTANCE_BUCKETS_M} | {"100+": 0}
    far: list[tuple[float, float]] = []
    origin = None
    if points:
        origin = {"lat": float(statistics.median(p[0] for p in points)),
                  "lon": float(statistics.median(p[1] for p in points))}
        for lat, lon in points:
            metres = Geodesic.WGS84.Inverse(origin["lat"], origin["lon"], lat, lon)["s12"]
            bucket = next((name for name, limit in DISTANCE_BUCKETS_M if metres <= limit), "100+")
            buckets[bucket] += 1
            if metres > 50_000:
                far.append((lat, lon))
    lats, lons = [p[0] for p in far], [p[1] for p in far]
    return {"origin": origin, "buckets_km": buckets, "beyond_50km": {
        "count": len(far),
        "lat_min": min(lats, default=None), "lat_max": max(lats, default=None),
        "lon_min": min(lons, default=None), "lon_max": max(lons, default=None)}}


def active_at(window: Any, reference: int) -> bool:
    if not isinstance(window, dict) or not is_int(window.get("start")):
        return False
    end = window.get("end")
    return window["start"] <= reference and (end is None or (is_int(end) and end >= reference))


def alert_metrics(doc: Any, reference: int) -> dict[str, Any]:
    alerts = data_list(doc, "alerts", "system_alerts")
    types: Counter[str] = Counter()
    summaries: dict[str, set[str]] = {}
    with_times = active = with_stations = with_regions = 0
    for alert in alerts:
        times = alert.get("times")
        if isinstance(times, list) and times:
            with_times += 1
            active += any(active_at(window, reference) for window in times)
        with_stations += isinstance(alert.get("station_ids"), list) and bool(alert["station_ids"])
        with_regions += isinstance(alert.get("region_ids"), list) and bool(alert["region_ids"])
        kind = alert.get("type") if isinstance(alert.get("type"), str) and alert["type"] else "invalid"
        types[kind] += 1
        found = summaries.setdefault(kind, set())
        if isinstance(alert.get("summary"), str):
            found.add(alert["summary"])
    return {"records": len(alerts), "with_times": with_times,
            "without_times": len(alerts) - with_times, "active_at_reference": active,
            "with_station_ids": with_stations, "with_region_ids": with_regions,
            "types": dict(types),
            "summaries_by_type": {kind: sorted(found) for kind, found in summaries.items()}}


def build_profile(snapshot: Path, lang: str) -> dict[str, Any]:
    manifest_bytes, entries, bodies = load_snapshot(snapshot)
    _, discovery_doc = snapshot_doc(entries, bodies, "discovery", None, None, True)
    _, info_doc = snapshot_doc(entries, bodies, "feed", lang, "station_information", True)
    status_entry, status_doc = snapshot_doc(entries, bodies, "feed", lang, "station_status", True)
    alerts_found = snapshot_doc(entries, bodies, "feed", lang, "system_alerts", False)
    collected_at = status_entry.get("collected_at")
    try:
        # strptime alone accepts "2023-1-4T2:3:4Z": check the exact form first.
        if not isinstance(collected_at, str) or not TIME_SHAPE.fullmatch(collected_at):
            raise ValueError(f"{collected_at!r} is not YYYY-MM-DDTHH:MM:SSZ")
        reference = int(datetime.strptime(collected_at, TIME_FORMAT)
                        .replace(tzinfo=timezone.utc).timestamp())
    except ValueError as exc:
        raise SnapshotError(f"station_status ({lang}): collected_at not valid: {exc}") from exc
    declared = discovery_feeds(discovery_doc)
    if declared is None:
        raise SnapshotError("discovery file: no data object of {lang: {feeds: [{name, url}]}}")

    files: dict[str, Any] = {}
    for entry in entries:
        if entry.get("path") is None:
            continue
        try:
            doc = parse_json(bodies[entry["path"]])
        except (ValueError, RecursionError):
            doc = None
        top = doc if isinstance(doc, dict) else {}
        files[entry["path"]] = {
            "http_status": entry.get("http_status"), "content_type": entry.get("content_type"),
            "bytes": entry.get("bytes"),
            "ttl": top["ttl"] if is_int(top.get("ttl")) else None,
            "last_updated": top["last_updated"] if is_int(top.get("last_updated")) else None,
            "has_version": "version" in top}

    information = data_list(info_doc, "stations", "station_information")
    status = data_list(status_doc, "stations", "station_status")
    info_invalid, info_duplicates, info_unique = index_ids(information)
    status_invalid, status_duplicates, status_unique = index_ids(status)
    # Join on ids that are unique in their feed and not duplicated in the other one.
    left = {i for i in info_unique if i not in status_duplicates}
    right = {i for i in status_unique if i not in info_duplicates}
    both = left & right

    population = equals_all = equals_available = 0
    for station_id in sorted(both):
        operands = [info_unique[station_id].get("capacity"),
                    *(status_unique[station_id].get(k) for k in CAPACITY_OPERANDS)]
        if not all(is_int(v) and v >= 0 for v in operands):
            continue
        capacity, bikes, docks, bikes_disabled, docks_disabled = operands
        population += 1
        equals_all += capacity == bikes + docks + bikes_disabled + docks_disabled
        equals_available += capacity == bikes + docks

    rental_values: Counter[str] = Counter()
    rental_without = rental_invalid = 0
    for record in information:
        if "rental_methods" not in record:
            rental_without += 1
        elif (isinstance(record["rental_methods"], list)
              and all(isinstance(m, str) for m in record["rental_methods"])):
            rental_values.update(set(record["rental_methods"]))  # unit: station record
        else:
            rental_invalid += 1

    classes = Counter(coordinate_class(r) for r in information)
    zero_zero = []
    for record in information:
        if coordinate_class(record) != "zero_zero":
            continue
        station_id = record.get("station_id")
        joined = (status_unique[station_id]
                  if isinstance(station_id, str) and station_id in both else None)
        zero_zero.append({"station_id": station_id, "capacity": record.get("capacity"),
                          "last_reported_invalid": None if joined is None
                          else last_reported_invalid(joined, reference)})
    zero_zero.sort(key=lambda z: (0, z["station_id"]) if isinstance(z["station_id"], str)
                   else (1, json.dumps(z["station_id"])))

    return {
        "schema": PROFILE_SCHEMA, "script_version": SCRIPT_VERSION, "lang": lang,
        "reference_time": status_entry["collected_at"], "reference_time_posix": reference,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "files": files,
        "discovery": {"languages": list(declared),
                      "feeds": {k: [name for name, _ in v] for k, v in declared.items()}},
        "stations": {
            "information_records": len(information), "status_records": len(status),
            "information_invalid_ids": info_invalid, "status_invalid_ids": status_invalid,
            "information_duplicate_ids": len(info_duplicates),
            "status_duplicate_ids": len(status_duplicates),
            "join": {"both": len(both), "information_only": len(left - right),
                     "status_only": len(right - left)}},
        "fields": {"station_information": field_inventory(information),
                   "station_status": field_inventory(status)},
        "flags": {**{name: dict(Counter(flag_key(r, name) for r in status)) for name in FLAGS},
                  "combinations": dict(Counter(",".join(normalised_flag(r, name) for name in FLAGS)
                                               for r in status))},
        "rental_methods": {"values": dict(rental_values), "records_without": rental_without,
                           "records_invalid": rental_invalid},
        "coordinates": {**{name: classes[name] for name in COORDINATE_CLASSES},
                        "zero_zero_stations": zero_zero},
        "capacity": {"population": population, "excluded": len(both) - population,
                     "equals_bikes_docks_disabled": equals_all,
                     "equals_bikes_docks": equals_available},
        "last_reported": last_reported_metrics(status, reference),
        "remote_stations": remote_station_metrics(information),
        "alerts": None if alerts_found is None else alert_metrics(alerts_found[1], reference),
    }


def cmd_profile(args: argparse.Namespace) -> int:
    snapshot = Path(args.dir)
    try:
        profile = build_profile(snapshot, args.lang)
    except SnapshotError as exc:
        print(f"profile: {exc}", file=sys.stderr)
        return 1
    write_json(snapshot / "profile.json", profile)
    print(f"profile: {profile['stations']['join']['both']} joined stations -> "
          f"{snapshot / 'profile.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and profile a GBFS snapshot.")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="download the discovery file and its feeds")
    fetch.add_argument("--out", required=True, help="new or empty directory")
    fetch.add_argument("--discovery-url", default=DEFAULT_DISCOVERY_URL)
    fetch.add_argument("--lang", action="append",
                       help="language to fetch (repeatable; default: every declared one)")
    profile = commands.add_parser("profile", help="check a snapshot and write profile.json")
    profile.add_argument("dir")
    profile.add_argument("--lang", default="en")
    args = parser.parse_args(argv)
    return cmd_fetch(args) if args.command == "fetch" else cmd_profile(args)


if __name__ == "__main__":
    sys.exit(main())
