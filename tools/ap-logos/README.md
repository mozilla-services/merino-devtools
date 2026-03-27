# ap-logos

Automated logo acquisition from AP Newsroom. Searches for entity logos (sports teams, airlines, Fortune 500 companies), uses Claude Vision to pick the correct image from search results, and downloads high-resolution PNGs. Generates GCS-compatible manifests for serving logos via a CDN.

## How it works

1. **Search** — queries the AP Newsroom API for each entity (e.g. "Boston Celtics logo"), filtering to graphics for sports teams
2. **Pre-filter** — for sports teams, narrows AP results to plausible logo candidates using caption heuristics (excludes faces, event graphics, old/retro versions)
3. **Reference match** — fetches the official logo from a configurable reference source (ESPN CDN by default) and uses Claude Vision (Sonnet) to find the AP image that best matches in design, shape, and color
4. **Download** — fetches the highest-resolution rendition (preferring PNG) via the AP media API, falling back to browser-based download

For non-sports entities (airlines, companies), the pipeline uses caption matching with a Vision fallback.

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

Optionally set `REFERENCE_SOURCE` to override the default reference logo source (e.g. `espn`).

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
ap-logos export
```

### Generate 200x200 thumbnails

Crop whitespace and resize logos to 200x200 with a white background:

```sh
# All sports logos
python convert_to_200.py

# Single logo
python convert_to_200.py --logo nba/nba_atl.png

# One league
python convert_to_200.py --league nhl

# Custom size
python convert_to_200.py --size 128
```

Output goes to `output/logos_200/` by default. Use `--input` and `--output` to override directories.

### Rebuild manifest

If the manifest gets out of sync with what's on disk (e.g. after multiple category runs), rebuild it:

```sh
ap-logos rebuild
```

This scans `output/logos/`, matches files to the entity CSV, and preserves any existing AP metadata (item IDs, titles, vision confidence).

### Export GCS manifests

Generate the two JSON manifests consumed by the Merino Manifest Provider:

```sh
ap-logos export
ap-logos export --cdn-base https://custom-cdn.example.com  # override default
```

Default CDN base: `https://storage.googleapis.com/merino-images-prod`

Produces:

- `output/logo_manifest_latest.json` — maps `logo_id` to CDN URL + AP metadata
- `output/logo_lookup_manifest_latest.json` — maps entity abbreviations to `logo_id`

Logo IDs are deterministic (`uuid5(NAMESPACE_URL, "{category}/{abbreviation}")`), so re-running export produces identical IDs.

### Check status

```sh
ap-logos status
```

## Reference sources

For sports teams (NBA, NFL, NHL, MLB), the pipeline fetches a reference logo to verify AP results against. Reference sources are configured in `ap_logos/reference.py`:

```python
REFERENCE_SOURCES = {
    "nba": [{"name": "espn", "url": "https://a.espncdn.com/i/teamlogos/nba/500/{abbr}.png"}],
    "nfl": [{"name": "espn", "url": "https://a.espncdn.com/i/teamlogos/nfl/500/{abbr}.png"}],
    ...
}
```

To add a new source (e.g. Yahoo Sports, official league CDN), append an entry to the list for that league. Sources are tried in order.

## Project structure

```
ap_logos/
  auth.py        — Playwright-based AP Newsroom login, session management
  search.py      — AP Newsroom search API client with browser fallback
  vision.py      — Claude Vision integration for logo identification
  reference.py   — Configurable reference logo sources (ESPN CDN, etc.)
  downloader.py  — Logo download via API renditions or browser
  manifest.py    — Manifest I/O, rebuild, and GCS export
  main.py        — Typer CLI
  models.py      — Pydantic models (entities, search results, manifests)
data/
  entities.csv   — Entity definitions (category, name, abbreviation, search query)
convert_to_200.py — Crop and resize logos to 200x200 thumbnails
```

## Linting

```sh
ruff check ap_logos/
ruff format ap_logos/
```
