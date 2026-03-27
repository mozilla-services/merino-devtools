from __future__ import annotations

import os

import httpx
from rich.console import Console

console = Console()

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


async def fetch_reference_logo(
    category: str, abbreviation: str, *, source_override: str | None = None
) -> tuple[bytes | None, str]:
    """Fetch a reference logo image from configured sources.

    Tries each configured source in order and returns the first successful
    result.  Set the ``REFERENCE_SOURCE`` environment variable to a source
    name (e.g. "espn") to force a specific source.

    Returns ``(image_bytes, source_name)`` on success or ``(None, "")`` on
    failure.
    """
    league = category.lower()
    sources = REFERENCE_SOURCES.get(league)
    if not sources:
        return None, ""

    preferred = source_override or os.getenv("REFERENCE_SOURCE", "")

    # If a specific source is requested, try it first
    if preferred:
        sources = sorted(sources, key=lambda s: s["name"] != preferred.lower())

    for source in sources:
        url = source["url"].format(abbr=abbreviation.lower())
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.content) > 500:
                    console.print(
                        f"  [dim]Fetched reference logo from {source['name']}[/dim]"
                    )
                    return resp.content, source["name"]
        except httpx.HTTPError as e:
            console.print(
                f"  [dim]Reference fetch failed ({source['name']}): {e}[/dim]"
            )

    return None, ""
