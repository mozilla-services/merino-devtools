from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

from .models import Category, Entity

console = Console()

SPORTSDATA_BASE_URL = "https://api.sportsdata.io/v4/soccer/scores/json"
SPORTSDATA_API_KEY_HEADER = "Ocp-Apim-Subscription-Key"

# Cup rounds before "Round 1" are only contested by lower non-league clubs.
_EARLY_ROUND_MARKERS = ("Preliminary", "Qualifying")

_CSV_FIELDS = [
    "category",
    "name",
    "abbreviation",
    "search_query",
    "league",
    "notes",
    "reference_url",
]


def _get(client: httpx.Client, path: str) -> Any:
    resp = client.get(f"{SPORTSDATA_BASE_URL}/{path}")
    resp.raise_for_status()
    return resp.json()


def _current_season(competitions: list[dict[str, Any]], key: str) -> dict[str, Any]:
    for competition in competitions:
        if competition["Key"] == key:
            for season in competition.get("Seasons", []):
                if season.get("CurrentSeason"):
                    return season
            raise ValueError(f"No current season for competition {key}")
    raise ValueError(f"Unknown competition {key}")


def _scheduled_team_ids(games: list[dict[str, Any]], round_ids: set[int] | None) -> set[int]:
    ids: set[int] = set()
    for game in games:
        if round_ids is not None and game.get("RoundId") not in round_ids:
            continue
        for field in ("GlobalHomeTeamId", "GlobalAwayTeamId"):
            if game.get(field):
                ids.add(game[field])
    return ids


def fetch_soccer_entities(api_key: str, leagues: list[str], cups: list[str]) -> list[Entity]:
    """Build soccer entities from SportsDataIO competitions.

    Leagues contribute every club on their current-season schedule. Cups
    contribute clubs scheduled from Round 1 onward, skipping preliminary and
    qualifying rounds.

    SportsDataIO's soccer team keys are not unique (e.g. "BAR" is Barnsley,
    Barnet and Barrow), so the entity abbreviation is the club's GlobalTeamId
    and the SportsDataIO key is kept in ``notes``.
    """
    clubs: dict[int, dict[str, Any]] = {}
    competitions_by_club: dict[int, set[str]] = {}

    with httpx.Client(timeout=30, headers={SPORTSDATA_API_KEY_HEADER: api_key}) as client:
        competitions = _get(client, "Competitions")
        for key in [*leagues, *cups]:
            season = _current_season(competitions, key)
            round_ids = None
            if key in cups:
                round_ids = {
                    r["RoundId"]
                    for r in season.get("Rounds", [])
                    if not any(marker in r.get("Name", "") for marker in _EARLY_ROUND_MARKERS)
                }
            games = _get(client, f"SchedulesBasic/{key}/{season['Season']}")
            team_ids = _scheduled_team_ids(games, round_ids)
            for team in _get(client, f"Teams/{key}"):
                team_id = team.get("GlobalTeamId")
                if team_id in team_ids:
                    clubs[team_id] = team
                    competitions_by_club.setdefault(team_id, set()).add(key)
            console.print(f"  {key} {season['Name']}: {len(team_ids)} clubs")

    entities = [
        Entity(
            category=Category.SOCCER,
            name=team["Name"],
            abbreviation=str(team_id),
            search_query=f"{team['Name']} logo",
            league="/".join(sorted(competitions_by_club[team_id])),
            notes=team.get("Key") or "",
            reference_url=team.get("WikipediaLogoUrl") or "",
        )
        for team_id, team in clubs.items()
    ]
    return sorted(entities, key=lambda e: e.name)


def write_category_entities(path: Path, category: Category, entities: list[Entity]) -> None:
    """Replace all rows of ``category`` in the entities CSV with ``entities``.

    Rows for other categories are left byte-for-byte untouched, and the
    ``reference_url`` column is added to the header if it is missing.
    """
    lines = path.read_text().splitlines(keepends=True)
    header = lines[0].rstrip("\r\n")
    if "reference_url" not in header.split(","):
        header = f"{header},reference_url"
    kept = [line for line in lines[1:] if not line.startswith(f"{category.value},")]
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"

    with open(path, "w", newline="") as f:
        f.write(f"{header}\n")
        f.writelines(kept)
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, lineterminator="\n")
        for entity in entities:
            writer.writerow(entity.model_dump(mode="json"))
