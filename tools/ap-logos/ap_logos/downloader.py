from __future__ import annotations

from pathlib import Path

import httpx
from playwright.async_api import async_playwright
from rich.console import Console

from .auth import AP_BASE, StorageState, get_httpx_cookies
from .models import APSearchResult, Rendition

console = Console()

MAPI_BASE = "https://mapi.associatedpress.com/v2"

# Preferred download resolution labels, tried in order.
_BROWSER_DOWNLOAD_LABELS = [
    "High Resolution (PNG)",
    "Full Resolution (JPG 768x576)",
    "Full Resolution (JPG 640x480)",
    "Full Resolution (JPG 2000x1500)",
    "Full Resolution (JPG 1500x1125)",
]

_IMAGE_MAGIC = {
    b"\x89PNG": "png",
    b"\xff\xd8": "jpg",
    b"GIF8": "gif",
}


def _detect_image_format(data: bytes) -> str | None:
    """Detect image format from magic bytes. Returns extension or None."""
    for magic, ext in _IMAGE_MAGIC.items():
        if data[: len(magic)] == magic:
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _pick_best_rendition(result: APSearchResult) -> Rendition | None:
    """Pick the best rendition to download: prefer PNG, then large JPG."""
    if not result.renditions:
        return None

    for r in result.renditions:
        if r.file_extension.upper() == "PNG" and r.rel == "Main":
            return r

    main_jpgs = [
        r
        for r in result.renditions
        if r.rel == "Main" and r.file_extension.lower() in ("jpg", "jpeg")
    ]
    if main_jpgs:
        return max(main_jpgs, key=lambda r: r.width * r.height)

    mains = [r for r in result.renditions if r.rel == "Main"]
    return mains[0] if mains else None


async def download_logo(
    result: APSearchResult,
    output_dir: Path,
    category: str,
    abbreviation: str,
    storage: StorageState,
) -> Path | None:
    """Download the selected logo image.

    Tries direct API download with rendition code first,
    falls back to Playwright browser download.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    rendition = _pick_best_rendition(result)
    # Always use .png for consistent filenames across runs
    filename = f"{category}_{abbreviation}.png"
    out_path = output_dir / filename

    if (
        rendition
        and rendition.code
        and await _download_rendition(result.item_id, rendition.code, out_path, storage)
    ):
        return out_path

    if await _download_via_browser(result, out_path, storage):
        return out_path

    return None


async def _download_rendition(
    item_id: str, rendition_code: str, out_path: Path, storage: StorageState
) -> bool:
    """Download a specific rendition via the mapi API."""
    cookies = get_httpx_cookies(storage)
    url = f"{MAPI_BASE}/items/{item_id}/renditions/{rendition_code}/download"
    headers = {"Referer": f"{AP_BASE}/", "Origin": AP_BASE}

    try:
        async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, timeout=30) as client:
            resp = await client.get(url, headers=headers)
            if (
                resp.status_code == 200
                and len(resp.content) > 500
                and _detect_image_format(resp.content)
            ):
                out_path.write_bytes(resp.content)
                console.print(
                    f"  [green]Downloaded rendition via API: "
                    f"{out_path.name} ({len(resp.content)} bytes)[/green]"
                )
                return True
    except httpx.HTTPError as e:
        console.print(f"  [yellow]Rendition download failed: {e}[/yellow]")

    return False


async def _download_via_browser(
    result: APSearchResult, out_path: Path, storage: StorageState
) -> bool:
    """Use Playwright to navigate to the item detail page and download."""
    if not result.item_id:
        return False

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=storage, accept_downloads=True)
            page = await context.new_page()

            # Search using item_id to find the exact item
            search_url = f"{AP_BASE}/home/search?query={result.item_id}&mediaType=graphic"
            await page.goto(search_url, wait_until="networkidle", timeout=30_000)
            await page.wait_for_timeout(3000)

            # Only click the card that matches our exact item_id — never a random card
            card = page.locator(f'img[src*="{result.item_id}"]').first
            try:
                await card.click(timeout=8000)
            except Exception:
                console.print(
                    f"  [yellow]Could not find item {result.item_id} on page, "
                    f"aborting browser download[/yellow]"
                )
                await browser.close()
                return False

            await page.wait_for_timeout(2000)

            download_btn = page.locator("#detail_download").first
            try:
                await download_btn.click(timeout=8000)
            except Exception:
                download_btn = page.locator('button:has-text("Download")').first
                await download_btn.click(timeout=5000)

            await page.wait_for_timeout(1000)

            for label in _BROWSER_DOWNLOAD_LABELS:
                option = page.locator(f'text="{label}"').first
                try:
                    async with page.expect_download(timeout=15_000) as dl_info:
                        await option.click(timeout=2000)
                    download = await dl_info.value
                    await download.save_as(str(out_path))
                    console.print(
                        f"  [green]Downloaded via browser ({label}): {out_path.name}[/green]"
                    )
                    await browser.close()
                    return True
                except Exception:
                    continue

            # Last resort: click any resolution option
            options = page.locator("text=/High Resolution|Full Resolution/")
            count = await options.count()
            for i in range(count):
                try:
                    async with page.expect_download(timeout=10_000) as dl_info:
                        await options.nth(i).click()
                    download = await dl_info.value
                    await download.save_as(str(out_path))
                    console.print(
                        f"  [green]Downloaded via browser (fallback): {out_path.name}[/green]"
                    )
                    await browser.close()
                    return True
                except Exception:
                    continue

            await browser.close()

    except Exception as e:
        console.print(f"  [red]Browser download failed: {e}[/red]")

    return False
