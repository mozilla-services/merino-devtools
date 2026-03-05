# ap-logos

Automated logo acquisition from AP Newsroom. Searches for entity logos (sports teams, airlines, Fortune 500 companies), uses Claude Vision to pick the correct image from search results, and downloads high-resolution PNGs. Generates GCS-compatible manifests for serving logos via a CDN.

## How it works

1. **Search** — queries the AP Newsroom API for each entity (e.g. "Boston Celtics logo")
2. **Identify** — sends search result thumbnails to Claude Vision, which classifies each image and picks the best standalone logo
3. **Download** — fetches the highest-resolution rendition (preferring PNG) via the AP media API, falling back to browser-based download

The pipeline is driven by `data/entities.csv`, which lists ~640 entities across six categories: NBA, NFL, NHL, MLB, airlines, and Fortune 500 companies.

## Setup

Requires Python 3.11+ and a Chromium browser for Playwright.

```sh
pip install -e .
playwright install chromium
```

Create a `.env` file (see `.env.example`):

```
AP_USERNAME=your-ap-newsroom-email
AP_PASSWORD=your-ap-newsroom-password
ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

### Authenticate

```sh
ap-logos login              # headless
ap-logos login --headed     # watch the browser
```

Session is saved to `.session/` and reused automatically.

### Fetch logos

```sh
# All entities
ap-logos fetch

# Single category
ap-logos fetch --category nba

# Single entity (must specify category when abbreviation is ambiguous)
ap-logos fetch --category company --entity AAPL

# Preview what Vision would pick without downloading
ap-logos fetch --category nhl --dry-run
```

Logos land in `output/logos/{category}/{category}_{abbr}.png`. The fetch command is idempotent — it skips entities already in the manifest unless you pass `--force`.

### Re-fetch a bad logo

If a logo is wrong (e.g. Vision picked a player photo instead of the logo), re-fetch just that entity:

```sh
ap-logos fetch --category company --entity AAPL --force
```

`--force` tells fetch to ignore the manifest and re-download. This replaces the single file on disk without touching anything else. After fixing, rebuild the manifest:

```sh
ap-logos rebuild
ap-logos export --cdn-base https://cdn.merino.example.com
```

### Rebuild manifest

If the manifest gets out of sync with what's on disk (e.g. after multiple category runs), rebuild it:

```sh
ap-logos rebuild
```

This scans `output/logos/`, matches files to the entity CSV, and preserves any existing AP metadata (item IDs, titles, vision confidence).

### Export GCS manifests

Generate the two JSON manifests consumed by the Merino Manifest Provider:

```sh
ap-logos export --cdn-base https://cdn.merino.example.com
```

Produces:

- `output/logo_manifest_latest.json` — maps `logo_id` to CDN URL + AP metadata
- `output/logo_lookup_manifest_latest.json` — maps entity abbreviations to `logo_id`

Logo IDs are deterministic (`uuid5(NAMESPACE_URL, "{category}/{abbreviation}")`), so re-running export produces identical IDs.

### Check status

```sh
ap-logos status
```

## Project structure

```
ap_logos/
  auth.py        — Playwright-based AP Newsroom login, session management
  search.py      — AP Newsroom search API client with browser fallback
  vision.py      — Claude Vision integration for logo identification
  downloader.py  — Logo download via API renditions or browser
  manifest.py    — Manifest I/O, rebuild, and GCS export
  main.py        — Typer CLI
  models.py      — Pydantic models (entities, search results, manifests)
data/
  entities.csv   — Entity definitions (category, name, abbreviation, search query)
```

## Linting

```sh
ruff check ap_logos/
ruff format ap_logos/
```
