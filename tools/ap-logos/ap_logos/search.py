from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import Page, Request, Response, async_playwright
from rich.console import Console

from .auth import AP_BASE, StorageState, get_httpx_cookies
from .models import APSearchResult, Rendition

console = Console()

NR_API_BASE = "https://api.newsroom.ap.org/v1"
SEARCH_ENDPOINT = f"{NR_API_BASE}/nrsearch/search"


async def debug_api(
    query: str, storage: StorageState, *, headed: bool = True
) -> list[dict[str, Any]]:
    """Launch a headed browser, perform a search, and capture all API requests.

    Logs captured requests to .session/api_debug.json.
    """
    captured: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        context = await browser.new_context(storage_state=storage)
        page = await context.new_page()

        async def on_request(request: Request) -> None:
            if request.resource_type in ("image", "stylesheet", "font", "media"):
                return
            url = request.url
            if url.startswith("data:"):
                return
            entry: dict[str, Any] = {
                "method": request.method,
                "url": url,
                "resource_type": request.resource_type,
            }
            if request.post_data:
                entry["post_data"] = request.post_data[:2000]
            captured.append(entry)
            console.print(
                f"  [cyan]-> {request.resource_type} {request.method} {url[:140]}[/cyan]"
            )

        async def on_response(response: Response) -> None:
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type:
                return
            try:
                body = await response.json()
                for entry in reversed(captured):
                    if entry["url"] == response.url:
                        entry["status"] = response.status
                        entry["response_preview"] = json.dumps(body)[:3000]
                        break
            except Exception:
                pass

        page.on("request", on_request)
        page.on("response", on_response)

        console.print(f"[bold]Navigating to AP Newsroom, will search: {query}[/bold]")
        await page.goto(AP_BASE, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(1000)

        search_input = page.locator(
            'input[type="search"], input[placeholder*="earch"], '
            'input[class*="search"], input[aria-label*="earch"]'
        ).first
        await search_input.click(timeout=5000)
        await search_input.fill(query)
        await page.keyboard.press("Enter")

        console.print("Waiting for search results...")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(5000)

        console.print(f"  [bold]Final URL: {page.url}[/bold]")

        img_dump = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('img')).map(img => ({
                src: img.src,
                alt: img.alt,
                width: img.naturalWidth,
                parentClass: img.parentElement?.className?.substring(0, 80)
            })).filter(i => i.src && !i.src.includes('data:'));
        }""")
        html_file = Path(".session") / "search_dom.json"
        html_file.write_text(json.dumps(img_dump, indent=2))
        console.print(f"[green]Image dump ({len(img_dump)} images) -> {html_file}[/green]")

        await browser.close()

    debug_file = Path(".session") / "api_debug.json"
    debug_file.parent.mkdir(exist_ok=True)
    debug_file.write_text(json.dumps(captured, indent=2))
    console.print(f"\n[green]Captured {len(captured)} API calls -> {debug_file}[/green]")

    return captured


async def search_api(
    query: str,
    storage: StorageState,
    max_results: int = 20,
    media_types: list[str] | None = None,
) -> list[APSearchResult]:
    """Search AP Newsroom via the nrsearch API using session cookies.

    Falls back to browser scraping if the API call fails.
    """
    if media_types is None:
        media_types = ["photo", "graphic"]
    cookies = get_httpx_cookies(storage)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": f"{AP_BASE}/",
        "Origin": AP_BASE,
    }

    payload: dict[str, Any] = {
        "SearchType": "keyword",
        "PageNumber": 1,
        "PageSize": min(max_results, 50),
        "MediaTypes": media_types,
        "TopicId": None,
        "isTopicSearch": False,
        "ProductGroup": "",
        "language": "",
        "digitizationType": None,
        "isSemanticItemIdSearch": False,
        "Semantic": None,
        "MyPlanSearch": False,
        "IsSharedSearch": False,
        "ShareToken": "",
        "persons": [],
        "Query": query,
        "IsSavedSearch": False,
        "photoOrientTypes": [],
        "Coll": "",
        "FilterBy": "",
        "FootageType": [],
        "IgnoreSpellCheck": False,
        "Sort": [""],
        "query_from_date": None,
        "itemId": None,
    }

    try:
        async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, timeout=20) as client:
            resp = await client.post(SEARCH_ENDPOINT, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                Path(".session").mkdir(exist_ok=True)
                Path(".session/api_response.json").write_text(json.dumps(data, default=str))
                results = _parse_nrsearch_response(data)
                if results:
                    console.print(f"  [green]API returned {len(results)} results[/green]")
                    return results[:max_results]

            console.print(
                f"[yellow]API returned {resp.status_code}, falling back to browser...[/yellow]"
            )
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        console.print(f"[yellow]API call failed ({e}), falling back to browser...[/yellow]")

    return await search_browser(query, storage, max_results)


async def search_browser(
    query: str, storage: StorageState, max_results: int = 20
) -> list[APSearchResult]:
    """Fallback: use Playwright to perform search and intercept API response."""
    results: list[APSearchResult] = []
    api_responses: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=storage)
        page = await context.new_page()

        async def on_response(response: Response) -> None:
            if "nrsearch/search" in response.url and response.status == 200:
                try:
                    body = await response.json()
                    api_responses.append(body)
                except Exception:
                    pass

        page.on("response", on_response)

        await page.goto(AP_BASE, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(1000)

        search_input = page.locator(
            'input[type="search"], input[placeholder*="earch"], '
            'input[class*="search"], input[aria-label*="earch"]'
        ).first
        await search_input.click(timeout=5000)
        await search_input.fill(query)
        await page.keyboard.press("Enter")

        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)

        if api_responses:
            for api_data in api_responses:
                results.extend(_parse_nrsearch_response(api_data))
            if results:
                await browser.close()
                return results[:max_results]

        results = await _scrape_search_results(page, max_results)
        await browser.close()

    return results


async def _scrape_search_results(page: Page, max_results: int) -> list[APSearchResult]:
    """Parse search results from the AP Newsroom DOM.

    Images are in .card-container elements with preview URLs from
    mapi.associatedpress.com.
    """
    items: list[dict[str, str]] = await page.evaluate("""() => {
        const results = [];
        const cards = document.querySelectorAll('.card-container');
        for (const card of cards) {
            const img = card.querySelector('img');
            if (!img) continue;
            const src = img.src || img.dataset.src || '';
            if (!src || !src.includes('mapi.associatedpress.com')) continue;

            const match = src.match(/items\\/([a-f0-9]+)\\/preview/);
            const itemId = match ? match[1] : '';

            let title = img.alt || '';
            const titleEl = card.querySelector(
                '[class*="title"], [class*="headline"], h3, h4, span'
            );
            if (titleEl) title = titleEl.textContent.trim() || title;

            const link = card.querySelector('a[href]');
            const href = link ? link.getAttribute('href') : '';

            results.push({ src, itemId, title, href });
        }
        return results;
    }""")

    results: list[APSearchResult] = []
    for item in items[:max_results]:
        item_id = item.get("itemId", "")
        href = item.get("href", "")
        results.append(
            APSearchResult(
                item_id=item_id or f"scraped-{len(results)}",
                title=item.get("title", ""),
                preview_url=item["src"],
                detail_url=f"{AP_BASE}{href}" if href else "",
            )
        )

    return results


def _parse_nrsearch_response(data: dict[str, Any] | list[Any]) -> list[APSearchResult]:
    """Parse the api.newsroom.ap.org nrsearch response."""
    results: list[APSearchResult] = []

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("Items", []) or data.get("items", [])
    else:
        return results

    for item in items:
        try:
            source = item.get("_source", item)

            item_id = str(source.get("itemid", "") or item.get("_id", "") or "")
            title = str(source.get("headline", "") or source.get("title", "") or "")
            friendly_key = str(source.get("friendlykey", "") or "")

            # Extract caption text (contains "primary logo", "secondary logo" etc.)
            cap = source.get("caption", {})
            caption_text = ""
            if isinstance(cap, dict):
                nitf = cap.get("nitf", "")
                # Strip HTML tags from NITF caption
                import re
                caption_text = re.sub(r"<[^>]+>", "", nitf).strip()
            elif isinstance(cap, str):
                caption_text = cap

            preview_url = ""
            if item_id:
                preview_url = (
                    f"https://mapi.associatedpress.com/v2/items/{item_id}"
                    f"/preview/AP{friendly_key}.jpg?s=540x360"
                )

            renditions: list[Rendition] = []
            for r in source.get("renditions", []):
                if isinstance(r, dict):
                    renditions.append(
                        Rendition(
                            title=r.get("title", ""),
                            code=r.get("code", ""),
                            file_extension=r.get("fileextension", ""),
                            width=r.get("width", 0) or 0,
                            height=r.get("height", 0) or 0,
                            rel=r.get("rel", ""),
                        )
                    )

            if item_id:
                results.append(
                    APSearchResult(
                        item_id=item_id,
                        title=title,
                        preview_url=preview_url,
                        caption=caption_text,
                        renditions=renditions,
                        date_created=str(
                            source.get("firstcreated", "")
                            or source.get("arrivaldatetime", "")
                            or ""
                        ),
                    )
                )
        except Exception:
            continue

    return results
