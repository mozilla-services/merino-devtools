from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import async_playwright
from rich.console import Console

console = Console()

# Playwright storage_state() returns a dict with "cookies" and "origins" keys.
# We pass it around as a plain dict rather than introducing a TypedDict, since
# Playwright's own API types it as dict[str, Any].
StorageState = dict[str, Any]

SESSION_DIR = Path(".session")
SESSION_FILE = SESSION_DIR / "ap_session.json"
AP_BASE = "https://newsroom.ap.org"


async def login(username: str, password: str, *, headed: bool = False) -> StorageState:
    """Log in to AP Newsroom via Okta and save session state."""
    SESSION_DIR.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        context = await browser.new_context()
        page = await context.new_page()

        console.print("[bold]Navigating to AP Newsroom...[/bold]")
        await page.goto(AP_BASE, wait_until="networkidle", timeout=30_000)

        # Click "Sign in" button on landing page if present
        sign_in_btn = page.locator('a:has-text("Sign in"), button:has-text("Sign in")').first
        try:
            await sign_in_btn.click(timeout=5000)
            console.print("Clicked Sign in, waiting for login form...")
            await page.wait_for_load_state("networkidle")
        except Exception:
            console.print("No Sign in button found, looking for login form directly...")

        console.print("Waiting for login form...")
        await page.wait_for_selector(
            'input[type="text"], input[type="email"], input[name="username"]',
            timeout=15_000,
        )

        console.print("Entering credentials...")
        username_input = page.locator(
            'input[type="text"], input[type="email"], input[name="username"]'
        ).first
        await username_input.fill(username)

        password_input = page.locator('input[type="password"]').first
        await password_input.fill(password)

        await page.keyboard.press("Enter")

        console.print("Waiting for login to complete...")
        await page.wait_for_url(f"{AP_BASE}/**", timeout=30_000)
        await page.wait_for_load_state("networkidle")

        storage = await context.storage_state()
        SESSION_FILE.write_text(json.dumps(storage, indent=2))
        await browser.close()

    console.print(f"[green]Session saved to {SESSION_FILE}[/green]")
    return storage


def load_session() -> StorageState | None:
    """Load a previously saved session state."""
    if not SESSION_FILE.exists():
        return None
    try:
        return json.loads(SESSION_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def extract_cookies(storage: StorageState) -> dict[str, str]:
    """Extract cookies from Playwright storage state as a flat dict."""
    return {c["name"]: c["value"] for c in storage.get("cookies", [])}


def get_httpx_cookies(storage: StorageState) -> httpx.Cookies:
    """Convert Playwright storage state cookies to httpx Cookies."""
    jar = httpx.Cookies()
    for cookie in storage.get("cookies", []):
        jar.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain", ""),
            path=cookie.get("path", "/"),
        )
    return jar


async def check_session(storage: StorageState) -> bool:
    """Check if the saved session is still valid by hitting the user API."""
    cookies = get_httpx_cookies(storage)
    try:
        async with httpx.AsyncClient(cookies=cookies, follow_redirects=True) as client:
            resp = await client.get(
                "https://api.newsroom.ap.org/v1/nraccount/getUserDetails",
                headers={"Referer": AP_BASE, "Origin": AP_BASE},
                timeout=10,
            )
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def ensure_session(username: str, password: str, *, headed: bool = False) -> StorageState:
    """Load existing session or log in fresh."""
    storage = load_session()
    if storage:
        console.print("Checking existing session...")
        if await check_session(storage):
            console.print("[green]Session is valid.[/green]")
            return storage
        console.print("[yellow]Session expired, re-authenticating...[/yellow]")

    return await login(username, password, headed=headed)
