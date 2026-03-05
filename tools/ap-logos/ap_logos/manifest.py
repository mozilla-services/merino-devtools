from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from .models import DownloadResult, Manifest, ManifestEntry

console = Console()


def load_manifest(output_dir: Path) -> Manifest | None:
    """Load existing manifest from output directory."""
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text())
        return Manifest(**data)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def save_manifest(results: list[DownloadResult], output_dir: Path) -> Manifest:
    """Generate and save manifest.json from download results."""
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).isoformat()
    entries: list[ManifestEntry] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for r in results:
        if r.success:
            entries.append(
                ManifestEntry(
                    category=r.entity.category.value,
                    name=r.entity.name,
                    abbreviation=r.entity.abbreviation,
                    file_path=r.file_path,
                    ap_item_id=r.ap_item_id,
                    ap_title=r.ap_title,
                    vision_confidence=r.vision_confidence,
                    downloaded_at=now,
                )
            )
        elif r.skipped:
            skipped.append(
                {
                    "category": r.entity.category.value,
                    "name": r.entity.name,
                    "abbreviation": r.entity.abbreviation,
                    "reason": r.skip_reason,
                }
            )
        else:
            failed.append(
                {
                    "category": r.entity.category.value,
                    "name": r.entity.name,
                    "abbreviation": r.entity.abbreviation,
                    "error": r.error,
                }
            )

    manifest = Manifest(
        generated_at=now,
        total=len(results),
        downloaded=len(entries),
        skipped=len(skipped),
        failed=len(failed),
        entries=entries,
        skipped_entities=skipped,
        failed_entities=failed,
    )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2))
    console.print(f"[green]Manifest saved to {manifest_path}[/green]")

    return manifest


def merge_manifest(
    existing: Manifest | None, new_results: list[DownloadResult]
) -> list[DownloadResult]:
    """Merge new results with existing manifest data.

    Returns the combined results list with existing successful entries
    preserved (for idempotency).
    """
    if not existing:
        return new_results

    downloaded = {(e.category, e.abbreviation) for e in existing.entries}

    merged = []
    for r in new_results:
        key = (r.entity.category.value, r.entity.abbreviation)
        if key in downloaded and not r.success:
            continue
        merged.append(r)

    return merged


def print_status(output_dir: Path) -> None:
    """Print a summary table of download status from manifest."""
    manifest = load_manifest(output_dir)
    if not manifest:
        console.print("[yellow]No manifest found.[/yellow]")
        return

    console.print(f"\n[bold]Manifest generated: {manifest.generated_at}[/bold]\n")

    table = Table(title="Download Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Total entities", str(manifest.total))
    table.add_row("Downloaded", f"[green]{manifest.downloaded}[/green]")
    table.add_row("Skipped", f"[yellow]{manifest.skipped}[/yellow]")
    table.add_row("Failed", f"[red]{manifest.failed}[/red]")
    console.print(table)

    if manifest.entries:
        detail = Table(title="\nDownloaded Logos")
        detail.add_column("Category")
        detail.add_column("Name")
        detail.add_column("Abbr")
        detail.add_column("File")
        detail.add_column("Confidence")

        for entry in manifest.entries:
            detail.add_row(
                entry.category,
                entry.name,
                entry.abbreviation,
                entry.file_path,
                entry.vision_confidence,
            )
        console.print(detail)

    if manifest.skipped_entities:
        console.print("\n[yellow]Skipped:[/yellow]")
        for entry in manifest.skipped_entities:
            console.print(f"  {entry['category']}/{entry['abbreviation']}: {entry['reason']}")

    if manifest.failed_entities:
        console.print("\n[red]Failed:[/red]")
        for entry in manifest.failed_entities:
            console.print(f"  {entry['category']}/{entry['abbreviation']}: {entry['error']}")


def rebuild_manifest(
    output_dir: Path,
    entities: list[tuple[str, str, str]],
) -> Manifest:
    """Rebuild manifest.json by scanning logos on disk and matching to entities.

    Preserves AP metadata from existing manifest entries where available.
    Creates stub entries for logos found on disk but missing from manifest.
    """
    entity_map: dict[tuple[str, str], str] = {(cat, abbr): name for cat, name, abbr in entities}

    existing = load_manifest(output_dir)
    existing_by_key: dict[tuple[str, str], ManifestEntry] = {}
    if existing:
        for entry in existing.entries:
            existing_by_key[(entry.category, entry.abbreviation)] = entry

    logos_dir = output_dir / "logos"
    if not logos_dir.exists():
        console.print("[red]No logos directory found.[/red]")
        raise SystemExit(1)

    now = datetime.now(UTC).isoformat()
    entries: list[ManifestEntry] = []

    for cat_dir in sorted(logos_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name
        for logo_file in sorted(cat_dir.iterdir()):
            if not logo_file.is_file():
                continue
            stem = logo_file.stem
            prefix = f"{category}_"
            if not stem.startswith(prefix):
                continue
            abbr = stem[len(prefix) :].upper()
            key = (category, abbr)
            rel_path = str(logo_file.relative_to(output_dir))

            if key in existing_by_key:
                old = existing_by_key[key]
                entries.append(
                    ManifestEntry(
                        category=category,
                        name=old.name,
                        abbreviation=abbr,
                        file_path=rel_path,
                        ap_item_id=old.ap_item_id,
                        ap_title=old.ap_title,
                        vision_confidence=old.vision_confidence,
                        downloaded_at=old.downloaded_at,
                    )
                )
            else:
                entries.append(
                    ManifestEntry(
                        category=category,
                        name=entity_map.get(key, abbr),
                        abbreviation=abbr,
                        file_path=rel_path,
                        ap_item_id="",
                        ap_title="",
                        vision_confidence="",
                        downloaded_at=now,
                    )
                )

    manifest = Manifest(
        generated_at=now,
        total=len(entries),
        downloaded=len(entries),
        skipped=0,
        failed=0,
        entries=entries,
    )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2))

    cats = Counter(e.category for e in entries)
    parts = [f"{cat}: {n}" for cat, n in sorted(cats.items())]
    console.print(f"[green]Rebuilt manifest: {len(entries)} entries ({', '.join(parts)})[/green]")

    return manifest


def _make_logo_id(category: str, abbreviation: str) -> str:
    """Deterministic logo ID via uuid5(NAMESPACE_URL, '{category}/{abbreviation}')."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{category}/{abbreviation}"))


def export_gcs_manifests(output_dir: Path, cdn_base: str = "") -> tuple[Path, Path]:
    """Generate GCS-compatible logo_manifest and logo_lookup_manifest files.

    Reads the existing manifest.json and produces:
    - logo_manifest_latest.json  (logo_id -> CDN URL + metadata)
    - logo_lookup_manifest_latest.json  (entity key -> logo_id)
    """
    manifest = load_manifest(output_dir)
    if not manifest or not manifest.entries:
        console.print("[red]No manifest found or no entries to export.[/red]")
        raise SystemExit(1)

    cdn_base = cdn_base.rstrip("/")
    now = datetime.now(UTC).isoformat()

    logos: dict[str, dict[str, str]] = {}
    lookups: list[dict[str, str]] = []

    for entry in manifest.entries:
        logo_id = _make_logo_id(entry.category, entry.abbreviation)
        file_path = entry.file_path
        fmt = Path(file_path).suffix.lstrip(".")
        url = f"{cdn_base}/{file_path}" if cdn_base else file_path

        logos[logo_id] = {
            "url": url,
            "format": fmt,
        }

        lookups.append(
            {
                "category": entry.category,
                "name": entry.name,
                "abbreviation": entry.abbreviation,
                "logo_id": logo_id,
            }
        )

    logo_manifest: dict[str, Any] = {"generated_at": now, "logos": logos}
    lookup_manifest: dict[str, Any] = {"generated_at": now, "lookups": lookups}

    logo_path = output_dir / "logo_manifest_latest.json"
    lookup_path = output_dir / "logo_lookup_manifest_latest.json"

    logo_path.write_text(json.dumps(logo_manifest, indent=2))
    lookup_path.write_text(json.dumps(lookup_manifest, indent=2))

    console.print(f"[green]Wrote {logo_path} ({len(logos)} logos)[/green]")
    console.print(f"[green]Wrote {lookup_path} ({len(lookups)} lookups)[/green]")

    return logo_path, lookup_path
