# Terrier district test sets (2026-10-21)

The repository owner identifies these as synthetic test data for CO-05, OR-05 and CT-01. This classification is owner-declared and has not been independently verified. Original source filenames, schema metadata and source fingerprints are preserved; their existing Statara import provenance is not rewritten or presented as proof of synthetic generation.

The release catalogue covers 262 unique dataset files: the 18 approved runtime-role references (contacts, roads, scores, street, walk and density), three source CSVs, 233 snapshot/cache files and the 11 supplemental-role references. Three density files occur in both role lists and are included once. Snapshot files retain the geographic/profile summaries, indices and generated v2/v3 caches present in the reviewed inventory. Supplemental files include area-score tables, their manifests, density, quality diagnostics and reviewed address corrections.

`manifest.json` gives exact archive and per-file byte sizes and SHA-256 checksums. District archives use ZIP_DEFLATED level 1 and ZIP64, deterministic member order and timestamps, and paths beginning `co-05/`, `or-05/` or `ct-01/`. Source CSVs live under each district's `sources/`; runtime/snapshot/supplemental files live under `data/`. All members were checked against their source SHA-256 and ZIP CRC. If a district needs more than one part, extract every numbered part into the same target directory. No source file was moved, modified or deleted.

The separate Census geography archive contains 48 exact public-source geometry/receipt inputs plus its README and manifest. Its source is the U.S. Census Bureau, TIGER/Line 2025 and 2020 ZCTA geography from TIGERweb. `geography-manifest.json` and its included source receipts retain the credits and original download URLs.

- [Census TIGER/Line](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html)
- [Census TIGERweb ZCTA service](https://tigerweb.geo.census.gov/arcgis/rest/services/Census2020/tigerWMS_Census2020/MapServer/84)

Accounts, access approvals, credentials, secrets, salts, sessions, Chromium state, logs, audit, locks, process files, migration state, backups and mirrors are excluded. These test-set archives are separate from application installers and do not change update discovery or install district data automatically.

Distribution target: a dedicated Geo data prerelease, marked not latest, using a tag outside `desktop-v*`. Every public asset must be smaller than 2 GiB; the large archives belong in release assets and only this small catalogue/documentation belongs in Git. Ordinary GitHub Git blobs are limited to 100 MiB.

- [GitHub large-file limits](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)
- [GitHub release-asset limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)

The dedicated Geo data release records publication separately from the original source files and application releases.

## Release downloads

[Geo data prerelease](https://github.com/pjg93-222/Geo/releases/tag/district-testsets-2026-10-21). It is separate from the stable Terrier installer release.

| Asset | Bytes | Layout |
| --- | ---: | --- |
| [terrier-testsets-co-05-2026-10-21.zip](https://github.com/pjg93-222/Geo/releases/download/district-testsets-2026-10-21/terrier-testsets-co-05-2026-10-21.zip) | 1,304,736,417 | co-05; `data/` and `sources/` under district root |
| [terrier-testsets-or-05-2026-10-21.zip](https://github.com/pjg93-222/Geo/releases/download/district-testsets-2026-10-21/terrier-testsets-or-05-2026-10-21.zip) | 1,576,961,706 | or-05; `data/` and `sources/` under district root |
| [terrier-testsets-ct-01-2026-10-21.zip](https://github.com/pjg93-222/Geo/releases/download/district-testsets-2026-10-21/terrier-testsets-ct-01-2026-10-21.zip) | 1,188,416,354 | ct-01; `data/` and `sources/` under district root |
| [terrier-district-geography-2026-10-21.zip](https://github.com/pjg93-222/Geo/releases/download/district-testsets-2026-10-21/terrier-district-geography-2026-10-21.zip) | 1,007,926,127 | Colorado at root; Oregon in `or-05/`, Connecticut in `ct-01/` |
| [manifest.json](https://github.com/pjg93-222/Geo/releases/download/district-testsets-2026-10-21/manifest.json) | Catalogue | Manifest, credits or checksums |
| [geography-manifest.json](https://github.com/pjg93-222/Geo/releases/download/district-testsets-2026-10-21/geography-manifest.json) | Catalogue | Manifest, credits or checksums |
| [README.md](https://github.com/pjg93-222/Geo/releases/download/district-testsets-2026-10-21/README.md) | Catalogue | Manifest, credits or checksums |
| [SHA256SUMS](https://github.com/pjg93-222/Geo/releases/download/district-testsets-2026-10-21/SHA256SUMS) | Catalogue | Manifest, credits or checksums |

Every district catalogue includes `contacts.sqlite3`, `roads.sqlite3`, `scores.sqlite3`, `street.sqlite3`, `walk.sqlite3` and `density.json`; `manifest.json` maps each required role to its exact archive member. No account database is included.
