# CityPulse — Data-source due diligence and preprocessing specification

**Audit date:** 2026-10-19 · **Status:** desk research / source metadata verified; raw-file profiling still pending.  
**Purpose:** reproducible, legally reusable, temporally honest pipeline for operational BIXI dashboards and later spatial/time-series forecasting.

## How to read this audit

- **CONFIRMED** = directly documented in an official source or a checked catalogue / endpoint.
- **REPORTED** = documented in an independent validator or secondary study; rerun the test.
- **NOT VERIFIED** = raw downloadable file(s), all rows, specific field type, license of every subordinate resource, or long-term stability remain unchecked.
- Crucial distinction: **metadata accessible ≠ the CSV/GeoJSON fully downloaded and profiled**. Direct network requests to BIXI endpoints were not available from the research execution environment; use the developer computer for smoke tests.
- Save license text/URL, raw checksum, HTTP metadata and the exact fetch timestamp. Source portal's `last_modified` is not a substitute for the time an individual record first became publicly available.

## 1. BIXI real-time GBFS — stations / system alerts (P0)

**Official discovery URL:** https://gbfs.velobixi.com/gbfs/gbfs.json  
**Official station data catalogue:** https://donnees.montreal.ca/en/dataset/89fdc53c-cf70-485f-ab1a-9b10044d9f15  
**Third-party schema validator:** https://mobilitydatabase.org/feeds/gbfs/gbfs-Bixi_MTL  
**License:** CC BY 4.0 according to Montréal catalogue and MobilityDatabase; retain BIXI Montréal credit; inspect copyright/trademark separately.  
**Feeds actually listed in directly retrieved root discovery JSON:** `station_information`, `station_status`, `system_information`, `system_alerts` in EN and FR. The root JSON exposed `last_updated` and `ttl=10` during inspection. The municipality says refresh roughly every minute.  
**Discrepancy:** MobilityDatabase lists `/gbfs/2-2/gbfs.json` as auto-discovery, but that path was inaccessible to our web fetch while `/gbfs/gbfs.json` returned JSON. **Do not hardcode assumed `/2-2/` endpoints**; follow URLs declared by the working discovery feed.

**Data-quality alerts:** MobilityDatabase's 2026-10-07 v2.2 validation report contains **11,312 schema errors**. This count is errors, not stations; re-run validator and classify issues rather than assuming all data are unusable. **I retrieved the actual validator JSON** at https://storage.googleapis.com/mobilitydata-gbfs-snapshots-prod/gbfs-Bixi_MTL/2.2/report_summary_20261007005424.json . Breakdown: `station_information` 4,480 errors (rental_methods invalid enums, grouped 2,240 occurrences); `station_status` 6,724 (is_installed/is_renting/is_returning not booleans, grouped 1,120 occurrences each; 2 invalid last_reported timestamps); `system_alerts` 102 (type enum grouped 51); `system_information` 4 (bad purchase/license URL formats); `vehicle_types` 2 (invalid form_factor). These error counts can reflect multiple validator failures per field/record. A snapshot may contain around 1,120 status records as inferred from grouped field counts, but **this is not an independently verified current station count**. Test examples of integer flags 0/1 and mapping, plus invalid timestamps and unknown alert values. This validator snapshot is older than the current root discovery response and not proof that errors persist today. Direct web fetch for status returned JSON; full direct fetch of station_information was not confirmed. Official catalogue says stations can move during a season, and temporary drop-off stations are omitted.

**Mandatory profiling:** HTTP status, content type, JSON parse, GBFS version, TTL/last_updated, number of stations, field inventory, duplicate `station_id`, availability of information/status pair, numeric/boolean coercions, counts >= 0, sensible coordinate range, availability vs capacity, inactive/reported/installed flags and nulls. Map bike-type breakdowns only if genuinely present; test capacity vs bikes+docks before interpreting occupancy. Keep all source unknown fields in raw file.

**Canonical records:** `source_id`, `station_id`, `station_version_valid_from`, `lat`, `lon`, `name`, `capacity`, `vehicle_type_counts`, `available_bikes`, `available_docks`, `is_installed`, `is_renting`, `is_returning`, `source_updated_at`, `collected_at`, `quality_flags`.

**Handling:** preserve raw JSON per ingest batch; separate slowly-changing station dimensions from time-series observations; idempotent snapshot key `(station_id, source_updated_at)` or fallback `(station_id, collected_at_bucket)` with a clearly documented choice; mark stale source and missing status, never convert to zero automatically. When timestamps or status fields violate spec, quarantine or normalize with logged coercions. Keep municipality as *derived* spatial attribute, not a permanent property of a station code.

**Unresolved:** full current records, station count, actual schema problems and whether anomalies affect usable fields, historic station positioning, detailed per-field license.

## 2. BIXI trip history 2014–2026 (P0 for forecasting, P1 this sprint)

**Official archive:** https://bixi.com/fr/donnees-ouvertes  
**Official data dictionary:** https://www.donneesquebec.ca/recherche/dataset/vmtl-bixi-historique-des-deplacements  
**Independent harmonization notes:** https://vigou3.gitlab.io/en/bixi-donnees-ouvertes-uniformes/  
**License discrepancy:** official Montréal BIXI data catalogue lists CC BY 4.0, while the independent harmonization project states BY-SA 4.0 for its republished adapted files and describes BIXI as BY-SA. **Resolve this discrepancy from primary terms before redistribution/commercial activity**. Do not reuse the harmonized adaptation without respecting its own license.

**Coverage:** publication of annual archives from 2014 to 2026. Latest year may be partial. Official description excludes trips shorter than **1 minute** and longer than **2 hours**. Official dataset dictionary describes pre-2022 schema (`start_date`, `start_station_code`, `end_date`, `end_station_code`, `is_member`, `duration_sec`) and later schema (`STARTSTATIONNAME`, `STARTSTATIONLATITUDE`, `STARTSTATIONLONGITUDE`, `END...`, `STARTTIMEMS`, `ENDTIMEMS`, etc.). The official dictionary mistakenly describes both STARTTIMEMS and ENDTIMEMS as an end timestamp: treat as **documentation ambiguity** to verify in actual CSV. An independent academic harmonization notes changes in station identifiers/names around 2021.

**Risks:** station code re-use or replacement; evolving network footprint; station migrations; seasons and winter coverage evolving; time parsing and DST ambiguity in old local timestamps; Unix millisecond vs second precision; different member/e-bike attributes across years; zero/missing station IDs; trips crossing regions; duplicated trips; archive updates; selection bias from excluded durations. Missing user IDs prevent reconstructing customer-level demand; observed trips are constrained by availability (censoring), and do not record unmet requests or truck rebalancing.

**Profile every year/month:** ZIP members/file sizes, delimiters, encoding, BOM, decimal separator, schema, row count, min/max dates, coordinate completeness and bounding ranges, unique station names/IDs, durations, duplicate keys/rows, month holes, DST repeated hour, station coverage evolution. Use format-specific adaptors, record `source_year`, `raw_record_source`, archive SHA256, and test mapping manually against known sample rows. **Do not assume a common station ID joins all years.**

**ML canonical shape:** timestamps UTC plus original timezone string; start/end coords with spatial zone assigned under an explicit versioned grid; source year/version; optional original station identifiers; quality flags. Start with comparable recent years, extend backward after mappings are validated. Maintain station-level operational map and separate **zone-hour demand targets** for robust long-term training. Compare e.g. ~250/500/750 m cell sizes; station movements may cross cell boundaries, so verify stability rather than assuming every 300-m shift remains within one zone.

## 3. Montréal ongoing works / permits (P1)

**Catalogue:** https://www.donneesquebec.ca/recherche/dataset/vmtl-info-travaux  
**Resources:** main permits CSV, effects/impacts CSV, complementary JSON; municipality daily updates; CC BY 4.0; geography City of Montréal. Official fields include `id`, `permit_permitid`, `boroughid`, `currentstatus`, `duration_startdate`, `duration_enddate`, `reason_category`, `occupancyname`, weekday schedules.

**Main risks:** permits can be requested, cancelled, or amended and may not reflect actual physical obstruction. Multi-table relations can multiply events if joins are not keyed properly. Start/end permit times differ from actual work hours. Daily schedule restrictions/weekday active flags must be applied. A record may have general borough coverage rather than a precise line geometry. Current export is not necessarily a reproducible historical archive.

**Actions:** profile keys, status distribution, cancellations/duplicates; join permits-to-impacts by verified key; build effective active intervals including weekday masks; attach line/polygon when present; preserve date first observed, each revision and `source_asof`; treat planned ≠ confirmed work. ML counts only features known on prediction date.

## 4. Montréal real-time traffic disruptions (P1, independent source)

**Catalogue:** https://www.donneesquebec.ca/recherche/dataset/vmtl-entraves-circulation  
**Description:** real-time interruptions, TMDD source converted to CIFS for public distribution; official dictionary includes unique `id` and ISO8601 `starttime` among others; CC BY 4.0; municipality Montréal.

**Risks:** event may overlap Montréal works/permits and Quebec 511 (double counting); feed may report updates/removals; incomplete coverage; format conversions; 'realtime' does not mean retrospective historical archive.  
**Actions:** de-duplicate within source by ID/version, and across sources using time, location and type, retaining both attributions; normalize localized timestamps; record first-seen/last-seen and changes; do not infer zero disruptions from feed downtime.

## 5. Laval roadworks (P1)

**Official catalogue:** https://donneesquebec.ca/recherche/dataset/chantiers-routiers  
**Formats:** CSV, GeoJSON, KML, SHP; daily refresh; CC BY 4.0; WGS84 EPSG:4326; mostly populated in summer. Official fields: `WKT`, `NO_OBSTRUC`, `TYPE`, `STATUT`, `DATE_DEBUT`, `DATE_FIN`, `REMARQUE`, `NATURE_TRA`, `ALERTE511`.

**Risks:** sparse outside summer, planned versus realized works, line geometries vs point approximation, incomplete missing-end intervals, duplicate records with provincial 511.  
**Actions:** prefer GeoJSON geometry; validate MultiLineString/LineString shape and geometry coordinate order; preserve WKT and raw fields; overlay on nearby BIXI stations with actual line-distance buffer, not only centroid. Keep sourceID and first-seen date. Map `STATUT` using actually observed values.

## 6. Québec 511 / provincial MTMD works (P2)

**Catalogue:** https://www.donneesquebec.ca/recherche/dataset/travaux-routiers  
**Formats:** CSV, GeoJSON, SHP, GPKG, WFS, WMS, marked real-time; provincial network *under MTMD responsibility*; CC BY 4.0; WGS84.  
**Coverage warning:** provincial roads only, not every local side street. Geometry is primarily linear.  
**Actions:** clip against each BIXI municipality plus buffer; WFS pagination and query limits; normalize road segment geometry; dedup 511 alert links in Laval; preserve status and update/validity timestamps. Do not conflate a file link's last modified date with data freshness.

## 7. Montréal public events (P2 ML)

**Official catalogue:** https://www.donneesquebec.ca/recherche/dataset/vmtl-evenements-publics  
**Coverage:** public Montréal city calendar, from 2023 onward, daily, CSV / GeoJSON / SHP, CC BY 4.0. Fields include `titre`, `url_fiche`, `description`, `date_debut`, `date_fin`, `type_evenement`, `public_cible`, `emplacement`, `inscription`, `cout`, `arrondissement`, address, `lat`, `long` WGS84 and projected `X`, `Y` in MTM zone 8.  
**Critical warning:** **does not include all events occurring in Montréal**, only those published on the city's own calendar. Lack of entry ≠ no event. Attendance is not a guaranteed field. Some events are online, recurring, cancelled, moved, geocoded coarsely, or published after first planning.

**Actions:** exclude online-only from spatial features; disambiguate address and geometry; dedup repeating titles/dates; parse long-duration events carefully; archive revisions, first published/first seen; calculate spatial-temporal exposure per station or zone and lead/lag windows, not just number of events. Never insert an event into the 2019 training period solely because today's page refers retrospectively to it.

## 8. Montréal municipal notices and alerts (P2, important)

**Official catalogue:** https://www.donneesquebec.ca/recherche/dataset/vmtl-avis-alertes  
**Coverage:** 2023-12 onward on the catalogue, but downloadable *current feed contains only the **12 most recent weeks***; daily CSV / GeoJSON, CC BY 4.0. Fields `titre`, `date_debut`, `date_fin` (documented as text), categorical `type` (e.g., Circulation et transport), `service_publieur`, `lien`, `Lat`, `Long`, `X`, `Y` in MTM 8 NAD83.

**Critical warning:** the beginning/end of *publication* may differ from the disruption's physical start/end dates. Data older than the rolling window need your own ongoing snapshots. Feed categories include water issues and parks, not all relevant to BIXI; protest coverage is not guaranteed.

**Actions:** archive daily diffs beginning now; filter relevant types and text; retain publication date, operational interval separately if extracted; structured extraction of street names/locations, with human QC; text extraction from official notice only if terms permit, and evaluate geocoding accuracy. For the forecasting model, use only alerts published before forecast issuance.

## 9. Montréal permanent bicycle counters (P2 ML)

**Official catalogue:** https://www.donneesquebec.ca/recherche/dataset/vmtl-cyclistes  
**Coverage:** from 2009, daily publishing, per-minute detector data with 15-min/hour/day/month/year aggregations; CC BY 4.0. 2026 Eco-Compteur/Ville resources and location table are listed. Distinct schema for permanent aggregation (`agg_code`, `instance`, `longitude`, `latitude`, `direction`, `periode`, `volume`, `vitesse`) versus historical annual files (`date`, `heure`, `id_compteur`, `nb_passage`, coords).

**Officially documented issues:** sensor/communications breakdowns, maintenance, equipment relocations, decommissioned sensors, some carbon bicycles not detected by older loops, potential anomalously elevated counts Dec 2019–Feb 2020, bike path/roadwork impacts, directional changes during COVID-era infrastructure. Counts measure **all bicycles**, not BIXI specifically.

**Actions:** compare coverage per site over time, distinguish missing periods from true zero, flag outliers, keep direction and aggregation level, do not sum 15m and 1h rows for the same time, time-align, map each sensor to nearby zones with distance decay; test whether additional covariate improves external-time holdout.

## 10. Vehicle / pedestrian / cyclist counts at traffic-light intersections (P3)

**Official catalogue:** https://www.donneesquebec.ca/recherche/dataset/vmtl-comptage-vehicules-pietons  
**Formats/coverage:** historical waves 2008 onward, 2023–2025 and rolling 2026–2028 resources, CSV and GeoJSON, Montréal's 19 boroughs, CC BY 4.0. Counts made at selected times of typical days primarily for traffic-signal compliance; categories occasionally omitted; cycle counts from 2009. Central-services counts do not include all borough counts.

**Critical warning:** this is not an uninterrupted live speed/congestion signal. Do not treat one intersection sampled in 2022 as current conditions or fill unobserved hours with zeros. Sampling bias by location/time is likely.

**Actions:** attach survey date, time windows, measurement direction, class of vehicle, and spatial geometry; use as long-term structural descriptor/traffic baseline, not short-horizon live signal until a suitable current feed is identified.

## 11. Segment travel times / congestion historical (P3)

**Official catalogue:** https://www.donneesquebec.ca/recherche/dataset/vmtl-temps-de-parcours-sur-des-segments-routiers-historique  
**Technology:** Bluetooth-derived travel times on selected segments. Official downloads for **2016–2019**; **2018 contains only Jan, Feb and Dec**; CSV files may be large, CC BY 4.0.  
**Critical warning:** obsolete for current operational congestion inference. Pre-COVID infrastructure/cycling behavior differs. Cannot naively merge 2018 missing 9 months into a 2026 model.

**Actions:** archive as research-only historic covariate; use only within overlapping target years when properly aligned; deprioritize for deployment.

## 12. Weather, historical and forecast (P2 ML)

**Preferred official source:** Environment and Climate Change Canada (ECCC) https://climate.weather.gc.ca/ ; hourly data via https://api.weather.gc.ca/collections/climate-hourly/items/ (OGC API Features), also historical CSV downloads.  
**Variables:** temperature, precipitation, humidity, wind speed/direction, station/time flags; check individual station/period coverage and units. **Do not invent a uniform complete weather record for all municipalities/years**. Station moves, gaps, trace precipitation, accumulated rainfall and recording periods matter.

**Fundamental ML concern:** weather *observed after issue time* cannot be a feature for a real-time future prediction. Build two pipelines: archived actual observations for analysis, and forecasts issued as of time `t` for operational inference/backtests.

**Alternative:** Open-Meteo free historical and forecast API for noncommercial demo, https://open-meteo.com/en/terms and https://open-meteo.com/en/docs/historical-forecast-api . Its **free API is non-commercial only**, and reanalysis data are not the same as issued forecasts. For monetization choose ECCC's permitted official datasets or an appropriately licensed paid weather feed; check ECCC reuse licence before launch.

**Actions:** save station metadata and distances; units; issued_at vs valid_at; observation source vs forecast model source; horizon availability and missing flags; compare daily/weekly seasonal effects.

## 13. Holidays, shops and school calendars (P2 ML, cheap)

**Public holidays:** CNESST https://www.cnesst.gouv.qc.ca/fr/conditions-travail/conges/jours-feries/liste-jours-feries . Quebec has 8 statutory holidays and some exceptions; Good Friday **or** Easter Monday depends on employer.  
**Shop openings:** Quebec official https://www.quebec.ca/entreprises-et-travailleurs-autonomes/commerce/ouverture-commerces/jours-feries ; retail closures differ by holiday and category, so `is_holiday` is **not** interchangeable with `stores_closed`.
**School calendar:** CSSDM https://www.cssdm.gouv.qc.ca/admission-inscription/calendriers-scolaires/ . Its published system calendar excludes 12 pedagogical days set by each school. School boards differ across cities and historically.

**Actions:** deterministic calendaring with sourced per-year lists; separate `is_qc_statutory_holiday`, `retail_restriction_class`, `is_school_break_board`, `is_weekend`, `days_to_holiday`, `school_year`. Don't assume school-day flag means all local schools are open.

## 14. Social and event-platform signals (P4 exploratory)

**Bluesky:** https://docs.bsky.app/docs/api/app-bsky-feed-get-feed — most `app.bsky.*` public GET endpoints via https://public.api.bsky.app, public post search exists, but geographic location of users/posts is mostly absent or unreliable, search coverage and service limitations need field tests. No blanket permission inferred for storage/commercial ML use from public visibility.

**Mastodon:** https://docs.joinmastodon.org/methods/search/ — full-text search depends on the server's search backend and authentication; federated and incomplete coverage. Posts are not authoritative street-closure notices.

**Reddit:** https://redditinc.com/policies/data-api-terms and https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data — commercial API use and model training require approval/permissions, user copyright matters. **Do not ingest Reddit posts into ML without authorization**.

**Ticketmaster:** https://developer.ticketmaster.com/products-and-docs/apis/discovery/ — developer key required; published quota 5,000 calls/day; differing official pages specify 2 or 5 requests/second, so use conservative throttling and read current account quota. Commercial retention/display/training rights require separate terms review. Venue coordinates and event dates can be useful; announced attendance is not universally supplied.

**Recommendation:** do not build on these for sprint 1. Start with official Montréal events and alerts, keep external provider connectors optional and separated by licence/retention policy.

## 15. Cartographic basemap, geometry and jurisdiction (P0 for frontend)

**OpenStreetMap data:** https://www.openstreetmap.org/copyright ; use Leaflet https://leafletjs.com/ ; OSM Foundation standard tile policy https://operations.osmfoundation.org/policies/tiles/ . OSM data may be reused with required attribution; its **standard public tile server is not a free commercial SLA**, prohibits bulk prefetch/offline export, requires normal cache & referer/identification practices.

**Spatial coordinate system:** all canonical lat/lon WGS84; keep source projection and geometry; projected MTM 8 NAD83 coordinates in city events/alerts must be explicitly transformed before distance calculations. Calculate metres in local projected CRS or using geodesic distances; don't treat raw lat/lon differences as metres.

**Spatial ML:** maintain a versioned geometry layer: `zone_id`, `grid_version`, polygon, valid_from; choose stable grid (e.g. H3 or locally projected tessellation), calculate demand by grid/time even as individual stations move. A shift across a hex boundary is possible: quantify sensitivity over 250m/500m/750m cells. Station exact points for UI, zone densities for historical ML, with clear units (bikes available, bikes/km², starts/hour/zone; not interchangeable).

## 16. Canonical schemas and time-aware data engineering

### Tables (suggested)
- `data_source_registry`: source, URL, license, last audit, access restrictions, update expected.
- `ingest_run`: source, requested_at, completed_at, http_status, sha256, row_count, schema_fingerprint, error.
- `raw_payload`: source ID, batch_id, storage reference / hash, received UTC.
- `station_dimension_scd2`: provider, station_id, valid_from/to, station attributes, geometry, capacity.
- `station_observation`: station_id, observed_at, retrieved_at, available_bikes/docks, operational flags, quality.
- `trip`: canonical timestamps & coordinates, source_archive, source_schema_version, zones, QC.
- `municipal_disruption`: original ID/source, status, geometry, operational interval, observed/published timestamps.
- `external_event`: venue/coordinates, event interval, first published/seen, type/category, confidence.
- `weather_observation` and `weather_forecast`: forecast issue time and valid time must be separate.
- `zone`: versioned polygon definitions + crosswalk.

### Quality gates, run on *every ingestion*
1. Transport/auth: HTTP status, redirects, timeout, retries/backoff, ETag/Last-Modified, rate limits, content-type.
2. Raw durability: immutable input + SHA256 + source/ref + collected timestamp; no credentials in repository.
3. Structural: schema registry per source version, encoding/BOM, columns, types and unknown fields.
4. Semantic: timestamps, finite/nonnegative counts, valid coordinates, enum flags, capacity consistency, non-empty IDs.
5. Temporal: UTC normalize, America/Toronto DST audit, freshness SLA, `published_at <= forecast_issue_time`, date ranges.
6. Spatial: CRS, geometry validity, border/out-of-city, station movements, zone versions, missing municipality.
7. Coverage: per source/year/week/municipality; completeness heatmap; avoid zero-filling missing source data.
8. Dedup: stable provider keys, logical collision and idempotent upsert, cross-source overlapping event identity.
9. Training: time holdout, station/network evolution, no forward-filled labels across long gaps; causality-safe feature windows, reproducible splits and baselines.
10. Monitoring: dashboard freshness badge, telemetry ingestion status and retained failure samples.

### First batch of automated tests (strongly recommended)
- A GBFS station information entry without status is *missing*, not zero bikes.
- An inactive station with zero available bikes is not counted as normal demand shortage.
- `2026-11-01 01:30` in Montréal can be ambiguous during DST fall-back; old local timestamps need explicit policy.
- A source outage does not yield artificial zero congestion/event counts.
- A roadwork line passes a zone even if its centroid lies outside it.
- Duplicated permit updates do not multiply distinct roadwork counts.
- An event announced *after* a target date never appears in training features as-of that prior date.
- A historic station relocated across a cell boundary is recorded with correct validity interval.
- Weather backtest uses a forecast previously issued, not today's retrospective observations masquerading as a past forecast.
- Every displayed map layer has license attribution and `data_as_of`.

## 17. Sprint sequencing and outstanding evidence

**Now (before coding business logic):** verify root GBFS and full station feeds via local client; download one small historical BIXI ZIP or bounded sample and inspect column variants; fetch Montréal works and Laval works sample GeoJSON. Write machine-readable `sources.yml` and data-contract tests.

**MVP:** GBFS ingest + versioned station/location + operational dashboard + municipal overlay (Montréal and Laval) + daily snapshots + reproducible tests; provide snapshot frontend for GitHub Pages and local full stack.

**Post-MVP:** archive trip imports, weather/calandar, spatial demand aggregation, LightGBM baseline; add events, counters only when a real comparable history exists; keep road congestion and social signals exploratory.

**Outstanding unknowns:** full files and sample row profiling for each source; per-provider restrictions beyond dataset licence; exact historical station matching per year; true publisher first-release timestamps; exact historical spatial coverage (especially BIXI expansion). These must be marked as outstanding, not silently assumed resolved.

## Source index (official first)
1. BIXI discovery: https://gbfs.velobixi.com/gbfs/gbfs.json
2. BIXI archive: https://bixi.com/fr/donnees-ouvertes
3. BIXI trip schema: https://www.donneesquebec.ca/recherche/dataset/vmtl-bixi-historique-des-deplacements
4. BIXI station metadata: https://donnees.montreal.ca/en/dataset/89fdc53c-cf70-485f-ab1a-9b10044d9f15
5. MobilityData feed validation: https://mobilitydatabase.org/feeds/gbfs/gbfs-Bixi_MTL
6. Montréal works: https://www.donneesquebec.ca/recherche/dataset/vmtl-info-travaux
7. Montréal road restrictions: https://www.donneesquebec.ca/recherche/dataset/vmtl-entraves-circulation
8. Laval roadworks: https://donneesquebec.ca/recherche/dataset/chantiers-routiers
9. Québec provincial works: https://www.donneesquebec.ca/recherche/dataset/travaux-routiers
10. Montréal events: https://www.donneesquebec.ca/recherche/dataset/vmtl-evenements-publics
11. Montréal alerts: https://www.donneesquebec.ca/recherche/dataset/vmtl-avis-alertes
12. Cycling counters: https://www.donneesquebec.ca/recherche/dataset/vmtl-cyclistes
13. Intersection counts: https://www.donneesquebec.ca/recherche/dataset/vmtl-comptage-vehicules-pietons
14. Segment travel times: https://www.donneesquebec.ca/recherche/dataset/vmtl-temps-de-parcours-sur-des-segments-routiers-historique
15. Weather ECCC: https://climate.weather.gc.ca/ ; https://api.weather.gc.ca/collections/climate-hourly/items/
16. Holidays: https://www.cnesst.gouv.qc.ca/fr/conditions-travail/conges/jours-feries/liste-jours-feries
17. Retail opening: https://www.quebec.ca/entreprises-et-travailleurs-autonomes/commerce/ouverture-commerces/jours-feries
18. School calendar: https://www.cssdm.gouv.qc.ca/admission-inscription/calendriers-scolaires/
19. OSM tile policy: https://operations.osmfoundation.org/policies/tiles/
20. Open-Meteo terms: https://open-meteo.com/en/terms
21. Bluesky docs: https://docs.bsky.app/docs/api/app-bsky-feed-get-feed
22. Mastodon: https://docs.joinmastodon.org/methods/search/
23. Reddit license: https://redditinc.com/policies/data-api-terms
24. Ticketmaster Discovery: https://developer.ticketmaster.com/products-and-docs/apis/discovery/

_Compiled as a source-aware research specification, not a certification of row-level source data. Revalidate each provider periodically; retain downloaded source evidence within the project._
