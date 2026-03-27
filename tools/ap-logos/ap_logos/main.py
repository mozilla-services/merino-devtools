from __future__ import annotations

import asyncio
import csv
import os
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .auth import StorageState, ensure_session, get_httpx_cookies, load_session, login
from .downloader import download_logo
from .reference import fetch_reference_logo
from .manifest import (
    export_gcs_manifests,
    load_manifest,
    print_status,
    rebuild_manifest,
    save_manifest,
)
from .models import APSearchResult, Category, DownloadResult, Entity
from .search import debug_api, search_api
from .vision import identify_logo, identify_logo_by_reference, is_likely_white_logo

load_dotenv()

app = typer.Typer(
    name="ap-logos",
    help="Fetch logos from AP Newsroom using Claude Vision",
    no_args_is_help=True,
)
console = Console()

_MAX_CONSECUTIVE_FAILURES = 3

_SPORTS_CATEGORIES = {Category.NBA, Category.NFL, Category.NHL, Category.MLB}

# Keywords that indicate event/composite graphics rather than standalone logos.
_EVENT_KEYWORDS = [
    "stanley cup", "all-star", "allstar", "playoff", "quarter century",
    "nhl draft", "nba draft", "nfl draft", "mlb draft", "super bowl",
    "world series", "fined", "suspended", "traded", "injury",
    "championship", "finals", "all star",
]

# Keywords that indicate old/retro logos.
_OLD_KEYWORDS = ["old version", "throwback", "vintage", "retro", "classic logo"]

# Keywords that indicate person-related content.
_PERSON_KEYWORDS = ["headshot", "portrait", "player", "coach", "manager"]


def _get_credentials() -> tuple[str, str]:
    username = os.getenv("AP_USERNAME", "")
    password = os.getenv("AP_PASSWORD", "")
    if not username or not password:
        console.print("[red]AP_USERNAME and AP_PASSWORD must be set in .env or environment[/red]")
        raise typer.Exit(1)
    return username, password


def _get_anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        console.print("[red]ANTHROPIC_API_KEY must be set in .env or environment[/red]")
        raise typer.Exit(1)
    return key


def _load_entities(
    input_path: Path,
    category: str | None = None,
    entity: str | None = None,
) -> list[Entity]:
    """Load entities from CSV, optionally filtering by category or abbreviation."""
    entities: list[Entity] = []
    with open(input_path) as f:
        for row in csv.DictReader(f):
            try:
                entities.append(
                    Entity(
                        category=Category(row["category"].strip().lower()),
                        name=row["name"].strip(),
                        abbreviation=row["abbreviation"].strip(),
                        search_query=row.get("search_query", "").strip()
                        or f"{row['name'].strip()} logo",
                        league=row.get("league", "").strip(),
                        notes=row.get("notes", "").strip(),
                    )
                )
            except (ValueError, KeyError) as exc:
                console.print(f"[yellow]Skipping invalid row {row}: {exc}[/yellow]")

    if category:
        entities = [e for e in entities if e.category.value == category.lower()]
    if entity:
        entities = [e for e in entities if e.abbreviation.upper() == entity.upper()]

    return entities


# ---------------------------------------------------------------------------
# Candidate pre-filtering for sports logos
# ---------------------------------------------------------------------------


def _prefilter_candidates(
    results: list[APSearchResult], max_candidates: int = 8
) -> list[tuple[int, APSearchResult]]:
    """Pre-filter AP results to plausible logo candidates for sports teams.

    Uses caption/title heuristics to remove faces, event graphics, and old
    versions, then scores and ranks the remainder.

    Returns a list of ``(original_index, result)`` tuples, capped at
    *max_candidates*.
    """
    scored: list[tuple[int, int, APSearchResult]] = []  # (score, orig_idx, result)

    for i, r in enumerate(results):
        caption = r.caption.lower()
        title = r.title.lower()
        combined = f"{caption} {title}"

        # Hard exclusions
        if any(kw in combined for kw in _EVENT_KEYWORDS):
            continue
        if any(kw in combined for kw in _OLD_KEYWORDS):
            continue
        if any(kw in combined for kw in _PERSON_KEYWORDS):
            continue

        # Soft scoring
        score = 0
        if "primary logo" in caption:
            score += 3
        if "cap logo" in caption or "logo" in caption:
            score += 2
        if "logo" in title:
            score += 1
        if r.media_type == "graphic":
            score += 1
        if "secondary" in caption or "alternate" in caption:
            score -= 1
        if "wordmark" in caption or "lettering" in caption:
            score -= 1
        if "white" in caption:
            score -= 2

        scored.append((score, i, r))

    # Sort by score descending, then by original position (earlier = better)
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [(idx, result) for _score, idx, result in scored[:max_candidates]]


def _find_best_logo_by_caption(results: list[APSearchResult]) -> int | None:
    """Pick best logo using AP caption labels (for non-sports entities).

    AP captions distinguish: "cap logo", "primary logo", "secondary logo",
    "alternate logo", "lettering logo". For suggest bar icons we prefer
    the simple cap mark over the elaborate primary/marketing logo.

    Priority: current cap logo > old cap logo > current primary logo.
    """
    cap_current: int | None = None
    cap_old: int | None = None
    primary_current: int | None = None

    for i, r in enumerate(results):
        caption = r.caption.lower()
        title = r.title.lower()
        is_old = "old version" in caption or "old version" in title
        is_event = any(kw in caption or kw in title for kw in _EVENT_KEYWORDS)

        if is_event:
            continue

        has_cap = "cap logo" in caption
        has_primary = "primary logo" in caption

        if has_cap and not is_old and cap_current is None:
            cap_current = i
        elif has_cap and is_old and cap_old is None:
            cap_old = i
        elif has_primary and not is_old and primary_current is None:
            primary_current = i

    if cap_current is not None:
        return cap_current
    if cap_old is not None:
        return cap_old
    if primary_current is not None:
        return primary_current
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("login")
def login_cmd(
    headed: bool = typer.Option(False, "--headed", help="Show browser window"),
) -> None:
    """Authenticate with AP Newsroom and save session."""
    username, password = _get_credentials()
    asyncio.run(login(username, password, headed=headed))


@app.command("debug-api")
def debug_api_cmd(
    query: str = typer.Argument(..., help="Search query to debug"),
    headed: bool = typer.Option(True, "--headed/--headless"),
) -> None:
    """Capture API calls for a test search (for endpoint discovery)."""
    storage = load_session()
    if not storage:
        console.print("[red]No session found. Run 'ap-logos login' first.[/red]")
        raise typer.Exit(1)
    asyncio.run(debug_api(query, storage, headed=headed))


@app.command()
def fetch(
    input_path: Path = typer.Option(
        Path("data/entities.csv"), "--input", "-i", help="Path to entities CSV"
    ),
    output_dir: Path = typer.Option(Path("output"), "--output", "-o", help="Output directory"),
    category: str | None = typer.Option(
        None, "--category", "-c", help="Filter by category (nba, nfl, nhl, mlb, airline, company)"
    ),
    entity: str | None = typer.Option(
        None, "--entity", "-e", help="Process single entity by abbreviation"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Search + evaluate but don't download"),
    force: bool = typer.Option(False, "--force", help="Re-download even if already in manifest"),
    concurrency: int = typer.Option(3, "--concurrency", help="Parallel fetches"),
    max_results: int = typer.Option(
        40, "--max-results", help="Search results to evaluate per entity"
    ),
    headed: bool = typer.Option(False, "--headed", help="Show browser windows for debugging"),
) -> None:
    """Fetch logos for entities: search AP, identify via Vision, download."""
    asyncio.run(
        _fetch_async(
            input_path,
            output_dir,
            category,
            entity,
            dry_run,
            force,
            concurrency,
            max_results,
            headed,
        )
    )


async def _fetch_async(
    input_path: Path,
    output_dir: Path,
    category: str | None,
    entity_abbr: str | None,
    dry_run: bool,
    force: bool,
    concurrency: int,
    max_results: int,
    headed: bool,
) -> None:
    username, password = _get_credentials()
    api_key = _get_anthropic_key()

    entities = _load_entities(input_path, category, entity_abbr)
    if not entities:
        console.print("[yellow]No entities to process.[/yellow]")
        return

    console.print(f"[bold]Processing {len(entities)} entities[/bold]")

    storage = await ensure_session(username, password, headed=headed)
    cookies = get_httpx_cookies(storage)
    consecutive_failures = 0

    async def refresh_session() -> None:
        nonlocal storage, cookies
        console.print("\n[yellow]Session appears expired, re-authenticating...[/yellow]")
        storage = await login(username, password, headed=headed)
        cookies = get_httpx_cookies(storage)
        console.print("[green]Session refreshed.[/green]\n")

    existing_manifest = load_manifest(output_dir)
    already_downloaded: set[tuple[str, str]] = set()
    if existing_manifest and not force:
        already_downloaded = {(e.category, e.abbreviation) for e in existing_manifest.entries}

    results: list[DownloadResult] = []
    semaphore = asyncio.Semaphore(concurrency)

    async def process_entity(ent: Entity) -> DownloadResult:
        async with semaphore:
            return await _process_single_entity(
                ent, storage, cookies, api_key, output_dir, max_results, dry_run
            )

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("Fetching logos...", total=len(entities))

        for ent in entities:
            key = (ent.category.value, ent.abbreviation)
            if key in already_downloaded:
                console.print(f"  [dim]Skipping {ent.name} (already downloaded)[/dim]")
                results.append(
                    DownloadResult(
                        entity=ent, success=False, skipped=True, skip_reason="Already downloaded"
                    )
                )
                progress.advance(task)
                continue

            result = await process_entity(ent)

            if not result.success and not result.skipped:
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    await refresh_session()
                    consecutive_failures = 0
                    result = await process_entity(ent)
            else:
                consecutive_failures = 0

            results.append(result)
            progress.advance(task)

    if not dry_run:
        save_manifest([r for r in results if not r.skipped], output_dir)

    successful = sum(1 for r in results if r.success)
    skipped = sum(1 for r in results if r.skipped)
    failed = sum(1 for r in results if not r.success and not r.skipped)

    console.print("\n[bold]Results:[/bold]")
    console.print(f"  [green]Downloaded: {successful}[/green]")
    console.print(f"  [yellow]Skipped: {skipped}[/yellow]")
    console.print(f"  [red]Failed: {failed}[/red]")


async def _process_single_entity(
    entity: Entity,
    storage: StorageState,
    cookies: httpx.Cookies,
    api_key: str,
    output_dir: Path,
    max_results: int,
    dry_run: bool,
) -> DownloadResult:
    """Process a single entity: search, evaluate with vision, download."""
    console.print(f"\n[bold]{entity.name}[/bold] ({entity.abbreviation})")

    is_sports = entity.category in _SPORTS_CATEGORIES

    # Search — prefer "graphic" media type for sports to avoid player photos
    media_types = ["graphic"] if is_sports else None

    console.print(f"  Searching: {entity.search_query}")
    try:
        search_results = await search_api(
            entity.search_query, storage, max_results, media_types=media_types
        )
        # If graphic-only search found nothing, retry with photos included
        if not search_results and media_types == ["graphic"]:
            console.print("  [dim]No graphics found, broadening to photos...[/dim]")
            search_results = await search_api(entity.search_query, storage, max_results)
    except Exception as e:
        console.print(f"  [red]Search failed: {e}[/red]")
        return DownloadResult(entity=entity, success=False, error=str(e))

    if not search_results:
        console.print("  [yellow]No search results found[/yellow]")
        return DownloadResult(
            entity=entity, success=False, skipped=True, skip_reason="No search results"
        )

    console.print(f"  Found {len(search_results)} results")

    # -----------------------------------------------------------------------
    # Sports pipeline: pre-filter → reference comparison → fallback vision
    # -----------------------------------------------------------------------
    if is_sports:
        best, vision_confidence = await _process_sports_entity(
            entity, search_results, cookies, api_key
        )
    # -----------------------------------------------------------------------
    # Non-sports pipeline: caption match → generic vision (unchanged)
    # -----------------------------------------------------------------------
    else:
        best, vision_confidence = await _process_nonsports_entity(
            entity, search_results, cookies, api_key
        )

    if best is None:
        console.print("  [yellow]No logo found[/yellow]")
        return DownloadResult(
            entity=entity, success=False, skipped=True, skip_reason="No matching logo found"
        )

    if dry_run:
        console.print("  [dim](dry run - skipping download)[/dim]")
        return DownloadResult(
            entity=entity,
            success=False,
            skipped=True,
            skip_reason="Dry run",
            ap_item_id=best.item_id,
            ap_title=best.title,
            vision_confidence=vision_confidence,
        )

    # Download
    console.print("  Downloading...")
    logo_dir = output_dir / "logos" / entity.category.value
    try:
        file_path = await download_logo(
            best, logo_dir, entity.category.value, entity.abbreviation.lower(), storage
        )
    except Exception as e:
        console.print(f"  [red]Download failed: {e}[/red]")
        return DownloadResult(entity=entity, success=False, error=str(e))

    if file_path:
        rel_path = str(file_path.relative_to(output_dir))
        console.print(f"  [green]Saved: {rel_path}[/green]")
        return DownloadResult(
            entity=entity,
            success=True,
            file_path=rel_path,
            ap_item_id=best.item_id,
            ap_title=best.title,
            vision_confidence=vision_confidence,
        )

    return DownloadResult(entity=entity, success=False, error="Download returned no file")


async def _process_sports_entity(
    entity: Entity,
    search_results: list[APSearchResult],
    cookies: httpx.Cookies,
    api_key: str,
) -> tuple[APSearchResult | None, str]:
    """Sports-specific pipeline: pre-filter → reference match → vision fallback.

    Always uses reference comparison when a reference image is available.
    Caption text is used to filter/score candidates, never as the sole selector.
    """
    # Step 1: Pre-filter to top candidates
    candidates = _prefilter_candidates(search_results)
    if not candidates:
        console.print("  [yellow]No plausible logo candidates after filtering[/yellow]")
        return None, ""

    console.print(f"  Pre-filtered to {len(candidates)} candidates")

    # Build a sub-list for vision (preserving original indices for mapping back)
    candidate_results = [r for _idx, r in candidates]
    index_map = {i: orig_idx for i, (orig_idx, _r) in enumerate(candidates)}

    # Step 2: Fetch reference logo
    reference_image, ref_source = await fetch_reference_logo(
        entity.category.value, entity.abbreviation
    )

    # Step 3: Reference-based comparison (primary path)
    if reference_image:
        console.print(f"  Comparing {len(candidate_results)} candidates against {ref_source} reference...")
        try:
            decision = await identify_logo_by_reference(
                entity.name, candidate_results, reference_image, api_key, cookies
            )
            if not decision.no_logo_found and decision.best_index is not None:
                # Map back to original search_results index
                orig_idx = index_map.get(decision.best_index, decision.best_index)
                best = search_results[orig_idx]
                vision_confidence = f"REF_MATCH ({decision.confidence})"
                console.print(
                    f'  [cyan]Reference match: #{orig_idx} '
                    f'"{best.title}"[/cyan]'
                )
                console.print(f"  Reasoning: {decision.reasoning}")
                return best, vision_confidence
        except Exception as e:
            console.print(f"  [yellow]Reference comparison failed: {e}[/yellow]")

    # Step 4: Fallback — generic vision on filtered candidates
    console.print("  Falling back to generic vision on filtered candidates...")
    try:
        decision = await identify_logo(
            entity.name, candidate_results, api_key, cookies
        )
    except Exception as e:
        console.print(f"  [red]Vision evaluation failed: {e}[/red]")
        return None, ""

    if decision.no_logo_found or decision.best_index is None:
        console.print(f"  [yellow]No logo found: {decision.reasoning}[/yellow]")
        return None, ""

    orig_idx = index_map.get(decision.best_index, decision.best_index)
    best = search_results[orig_idx]
    vision_confidence = decision.confidence
    console.print(
        f'  [cyan]Vision match: #{orig_idx} '
        f'"{best.title}" ({vision_confidence})[/cyan]'
    )
    console.print(f"  Reasoning: {decision.reasoning}")
    return best, vision_confidence


async def _process_nonsports_entity(
    entity: Entity,
    search_results: list[APSearchResult],
    cookies: httpx.Cookies,
    api_key: str,
) -> tuple[APSearchResult | None, str]:
    """Non-sports pipeline: caption match → generic vision (unchanged logic)."""
    # Phase 1: Caption-based selection (fast, no API call)
    caption_index = _find_best_logo_by_caption(search_results)

    if caption_index is not None:
        best = search_results[caption_index]
        vision_confidence = "CAPTION_MATCH"
        console.print(
            f'  [cyan]Caption match: #{caption_index} "{best.caption or best.title}"[/cyan]'
        )
        return best, vision_confidence

    # Phase 2: Generic Vision evaluation (fallback)
    console.print("  Evaluating with Claude Vision...")
    try:
        decision = await identify_logo(
            entity.name, search_results, api_key, cookies
        )
    except Exception as e:
        console.print(f"  [red]Vision evaluation failed: {e}[/red]")
        return None, ""

    if decision.no_logo_found or decision.best_index is None:
        console.print(f"  [yellow]No logo found: {decision.reasoning}[/yellow]")
        return None, ""

    best = search_results[decision.best_index]
    vision_confidence = decision.confidence
    console.print(
        f'  [cyan]Vision match: #{decision.best_index} '
        f'"{best.title}" ({vision_confidence})[/cyan]'
    )
    console.print(f"  Reasoning: {decision.reasoning}")
    return best, vision_confidence


@app.command()
def rebuild(
    input_path: Path = typer.Option(
        Path("data/entities.csv"), "--input", "-i", help="Path to entities CSV"
    ),
    output_dir: Path = typer.Option(
        Path("output"), "--output", "-o", help="Output directory containing logos/"
    ),
) -> None:
    """Rebuild manifest.json from logos on disk, preserving existing AP metadata."""
    entities = [(e.category.value, e.name, e.abbreviation) for e in _load_entities(input_path)]
    rebuild_manifest(output_dir, entities)


@app.command()
def export(
    output_dir: Path = typer.Option(
        Path("output"), "--output", "-o", help="Output directory (must contain manifest.json)"
    ),
    cdn_base: str = typer.Option(
        "https://storage.googleapis.com/merino-images-prod",
        "--cdn-base",
        help="CDN base URL for logo URLs",
    ),
) -> None:
    """Export GCS-compatible logo manifests from downloaded logos."""
    export_gcs_manifests(output_dir, cdn_base)


@app.command()
def status(
    output_dir: Path = typer.Option(Path("output"), "--output", "-o", help="Output directory"),
) -> None:
    """Show download progress from manifest."""
    print_status(output_dir)


if __name__ == "__main__":
    app()
