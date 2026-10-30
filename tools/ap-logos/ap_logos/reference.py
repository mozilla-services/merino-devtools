from __future__ import annotations

import os

import httpx
from rich.console import Console

console = Console()

# Wikimedia rejects requests with generic client User-Agents (e.g. httpx's
# default) and asks for one that identifies the tool.
# See https://meta.wikimedia.org/wiki/User-Agent_policy
USER_AGENT = "merino-ap-logos/0.1 (https://github.com/mozilla-services/merino-devtools)"

_WIKIMEDIA_UPLOAD_PREFIX = "https://upload.wikimedia.org/wikipedia/"

# Ordered reference sources per league. Each source is tried in order until
# one succeeds.  Add new sources (Yahoo, official league CDNs, etc.) by
# appending entries here.
REFERENCE_SOURCES: dict[str, list[dict[str, str]]] = {
    "nba": [
        {"name": "espn", "url": "https://a.espncdn.com/i/teamlogos/nba/500/{abbr}.png"},
    ],
    "nfl": [
        {"name": "espn", "url": "https://a.espncdn.com/i/teamlogos/nfl/500/{abbr}.png"},
    ],
    "nhl": [
        {"name": "espn", "url": "https://a.espncdn.com/i/teamlogos/nhl/500/{abbr}.png"},
    ],
    "mlb": [
        {"name": "espn", "url": "https://a.espncdn.com/i/teamlogos/mlb/500/{abbr}.png"},
    ],
}


def wikimedia_png_url(url: str, width: int = 500) -> str:
    """Return a raster URL for a Wikimedia-hosted image.

    Claude Vision does not accept SVG, so SVG originals are swapped for the PNG
    thumbnail Wikimedia renders for them. Query strings are dropped. Other URLs
    are returned unchanged.
    """
    url = url.split("?", 1)[0]
    if (
        not url.startswith(_WIKIMEDIA_UPLOAD_PREFIX)
        or "/thumb/" in url
        or not url.lower().endswith(".svg")
    ):
        return url
    project, path = url[len(_WIKIMEDIA_UPLOAD_PREFIX) :].split("/", 1)
    filename = path.rsplit("/", 1)[-1]
    return f"{_WIKIMEDIA_UPLOAD_PREFIX}{project}/thumb/{path}/{width}px-{filename}.png"


async def fetch_reference_logo(
    category: str,
    abbreviation: str,
    *,
    source_override: str | None = None,
    reference_url: str = "",
) -> tuple[bytes | None, str]:
    """Fetch a reference logo image from configured sources.

    If the entity has its own ``reference_url`` it is tried first, followed by
    the sources configured for the category. Set the ``REFERENCE_SOURCE``
    environment variable to a source name (e.g. "espn") to force a specific
    configured source.

    Returns ``(image_bytes, source_name)`` on success or ``(None, "")`` on
    failure.
    """
    league = category.lower()
    sources = REFERENCE_SOURCES.get(league, [])

    preferred = source_override or os.getenv("REFERENCE_SOURCE", "")

    # If a specific source is requested, try it first
    if preferred:
        sources = sorted(sources, key=lambda s: s["name"] != preferred.lower())

    urls = [
        (source["name"], source["url"].format(abbr=abbreviation.lower())) for source in sources
    ]
    if reference_url:
        name = "wikipedia" if reference_url.startswith(_WIKIMEDIA_UPLOAD_PREFIX) else "entity"
        urls.insert(0, (name, wikimedia_png_url(reference_url)))

    if not urls:
        return None, ""

    async with httpx.AsyncClient(
        timeout=10, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        for name, url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.content) > 500:
                    console.print(f"  [dim]Fetched reference logo from {name}[/dim]")
                    return resp.content, name
                console.print(
                    f"  [dim]Reference fetch failed ({name}): HTTP {resp.status_code}[/dim]"
                )
            except httpx.HTTPError as e:
                console.print(f"  [dim]Reference fetch failed ({name}): {e}[/dim]")

    return None, ""
