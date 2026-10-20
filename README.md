# CityPulse

Bike-share availability monitoring and analysis, starting with the official BIXI GBFS feed.
"CityPulse" is a working name.

**Status: pre-alpha.** The repository has just been bootstrapped; nothing runs yet.

## What the MVP will do

- A map of every station published in the BIXI GBFS feed, with bikes, docks,
  operating status and last report time.
- A dashboard: stations in service, bikes available, low-bike and low-dock stations
  (two separate risks), and data freshness.
- A station detail view with the history that was actually collected — nothing interpolated
  or invented.
- One municipal disruption layer (Montréal or Laval roadworks).
- Two ways to use it: a static snapshot demo on GitHub Pages, and the full stack
  (frontend, API, PostgreSQL) with `docker compose up --build`.

Out of scope for the MVP: demand forecasting, user accounts and payments.

## Roadmap

Work is tracked in [GitHub issues](../../issues) and [milestones](../../milestones).

## Data sources and attribution

Contains data from BIXI Montréal, published under the
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/) licence:

- GBFS discovery feed: https://gbfs.velobixi.com/gbfs/gbfs.json
- Catalogue entry: https://donnees.montreal.ca/en/dataset/89fdc53c-cf70-485f-ab1a-9b10044d9f15

This project is not affiliated with or endorsed by BIXI Montréal.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).

## Licence

Code is released under the [MIT licence](LICENSE). Data keeps the licence of its source.
