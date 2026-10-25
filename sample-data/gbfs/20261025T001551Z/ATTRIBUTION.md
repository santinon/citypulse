# Attribution

`gbfs.json` and the files in `en/` are an unmodified copy of the BIXI Montréal GBFS feed,
fetched on 2026-10-25T00:15:51Z (UTC) from https://gbfs.velobixi.com/gbfs/gbfs.json with
`scripts/audit_gbfs.py fetch --lang en`. `manifest.json` (fetch metadata and SHA-256) and
`profile.json` (computed metrics) are produced by this project.

- Source: BIXI Montréal, dataset "État des stations",
  https://donnees.montreal.ca/en/dataset/89fdc53c-cf70-485f-ab1a-9b10044d9f15
- Licence: Creative Commons Attribution 4.0 International (CC BY 4.0),
  https://creativecommons.org/licenses/by/4.0/ (checked on 2026-10-24 in the Montréal
  open-data catalogue API).
- Changes: none. The files are byte-for-byte copies of the server responses.

This project is not affiliated with or endorsed by BIXI Montréal. The licence covers the data,
not BIXI trademarks or logos.
