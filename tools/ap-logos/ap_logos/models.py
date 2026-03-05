from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class Category(StrEnum):
    NBA = "nba"
    NFL = "nfl"
    NHL = "nhl"
    MLB = "mlb"
    AIRLINE = "airline"
    COMPANY = "company"


class Entity(BaseModel):
    category: Category
    name: str
    abbreviation: str
    search_query: str
    league: str = ""
    notes: str = ""


class Rendition(BaseModel):
    """A single rendition/resolution for an AP item."""

    title: str = ""
    code: str = ""
    file_extension: str = ""
    width: int = 0
    height: int = 0
    rel: str = ""


class APSearchResult(BaseModel):
    """A single image result from AP Newsroom search."""

    item_id: str
    title: str
    preview_url: str
    detail_url: str = ""
    media_type: str = "photo"
    date_created: str = ""
    renditions: list[Rendition] = []


class VisionChoice(StrEnum):
    LOGO = "logo"
    NOT_LOGO = "not_logo"
    UNCERTAIN = "uncertain"


class VisionCandidate(BaseModel):
    """Vision classification for a single search result."""

    result_index: int
    classification: VisionChoice
    reasoning: str = ""


class VisionDecision(BaseModel):
    """Claude Vision's decision for which result is the best logo."""

    entity_name: str
    best_index: int | None = None
    candidates: list[VisionCandidate] = []
    confidence: str = ""
    reasoning: str = ""
    no_logo_found: bool = False


class DownloadResult(BaseModel):
    entity: Entity
    success: bool
    file_path: str = ""
    ap_item_id: str = ""
    ap_title: str = ""
    vision_confidence: str = ""
    error: str = ""
    skipped: bool = False
    skip_reason: str = ""


class ManifestEntry(BaseModel):
    category: str
    name: str
    abbreviation: str
    file_path: str
    ap_item_id: str
    ap_title: str
    vision_confidence: str
    downloaded_at: str


class Manifest(BaseModel):
    generated_at: str
    total: int
    downloaded: int
    skipped: int
    failed: int
    entries: list[ManifestEntry] = []
    skipped_entities: list[dict[str, Any]] = []
    failed_entities: list[dict[str, Any]] = []
