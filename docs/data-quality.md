# Data quality: BIXI GBFS feed

This document turns the first manual audit of the BIXI GBFS feed (2026-10-20, issue #2) into
a reproducible measurement and a set of numbered rules (DQ-n) for the normaliser (#4), the
database schema (#5) and the collector (#6). Every figure below comes from one committed
snapshot and is recomputed by a test.

## 1. Method

**Why.** The business rules (out of service, stale data, occupancy) depend on how the feed
actually behaves: types of the flags, sentinel timestamps, stations at (0, 0), what `capacity`
counts. Those facts must be measured, versioned and re-checkable before any logic relies on them.

**How.** `scripts/audit_gbfs.py` (standard library plus `geographiclib`):

```sh
uv run scripts/audit_gbfs.py fetch --out <new folder> --lang en   # raw snapshot + manifest.json
uv run scripts/audit_gbfs.py profile <folder>                     # checks SHA-256, writes profile.json
```

- `fetch` reads the discovery file and downloads exactly the feed URLs it declares. Bodies are
  written byte for byte; `manifest.json` records per file the URL, HTTP status, content type,
  `Last-Modified`, size, SHA-256 and `collected_at` (UTC, when the body finished downloading).
- `profile` refuses a snapshot whose files do not match the manifest, then writes
  `profile.json`. It reads no clock: the same snapshot always gives the same bytes.
- Snapshot used here: `sample-data/gbfs/20261025T001551Z/` (English feeds, fetched on
  Saturday 2026-10-24 at 20:15 in Montréal). French feeds were not fetched.

**Definitions used by the profile.**

- Counting unit: one station record (one object of `data.stations`), unless stated otherwise.
- Reference time R: `collected_at` of `station_status`. Ages are `R - last_reported`, in seconds.
- Integer: a JSON integer, not a boolean and not a float (`3.0` is not an integer).
- Valid id: `station_id` is a non-empty string. Join: valid ids that appear once in each feed;
  an id repeated in either feed is left out of the join.
- Coordinate classes, first match wins: non-numeric (absent, boolean or not a number),
  non-finite, out of range (|lat| > 90 or |lon| > 180), exactly (0, 0), valid.
- `last_reported` is invalid when absent, null, not an integer, before 946684800
  (2000-01-01T00:00:00Z) or more than 300 s after R. The 2000-01-01 floor is an application
  cutoff, not a GBFS rule. Values between R and R + 300 s are valid (tolerated clock skew).
- Median: `statistics.median`. p90: nearest rank, the value at position ceil(0.9 n) of the
  sorted ages.
- Distances: geodesic on the WGS84 ellipsoid (geographiclib) from the component-wise median of
  the valid coordinates.
- Alert active at R: one `times` entry with `start <= R` and an `end` that is absent, null or
  `>= R`.

**External observation, not independently verified.** The MobilityDatabase GBFS v2.2
validation report of 2026-10-07 for this feed lists schema errors on the integer flags, the
`rental_methods` values and the alert types (see `docs/data-source-audit.md`). Our own
measurements are in the table below.

## 2. Figures

Each row gives a key of `profile.json` (RFC 6901 JSON Pointer) and its exact value for the
snapshot named below. `tests/audit/test_snapshot.py` recomputes the profile and checks every row.
The findings in section 3 summarise these rows in words; only the table is checked.

<!-- snapshot: sample-data/gbfs/20261025T001551Z -->
<!-- figures:begin -->
| Profile key | Value | Meaning |
|---|---|---|
| `/reference_time` | `"2026-10-25T00:15:51Z"` | Reference time R: fetch time of `station_status` (UTC) |
| `/discovery/languages` | `["en","fr"]` | Languages declared by the discovery file |
| `/files/gbfs.json/ttl` | `10` | `ttl` of gbfs.json, seconds |
| `/files/en~1station_information.json/ttl` | `10` | `ttl` of en/station_information.json, seconds |
| `/files/en~1station_status.json/ttl` | `10` | `ttl` of en/station_status.json, seconds |
| `/files/en~1system_alerts.json/ttl` | `10` | `ttl` of en/system_alerts.json, seconds |
| `/files/en~1system_information.json/ttl` | `10` | `ttl` of en/system_information.json, seconds |
| `/files/gbfs.json/has_version` | `false` | gbfs.json has a `version` field |
| `/files/en~1station_information.json/has_version` | `false` | en/station_information.json has a `version` field |
| `/files/en~1station_status.json/has_version` | `false` | en/station_status.json has a `version` field |
| `/files/en~1system_alerts.json/has_version` | `false` | en/system_alerts.json has a `version` field |
| `/files/en~1system_information.json/has_version` | `false` | en/system_information.json has a `version` field |
| `/stations/information_records` | `1121` | Station records in `station_information` |
| `/stations/status_records` | `1121` | Station records in `station_status` |
| `/stations/information_invalid_ids` | `0` | Information records whose `station_id` is not a non-empty string |
| `/stations/status_invalid_ids` | `0` | Status records whose `station_id` is not a non-empty string |
| `/stations/information_duplicate_ids` | `0` | Distinct ids seen more than once in `station_information` |
| `/stations/status_duplicate_ids` | `0` | Distinct ids seen more than once in `station_status` |
| `/stations/join` | `{"both":1121,"information_only":0,"status_only":0}` | Join of unique valid ids: in both feeds, information only, status only |
| `/fields/station_status/is_installed/types` | `{"int":1121}` | JSON types of `is_installed` (status records) |
| `/fields/station_status/is_renting/types` | `{"int":1121}` | JSON types of `is_renting` (status records) |
| `/fields/station_status/is_returning/types` | `{"int":1121}` | JSON types of `is_returning` (status records) |
| `/flags/combinations` | `{"0,1,1":5,"1,1,1":1116}` | Status records per (installed, renting, returning), 0/1 after normalisation |
| `/rental_methods/values` | `{"CREDITCARD":1121,"KEY":1121}` | Information records listing each `rental_methods` value |
| `/coordinates/valid` | `1117` | Information records with valid coordinates |
| `/coordinates/zero_zero` | `4` | Information records at exactly (0, 0) |
| `/coordinates/out_of_range` | `0` | Information records with latitude beyond ±90 or longitude beyond ±180 |
| `/coordinates/non_numeric` | `0` | Information records with absent or non-numeric coordinates |
| `/coordinates/non_finite` | `0` | Information records with non-finite coordinates |
| `/coordinates/zero_zero_stations` | `[{"capacity":0,"last_reported_invalid":true,"station_id":"304"},{"capacity":0,"last_reported_invalid":true,"station_id":"36"},{"capacity":23,"last_reported_invalid":false,"station_id":"724"},{"capacity":0,"last_reported_invalid":true,"station_id":"747"}]` | Stations at (0, 0): capacity and whether `last_reported` is invalid |
| `/capacity/population` | `1121` | Joined stations whose capacity and four `num_*` counts are integers >= 0 |
| `/capacity/excluded` | `0` | Joined stations left out of the capacity checks |
| `/capacity/equals_bikes_docks_disabled` | `1121` | Population where capacity = bikes + docks + disabled bikes + disabled docks |
| `/capacity/equals_bikes_docks` | `713` | Population where capacity = bikes + docks available |
| `/last_reported/records` | `1121` | Status records checked |
| `/last_reported/invalid` | `3` | Status records with an invalid `last_reported` |
| `/last_reported/invalid_values` | `{"86400":3}` | Invalid `last_reported` values and their counts |
| `/last_reported/future_tolerated` | `0` | Valid values after R (within 300 s) |
| `/last_reported/age_seconds` | `{"max":3135522,"median":634.5,"min":2,"n":1118,"p90":2308}` | Ages R - `last_reported` over valid values, seconds (n, min, median, nearest-rank p90, max) |
| `/last_reported/older_than` | `{"2592000":1,"3600":63,"86400":2}` | Valid status records older than 1 h, 24 h and 30 days (seconds, strict) |
| `/remote_stations/origin` | `{"lat":45.52300306499666,"lon":-73.58193688100073}` | Component-wise median of the valid coordinates |
| `/remote_stations/buckets_km` | `{"0-10":876,"10-50":216,"100+":25,"50-100":0}` | Valid stations per geodesic distance from the origin, km |
| `/remote_stations/beyond_50km` | `{"count":25,"lat_max":45.41233566650505,"lat_min":45.379756292248274,"lon_max":-71.87262399125757,"lon_min":-71.92877769470215}` | Stations more than 50 km from the origin and their bounds |
| `/alerts/records` | `51` | Alerts in `system_alerts` |
| `/alerts/with_times` | `51` | Alerts with a non-empty `times` list |
| `/alerts/active_at_reference` | `0` | Alerts active at R |
| `/alerts/with_station_ids` | `0` | Alerts with a non-empty `station_ids` list |
| `/alerts/with_region_ids` | `0` | Alerts with a non-empty `region_ids` list |
| `/alerts/types` | `{"OTHER":39,"SYSTEM_CLOSURE":12}` | Alerts per `type` |
| `/alerts/summaries_by_type/SYSTEM_CLOSURE` | `["Early network opening","Good news — more BIXIs are coming !","Information","Network Info","New Amis BIXI Reward"]` | Distinct summaries of the `SYSTEM_CLOSURE` alerts |
<!-- figures:end -->

## 3. Findings

- The discovery file and every English feed have no `version` field; all declare `ttl` 10.
- Information and status hold the same stations: no invalid or duplicate id, and the join
  leaves nothing on either side.
- `is_installed`, `is_renting` and `is_returning` are integers 0/1 in every record, never
  booleans as GBFS asks. Some stations are not installed but renting and returning.
- `rental_methods` values are uppercase strings, not the lowercase GBFS values.
- Four stations sit at exactly (0, 0). Most of them have capacity 0 and an invalid
  `last_reported`; all invalid values are the same sentinel, 86400 (1970-01-02).
- `capacity` equals bikes + docks + disabled bikes + disabled docks for every comparable
  station, but bikes + docks alone for far fewer: capacity includes disabled equipment.
- Ages since `last_reported` range from seconds to more than 30 days; the age rows give the
  median, the p90 and the counts above 1 h, 24 h and 30 days.
- A group of stations lies more than 100 km from the median position (bounds in the
  `/remote_stations/beyond_50km` row). Those bounds fall in the Sherbrooke area; that reading
  is ours, not confirmed by BIXI.
- `system_alerts`: every alert has `times`, none is active at R and none targets stations or
  regions. Alerts typed `SYSTEM_CLOSURE` include messages that are not closures.

## 4. Rules

Each rule is a testable statement. "Now" names what the audit tests check today; "Later"
names the issue whose code must test the rule itself.

**DQ-1 Missing status is not zero.** A station in `station_information` without a status
record, or with an invalid status (DQ-3, DQ-6, DQ-9), has null bikes, docks and occupancy and
the flag `status_missing` or `status_invalid`. Never 0.
Now: join counts (`information_only`). Later: #4.

**DQ-2 Status without information.** A status record whose id is not in
`station_information` is kept raw, not displayed (no coordinates), and flagged
`information_missing`. Now: join counts (`status_only`). Later: #4.

**DQ-3 Flags.** `true`/`false` are accepted. Integers 0 and 1 are coerced to booleans and
flagged `flag_coerced`. Any other value (string, other integer, float, null, absent) makes the
status invalid. Now: flag value keys and combinations. Later: #4.

**DQ-4 Out of service.** A station is out of service when `is_installed`, `is_renting` or
`is_returning` is false. Its counts are still shown (GBFS counts bikes even when not renting),
but it is left out of both risk counts (few bikes, few docks). It is not a shortage.
Now: flag combinations. Later: #4, #7.

**DQ-5 Invalid coordinates are quarantined.** Non-numeric, non-finite, out-of-range or exactly
(0, 0) coordinates: the station is kept and counted, not drawn on the map, not used in spatial
filters, and flagged `coordinates_invalid`. There is no service-area box: stations outside
Montréal are legitimate. Now: coordinate classes. Later: #4, #9.

**DQ-6 Sentinel and invalid `last_reported`.** `last_reported` must be an integer between
2000-01-01T00:00:00Z and `collected_at` + 5 minutes. Otherwise `observed_at` is null and the
status is invalid (DQ-1). The floor catches the 86400 sentinel and unknown ones; values in the
5-minute future window are kept and counted. Now: validity boundaries and the list of invalid
values. Later: #4.

**DQ-7 Stale threshold.** A valid status with `collected_at - last_reported` above 60 minutes
is `stale`: shown with a marker, counted in the freshness indicator, and left out of both risk
counts. The threshold is configurable. Now: the count of ages above 3600 s. Later: #4, #7.

**DQ-8 Occupancy denominator.** Occupancy = bikes available / (bikes available + docks
available); null when that sum is 0 or the status is invalid. `capacity` is never the
denominator: it includes disabled bikes and docks. Now: the capacity identities. Later: #4.

**DQ-9 Counts.** Every `num_*` field must be an integer >= 0. Otherwise that field is null and
flagged; a null bikes or docks count makes the status invalid. Now: capacity population (only
stations whose five operands are integers >= 0). Later: #4.

**DQ-10 Station ids.** `station_id` must be a non-empty string, unique within one feed. Every
record whose id is repeated is flagged `duplicate_id` and none of them is used.
Now: invalid and duplicate id counts, join. Later: #4.

**DQ-11 Observation idempotency key.** An observation is identified by (system_id, station_id,
last_reported). A repeated fetch with the same key adds nothing. The same key with any
difference in counts or flags keeps the first observation and flags `conflicting_duplicate`;
both raw payloads stay available. A status with an invalid `last_reported` creates no
observation; its raw payload is still kept. Limitation: keep-first is a provisional choice.
Now: nothing (no observation store yet). Later: #5, #6.

**DQ-12 Alerts.** The alert `type` drives no logic: `SYSTEM_CLOSURE` is used for messages that
are not closures. Alerts are shown as text with their `times`. Now: alert counts, types and
summaries. Later: #4, #15.

**DQ-13 Raw first.** Every fetched body is kept with its SHA-256 and `collected_at` before any
normalisation; unknown fields are kept. Now: manifest and SHA checks. Later: #6.

**DQ-14 No hardcoded feed path.** Feed URLs are read from the discovery file at request time.
Now: a discovery file with unusual URLs is followed exactly. Later: #4, #6.

**DQ-15 Source time is not fetch time.** `last_reported` and the feed `last_updated` are kept
apart from `collected_at`; any forecast carries its own `forecast_issued_at`.
Now: separate manifest and profile fields. Later: #5.

## 5. Open questions

- **Stability across snapshots.** One snapshot is one moment (Saturday evening, late October).
  Do counts change while `last_reported` stays the same (DQ-11)? Two fetches a few minutes apart
  would show it.
- **Winter coverage.** BIXI closes most stations in winter; which stations stay in the feed,
  and with which flags?
- **Municipality boundaries.** Which source assigns stations to municipalities (#10)?
- **Root vs `/2-2/` endpoint.** The Montréal catalogue points to the root discovery file, which
  declares no version; `/gbfs/2-2/gbfs.json` declares 2.2 and adds `vehicle_types`. Which one
  should the collector follow?
- **Not installed but renting.** What does BIXI mean by a station that is not installed but
  renting and returning? DQ-4 treats it as out of service until BIXI says otherwise.
- **Alerts without `times`.** None today; GBFS does not say what their absence means.
