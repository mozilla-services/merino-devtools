from __future__ import annotations

import base64
import io
from typing import Any

import anthropic
import httpx
from PIL import Image
from rich.console import Console

from .models import APSearchResult, VisionCandidate, VisionChoice, VisionDecision

console = Console()

# Haiku for generic (fallback) classification; Sonnet for reference matching
# where accuracy on subtle color/design differences matters most.
VISION_MODEL = "claude-haiku-4-5-20251001"
REFERENCE_VISION_MODEL = "claude-sonnet-4-20250514"

LOGO_PROMPT = (
    "You are evaluating AP Newsroom search results to find the official "
    "logo/emblem for: {entity_name}\n"
    "\n"
    "I'm showing you {count} thumbnail images labeled by index (0-based).\n"
    "\n"
    "CRITICAL RULES — read these before classifying:\n"
    "\n"
    "1. IMMEDIATELY REJECT any image that contains a human face, a person, "
    "a player, an athlete, a headshot, or a photograph of real people. "
    "These are ALWAYS NOT_LOGO regardless of whether a logo appears "
    "somewhere in the frame. Logos are vector graphics, not photographs.\n"
    "\n"
    "2. IMMEDIATELY REJECT event logos, anniversary badges, All-Star Game "
    "graphics, championship graphics, or any composite that wraps the "
    "entity's logo inside a larger event design.\n"
    "\n"
    "3. A logo is a clean graphic mark: a symbol, wordmark, or emblem on "
    "a plain/simple background. It should NOT contain photographs, player "
    "images, stadium shots, or action scenes.\n"
    "\n"
    "4. REJECT any image that is predominantly white or very light colored. "
    "A white logo on a white or light background is unusable — these "
    "'ghost' logos are invisible when displayed.\n"
    "\n"
    "Classify each image as:\n"
    "- LOGO: A clean graphic containing the entity's official logo/emblem "
    "(no people, no photos, not predominantly white)\n"
    "- NOT_LOGO: Contains people, is a photograph, has no logo, is an "
    "event/commemorative badge, or is a white/invisible logo\n"
    "- UNCERTAIN: Might be a logo graphic but hard to tell at this size\n"
    "\n"
    "Pick the BEST logo. Prefer:\n"
    "1. The primary standalone logo mark (icon/emblem, not just a wordmark "
    "if both exist)\n"
    "2. Clean vector graphic on a white or simple background\n"
    "3. Current/modern version of the logo\n"
    "4. Logo only — not combined with other logos or branding\n"
    "\n"
    "If multiple valid logos exist, prefer the one that shows the iconic "
    "symbol/emblem (e.g. the bird, the star, the swoosh) rather than just "
    "a text wordmark.\n"
    "\n"
    "Return NONE only if truly zero images are clean logo graphics.\n"
    "\n"
    "Respond in this exact format:\n"
    "\n"
    "CLASSIFICATIONS:\n"
    "0: LOGO|NOT_LOGO|UNCERTAIN - brief reason\n"
    "1: LOGO|NOT_LOGO|UNCERTAIN - brief reason\n"
    "...\n"
    "\n"
    "BEST_RESULT: <index number>\n"
    "CONFIDENCE: HIGH|MEDIUM|LOW\n"
    "REASONING: <one sentence explaining your choice>\n"
    "\n"
    "If no clean logo graphic exists:\n"
    "BEST_RESULT: NONE\n"
    "CONFIDENCE: HIGH\n"
    "REASONING: <explanation>\n"
)

REFERENCE_MATCH_PROMPT = (
    "You are matching AP Newsroom images to the official reference logo for: "
    "{entity_name}\n"
    "\n"
    "The FIRST image (labeled 'REFERENCE') shows the CORRECT current official "
    "logo from the league's website.\n"
    "The remaining {count} images are AP Newsroom search results (candidates).\n"
    "\n"
    "Find the AP image that BEST MATCHES the reference logo.\n"
    "\n"
    "MATCHING CRITERIA (in priority order):\n"
    "\n"
    "1. DESIGN/SHAPE: Must be the same logo design — same icon, mascot, "
    "letters, or emblem shape as the reference. This is the most important "
    "criterion.\n"
    "\n"
    "2. COLORS: Colors must match the reference closely. A logo with the "
    "correct shape but WRONG COLORS (grayscale, inverted, alternate color "
    "scheme, different era's colors) is NOT a good match. Pay attention to "
    "primary team colors, accent colors, and outlines.\n"
    "\n"
    "3. CURRENT VERSION: Prefer the current/modern version of the logo. "
    "Reject old, retro, throwback, or vintage versions even if they look "
    "similar in shape — the fine details (line weight, proportions, color "
    "gradients) should match the reference.\n"
    "\n"
    "REJECTION RULES:\n"
    "\n"
    "- REJECT any image that is predominantly white/light — these are "
    "invisible on white backgrounds and unusable.\n"
    "- REJECT any image containing human faces, photographs, or people.\n"
    "- REJECT event graphics, game scores, championship badges, or marketing "
    "composites.\n"
    "- REJECT images where the logo is too small, cropped, or obscured.\n"
    "- VERIFY the logo actually belongs to {entity_name}. Some AP results "
    "show a completely different team's logo.\n"
    "\n"
    "PREFERENCE (when multiple reasonable matches exist):\n"
    "- Clean graphic on white or simple background\n"
    "- No extra text, no elaborate marketing versions with wordmarks\n"
    "- Standalone logo mark (icon/emblem preferred over text-only version)\n"
    "\n"
    "Respond in this exact format:\n"
    "BEST_RESULT: <index number of the AP image>\n"
    "CONFIDENCE: HIGH|MEDIUM|LOW\n"
    "COLOR_MATCH: YES|NO|PARTIAL\n"
    "REASONING: <one sentence>\n"
    "\n"
    "If no AP image matches the reference:\n"
    "BEST_RESULT: NONE\n"
    "CONFIDENCE: HIGH\n"
    "COLOR_MATCH: NO\n"
    "REASONING: <explanation>\n"
)


_CLASSIFICATION_MAP: dict[str, VisionChoice] = {
    "LOGO": VisionChoice.LOGO,
    "NOT_LOGO": VisionChoice.NOT_LOGO,
    "UNCERTAIN": VisionChoice.UNCERTAIN,
}


def is_likely_white_logo(image_data: bytes, brightness_threshold: float = 0.92) -> bool:
    """Detect if an image is predominantly white (likely invisible on white bg).

    Returns True when the average brightness exceeds *brightness_threshold*
    (0-1 scale where 1 is fully white).
    """
    try:
        img = Image.open(io.BytesIO(image_data)).convert("L")
        pixels = img.getdata()
        avg_brightness = sum(pixels) / (len(pixels) * 255)
        return avg_brightness > brightness_threshold
    except Exception:
        return False


async def identify_logo(
    entity_name: str,
    results: list[APSearchResult],
    api_key: str,
    cookies: httpx.Cookies | None = None,
) -> VisionDecision:
    """Send thumbnails to Claude Vision and identify the best logo."""
    if not results:
        return VisionDecision(
            entity_name=entity_name,
            no_logo_found=True,
            reasoning="No search results to evaluate",
        )

    images = await _fetch_thumbnails(results, cookies)

    if not any(img is not None for img in images):
        return VisionDecision(
            entity_name=entity_name,
            no_logo_found=True,
            reasoning="Could not fetch any preview thumbnails",
        )

    content = _build_vision_content(entity_name, results, images)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=VISION_MODEL,
        max_tokens=1024,
        temperature=0,
        messages=[{"role": "user", "content": content}],
    )

    return _parse_vision_response(entity_name, response.content[0].text)


async def identify_logo_by_reference(
    entity_name: str,
    results: list[APSearchResult],
    reference_image: bytes,
    api_key: str,
    cookies: httpx.Cookies | None = None,
) -> VisionDecision:
    """Compare AP thumbnails against a reference logo image using Vision.

    Uses the more capable Sonnet model for reference matching to better
    distinguish subtle color and design differences.
    """
    if not results:
        return VisionDecision(
            entity_name=entity_name,
            no_logo_found=True,
            reasoning="No search results to evaluate",
        )

    images = await _fetch_thumbnails(results, cookies)

    if not any(img is not None for img in images):
        return VisionDecision(
            entity_name=entity_name,
            no_logo_found=True,
            reasoning="Could not fetch any preview thumbnails",
        )

    content = _build_reference_content(entity_name, reference_image, results, images)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=REFERENCE_VISION_MODEL,
        max_tokens=512,
        temperature=0,
        messages=[{"role": "user", "content": content}],
    )

    return _parse_vision_response(entity_name, response.content[0].text)


def _build_reference_content(
    entity_name: str,
    reference_image: bytes,
    results: list[APSearchResult],
    images: list[bytes | None],
) -> list[dict[str, Any]]:
    """Build content array with reference image first, then AP thumbnails."""
    content: list[dict[str, Any]] = []

    prompt_text = REFERENCE_MATCH_PROMPT.format(
        entity_name=entity_name,
        count=sum(1 for img in images if img is not None),
    )
    content.append({"type": "text", "text": prompt_text})

    # Reference image first
    content.append({"type": "text", "text": "\n--- REFERENCE: correct official logo ---"})
    content.append(
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _detect_media_type(reference_image),
                "data": base64.b64encode(reference_image).decode("utf-8"),
            },
        }
    )

    # AP thumbnails
    for i, (result, img_data) in enumerate(zip(results, images, strict=True)):
        if img_data is None:
            continue

        content.append(
            {
                "type": "text",
                "text": f'\n--- Image {i}: "{result.title}" ---',
            }
        )
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _detect_media_type(img_data),
                    "data": base64.b64encode(img_data).decode("utf-8"),
                },
            }
        )

    return content


async def _fetch_thumbnails(
    results: list[APSearchResult],
    cookies: httpx.Cookies | None = None,
) -> list[bytes | None]:
    """Fetch preview images for all results. Returns None for failures."""
    images: list[bytes | None] = []

    async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, timeout=15) as client:
        for result in results:
            if not result.preview_url:
                images.append(None)
                continue
            try:
                resp = await client.get(result.preview_url)
                if resp.status_code == 200 and len(resp.content) > 100:
                    images.append(resp.content)
                else:
                    images.append(None)
            except httpx.HTTPError:
                images.append(None)

    return images


def _detect_media_type(data: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:4] == b"GIF8":
        return "image/gif"
    return "image/jpeg"


def _build_vision_content(
    entity_name: str,
    results: list[APSearchResult],
    images: list[bytes | None],
) -> list[dict[str, Any]]:
    """Build the multi-image content array for the Claude API call."""
    content: list[dict[str, Any]] = []

    prompt_text = LOGO_PROMPT.format(
        entity_name=entity_name,
        count=sum(1 for img in images if img is not None),
    )
    content.append({"type": "text", "text": prompt_text})

    for i, (result, img_data) in enumerate(zip(results, images, strict=True)):
        if img_data is None:
            continue

        content.append(
            {
                "type": "text",
                "text": f'\n--- Image {i}: "{result.title}" ---',
            }
        )

        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": _detect_media_type(img_data),
                    "data": base64.b64encode(img_data).decode("utf-8"),
                },
            }
        )

    return content


def _parse_vision_response(entity_name: str, response_text: str) -> VisionDecision:
    """Parse Claude's structured response into a VisionDecision."""
    candidates: list[VisionCandidate] = []
    best_index: int | None = None
    confidence = ""
    reasoning = ""
    no_logo_found = False

    lines = response_text.strip().split("\n")
    in_classifications = False

    for line in lines:
        line = line.strip()

        if line.startswith("CLASSIFICATIONS:"):
            in_classifications = True
            continue

        if in_classifications and line and line[0].isdigit() and ":" in line:
            try:
                idx_str, rest = line.split(":", 1)
                idx = int(idx_str.strip())
                rest = rest.strip()
                parts = rest.split(" - ", 1)
                classification = _CLASSIFICATION_MAP.get(
                    parts[0].strip().upper(), VisionChoice.NOT_LOGO
                )
                reason = parts[1].strip() if len(parts) > 1 else ""
                candidates.append(
                    VisionCandidate(
                        result_index=idx,
                        classification=classification,
                        reasoning=reason,
                    )
                )
            except (ValueError, IndexError):
                continue

        if line.startswith("BEST_RESULT:"):
            value = line.split(":", 1)[1].strip()
            if value.upper() == "NONE":
                no_logo_found = True
            else:
                try:
                    best_index = int(value)
                except ValueError:
                    no_logo_found = True
            in_classifications = False

        if line.startswith("CONFIDENCE:"):
            confidence = line.split(":", 1)[1].strip()

        if line.startswith("REASONING:"):
            reasoning = line.split(":", 1)[1].strip()

    return VisionDecision(
        entity_name=entity_name,
        best_index=best_index,
        candidates=candidates,
        confidence=confidence,
        reasoning=reasoning,
        no_logo_found=no_logo_found,
    )
