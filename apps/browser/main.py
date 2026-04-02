"""Browser automation CLI — Playwright-powered Chrome control for AI agents.

Provides programmatic browser control: navigate, click, type, extract data,
handle forms, manage tabs, take screenshots, and more. Uses Chrome via
Playwright for full rendering and JavaScript support.

Usage:
    cd apps\\browser && uv run python main.py <command> --json
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(help="Browser automation via Playwright")

# Persistent browser state
_BROWSER_STATE_DIR = Path(tempfile.gettempdir()) / "browser-agent"
_BROWSER_STATE_DIR.mkdir(exist_ok=True)
_CDP_FILE = _BROWSER_STATE_DIR / "cdp-endpoint.txt"
_COOKIES_FILE = _BROWSER_STATE_DIR / "cookies.json"


def _output(data: dict, as_json: bool = False):
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        for k, v in data.items():
            print(f"{k}: {v}")


def _run_browser_command(coro_fn):
    """Run an async browser command with guaranteed Playwright cleanup.

    Wraps the coroutine so that pw.stop() is always called, even if the
    command throws an exception. On error, prints a JSON error result
    so the calling agent always gets structured output.
    """
    async def _safe_run():
        pw = None
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            await coro_fn(pw)
        except Exception as e:
            # Always output structured error so agent can parse it
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        finally:
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass

    asyncio.run(_safe_run())


def _find_chrome_path() -> str:
    """Find Chrome executable path on macOS."""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    # Check if it's on PATH
    import shutil
    which = shutil.which("google-chrome") or shutil.which("chromium")
    return which or "Google Chrome"


def _is_chrome_running_on_cdp(port: int = 9222) -> bool:
    """Check if Chrome is already running with CDP on the given port."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except (ConnectionRefusedError, OSError):
        return False


async def _get_browser(pw=None):
    """Get or launch a persistent Chrome browser via CDP.

    Always uses CDP connection for consistency. If Chrome isn't running
    with remote debugging, launches it as a subprocess first, then connects.
    This ensures every command sees the same pages/tabs.
    """
    if pw is None:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()

    CDP_PORT = 9222
    CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

    # If Chrome is running with CDP, connect to it
    if _is_chrome_running_on_cdp(CDP_PORT):
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            _CDP_FILE.write_text(CDP_URL)
            return browser
        except Exception:
            pass  # Fall through to launch

    # Launch Chrome as a subprocess with remote debugging
    import subprocess
    chrome_path = _find_chrome_path()
    user_data_dir = str(_BROWSER_STATE_DIR / "chrome-profile")
    os.makedirs(user_data_dir, exist_ok=True)

    subprocess.Popen(
        [
            chrome_path,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--start-maximized",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for Chrome to start accepting CDP connections
    for i in range(30):
        await asyncio.sleep(0.5)
        if _is_chrome_running_on_cdp(CDP_PORT):
            break
    else:
        raise RuntimeError("Chrome failed to start with remote debugging")

    browser = await pw.chromium.connect_over_cdp(CDP_URL)
    _CDP_FILE.write_text(CDP_URL)
    return browser


_ACTIVE_PAGE_FILE = _BROWSER_STATE_DIR / "active-page.json"


async def _get_page(browser, tab_index: int = 0):
    """Get a page from the browser, creating one if needed.

    When reconnecting via CDP, Chrome may expose multiple contexts and pages.
    This function intelligently picks the right page by:
    1. Looking across ALL contexts for non-blank pages
    2. Using saved active page state to find the right tab
    3. Falling back to tab_index within the filtered list
    """
    # Collect all pages across all contexts (CDP can split them)
    all_contexts = browser.contexts
    all_pages = []
    primary_context = None
    for ctx in all_contexts:
        for p in ctx.pages:
            all_pages.append((ctx, p))
        if ctx.pages:
            primary_context = ctx

    if not all_contexts or not all_pages:
        # No contexts or pages — create fresh
        context = await browser.new_context(
            viewport=None,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        return context, page

    # Filter out about:blank pages (prefer pages with real content)
    real_pages = [(ctx, p) for ctx, p in all_pages if p.url and p.url != "about:blank"]

    # Try to match saved active page URL
    if _ACTIVE_PAGE_FILE.exists():
        try:
            saved = json.loads(_ACTIVE_PAGE_FILE.read_text())
            saved_url = saved.get("url", "")
            if saved_url and saved_url != "about:blank":
                for ctx, p in all_pages:
                    if p.url == saved_url:
                        return ctx, p
        except Exception:
            pass

    # Use real pages if available, otherwise all pages
    candidates = real_pages if real_pages else all_pages

    if tab_index < len(candidates):
        ctx, page = candidates[tab_index]
    else:
        ctx, page = candidates[-1]

    return ctx, page


def _save_active_page(url: str, title: str = ""):
    """Save active page state for reconnection."""
    try:
        _ACTIVE_PAGE_FILE.write_text(json.dumps({"url": url, "title": title}))
    except Exception:
        pass


@app.command()
def launch(
    url: Optional[str] = typer.Argument(None, help="URL to navigate to"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Launch Chrome browser, optionally navigating to a URL."""

    async def _run(pw):
        browser = await _get_browser(pw)
        context, page = await _get_page(browser)

        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = await page.title()
            current_url = page.url
        else:
            title = await page.title()
            current_url = page.url

        _save_active_page(current_url, title)

        result = {
            "ok": True,
            "action": "launch",
            "url": current_url,
            "title": title,
            "tabs": len(context.pages),
        }

        # Load saved cookies if available
        if _COOKIES_FILE.exists():
            try:
                cookies = json.loads(_COOKIES_FILE.read_text())
                await context.add_cookies(cookies)
                result["cookies_loaded"] = True
            except Exception:
                pass

        _output(result, json_output)

    _run_browser_command(_run)


@app.command()
def goto(
    url: str = typer.Argument(..., help="URL to navigate to"),
    tab: int = typer.Option(0, help="Tab index"),
    wait: str = typer.Option("domcontentloaded", help="Wait event: load, domcontentloaded, networkidle"),
    timeout: int = typer.Option(30, help="Timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Navigate to a URL in the browser."""

    async def _run(pw):
        browser = await _get_browser(pw)
        context, page = await _get_page(browser, tab)

        response = await page.goto(url, wait_until=wait, timeout=timeout * 1000)

        title = await page.title()
        _save_active_page(page.url, title)

        _output({
            "ok": True,
            "action": "goto",
            "url": page.url,
            "title": title,
            "status": response.status if response else None,
        }, json_output)

    _run_browser_command(_run)


@app.command()
def click(
    selector: str = typer.Argument(..., help="CSS selector or text to click"),
    tab: int = typer.Option(0, help="Tab index"),
    text: bool = typer.Option(False, "--text", help="Match by visible text instead of CSS selector"),
    double: bool = typer.Option(False, "--double", help="Double-click"),
    right: bool = typer.Option(False, "--right", help="Right-click"),
    timeout: int = typer.Option(10, help="Timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Click an element by CSS selector or text content."""

    async def _run(pw):
        browser = await _get_browser(pw)
        _, page = await _get_page(browser, tab)

        if text:
            locator = page.get_by_text(selector)
        else:
            locator = page.locator(selector)

        if double:
            await locator.dblclick(timeout=timeout * 1000)
        elif right:
            await locator.click(button="right", timeout=timeout * 1000)
        else:
            await locator.click(timeout=timeout * 1000)

        await page.wait_for_timeout(500)

        _output({
            "ok": True,
            "action": "click",
            "selector": selector,
            "url": page.url,
            "title": await page.title(),
        }, json_output)

    _run_browser_command(_run)


@app.command()
def fill(
    selector: str = typer.Argument(..., help="CSS selector of input field"),
    value: str = typer.Argument(..., help="Text to type"),
    tab: int = typer.Option(0, help="Tab index"),
    clear: bool = typer.Option(True, help="Clear field first"),
    submit: bool = typer.Option(False, help="Press Enter after typing"),
    timeout: int = typer.Option(10, help="Timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Fill a text input field."""

    async def _run(pw):
        browser = await _get_browser(pw)
        _, page = await _get_page(browser, tab)

        locator = page.locator(selector)
        if clear:
            await locator.fill(value, timeout=timeout * 1000)
        else:
            await locator.type(value, timeout=timeout * 1000)

        if submit:
            await locator.press("Enter")
            await page.wait_for_timeout(1000)

        _output({
            "ok": True,
            "action": "fill",
            "selector": selector,
            "value": value[:100],
            "url": page.url,
        }, json_output)

    _run_browser_command(_run)


@app.command()
def extract(
    selector: Optional[str] = typer.Argument(None, help="CSS selector to extract from"),
    tab: int = typer.Option(0, help="Tab index"),
    attr: Optional[str] = typer.Option(None, help="Attribute to extract (e.g., href, src)"),
    all_matches: bool = typer.Option(False, "--all", help="Extract from all matching elements"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Extract text or attributes from page elements."""

    async def _run(pw):
        browser = await _get_browser(pw)
        _, page = await _get_page(browser, tab)

        if selector is None:
            text = await page.inner_text("body")
            _output({
                "ok": True,
                "action": "extract",
                "text": text[:5000],
                "url": page.url,
                "title": await page.title(),
            }, json_output)
        elif all_matches:
            locators = page.locator(selector)
            count = await locators.count()
            results = []
            for i in range(min(count, 100)):
                el = locators.nth(i)
                if attr:
                    val = await el.get_attribute(attr)
                else:
                    val = await el.inner_text()
                results.append(val)
            _output({
                "ok": True,
                "action": "extract",
                "selector": selector,
                "count": count,
                "results": results,
            }, json_output)
        else:
            locator = page.locator(selector).first
            if attr:
                val = await locator.get_attribute(attr)
            else:
                val = await locator.inner_text()
            _output({
                "ok": True,
                "action": "extract",
                "selector": selector,
                "value": val,
            }, json_output)

    _run_browser_command(_run)


@app.command()
def screenshot(
    path: Optional[str] = typer.Option(None, help="Save path (default: temp file)"),
    tab: int = typer.Option(0, help="Tab index"),
    full_page: bool = typer.Option(False, "--full-page", help="Capture full scrollable page"),
    selector: Optional[str] = typer.Option(None, help="CSS selector to screenshot"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Take a screenshot of the browser page."""

    async def _run(pw):
        browser = await _get_browser(pw)
        _, page = await _get_page(browser, tab)

        if path is None:
            screenshot_path = str(_BROWSER_STATE_DIR / f"screenshot-{int(time.time())}.png")
        else:
            screenshot_path = path

        if selector:
            await page.locator(selector).screenshot(path=screenshot_path)
        else:
            await page.screenshot(path=screenshot_path, full_page=full_page)

        _output({
            "ok": True,
            "action": "screenshot",
            "path": screenshot_path,
            "url": page.url,
            "title": await page.title(),
        }, json_output)

    _run_browser_command(_run)


@app.command()
def tabs(
    tab: int = typer.Option(0, help="Tab index"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List all open browser tabs."""

    async def _run(pw):
        browser = await _get_browser(pw)
        context, _ = await _get_page(browser, tab)

        tab_list = []
        for i, page in enumerate(context.pages):
            tab_list.append({
                "index": i,
                "url": page.url,
                "title": await page.title(),
            })

        _output({
            "ok": True,
            "action": "tabs",
            "tabs": tab_list,
            "count": len(tab_list),
        }, json_output)

    _run_browser_command(_run)


@app.command()
def new_tab(
    url: Optional[str] = typer.Argument(None, help="URL to open in new tab"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Open a new browser tab."""

    async def _run(pw):
        browser = await _get_browser(pw)
        context, _ = await _get_page(browser)

        page = await context.new_page()
        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        title = await page.title()
        _save_active_page(page.url, title)

        _output({
            "ok": True,
            "action": "new_tab",
            "index": len(context.pages) - 1,
            "url": page.url,
            "title": title,
        }, json_output)

    _run_browser_command(_run)


@app.command()
def close_tab(
    tab: int = typer.Option(0, help="Tab index to close"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Close a browser tab."""

    async def _run(pw):
        browser = await _get_browser(pw)
        context, _ = await _get_page(browser)

        pages = context.pages
        if tab < len(pages):
            await pages[tab].close()
            _output({
                "ok": True,
                "action": "close_tab",
                "closed_index": tab,
                "remaining_tabs": len(context.pages),
            }, json_output)
        else:
            _output({"ok": False, "error": f"Tab {tab} not found"}, json_output)

    _run_browser_command(_run)


@app.command()
def execute(
    script: str = typer.Argument(..., help="JavaScript to execute in page"),
    tab: int = typer.Option(0, help="Tab index"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Execute JavaScript in the browser page."""

    async def _run(pw):
        browser = await _get_browser(pw)
        _, page = await _get_page(browser, tab)

        result = await page.evaluate(script)

        _output({
            "ok": True,
            "action": "execute",
            "result": result,
            "url": page.url,
        }, json_output)

    _run_browser_command(_run)


@app.command()
def wait_for(
    selector: str = typer.Argument(..., help="CSS selector to wait for"),
    tab: int = typer.Option(0, help="Tab index"),
    state: str = typer.Option("visible", help="State: visible, hidden, attached, detached"),
    timeout: int = typer.Option(30, help="Timeout in seconds"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Wait for an element to reach a specific state."""

    async def _run(pw):
        browser = await _get_browser(pw)
        _, page = await _get_page(browser, tab)

        try:
            await page.locator(selector).wait_for(state=state, timeout=timeout * 1000)
            _output({
                "ok": True,
                "action": "wait_for",
                "selector": selector,
                "state": state,
            }, json_output)
        except Exception as e:
            _output({
                "ok": False,
                "error": f"Timeout waiting for {selector} to be {state}: {e}",
            }, json_output)

    _run_browser_command(_run)


@app.command()
def cookies(
    action: str = typer.Argument("list", help="Action: list, save, load, clear"),
    domain: Optional[str] = typer.Option(None, help="Filter by domain"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Manage browser cookies."""

    async def _run(pw):
        browser = await _get_browser(pw)
        context, _ = await _get_page(browser)

        if action == "list":
            all_cookies = await context.cookies()
            if domain:
                all_cookies = [c for c in all_cookies if domain in c.get("domain", "")]
            _output({
                "ok": True,
                "action": "cookies",
                "count": len(all_cookies),
                "cookies": all_cookies[:50],
            }, json_output)
        elif action == "save":
            all_cookies = await context.cookies()
            _COOKIES_FILE.write_text(json.dumps(all_cookies))
            _output({
                "ok": True,
                "action": "save_cookies",
                "count": len(all_cookies),
                "path": str(_COOKIES_FILE),
            }, json_output)
        elif action == "load":
            if _COOKIES_FILE.exists():
                saved = json.loads(_COOKIES_FILE.read_text())
                await context.add_cookies(saved)
                _output({
                    "ok": True,
                    "action": "load_cookies",
                    "count": len(saved),
                }, json_output)
            else:
                _output({"ok": False, "error": "No saved cookies found"}, json_output)
        elif action == "clear":
            await context.clear_cookies()
            _output({"ok": True, "action": "clear_cookies"}, json_output)

    _run_browser_command(_run)


@app.command()
def pdf(
    path: Optional[str] = typer.Option(None, help="Save path"),
    tab: int = typer.Option(0, help="Tab index"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Save the current page as PDF."""

    async def _run(pw):
        browser = await _get_browser(pw)
        _, page = await _get_page(browser, tab)

        pdf_path = path or str(_BROWSER_STATE_DIR / f"page-{int(time.time())}.pdf")
        await page.pdf(path=pdf_path)

        _output({
            "ok": True,
            "action": "pdf",
            "path": pdf_path,
            "url": page.url,
        }, json_output)

    _run_browser_command(_run)


@app.command()
def select(
    selector: str = typer.Argument(..., help="CSS selector of select element"),
    value: str = typer.Argument(..., help="Value to select"),
    tab: int = typer.Option(0, help="Tab index"),
    by_label: bool = typer.Option(False, "--label", help="Select by visible label instead of value"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Select an option from a dropdown."""

    async def _run(pw):
        browser = await _get_browser(pw)
        _, page = await _get_page(browser, tab)

        locator = page.locator(selector)
        if by_label:
            await locator.select_option(label=value)
        else:
            await locator.select_option(value=value)

        _output({
            "ok": True,
            "action": "select",
            "selector": selector,
            "value": value,
        }, json_output)

    _run_browser_command(_run)


@app.command()
def scroll_page(
    direction: str = typer.Argument("down", help="Direction: up, down, top, bottom"),
    amount: int = typer.Option(500, help="Pixels to scroll"),
    tab: int = typer.Option(0, help="Tab index"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Scroll the page."""

    async def _run(pw):
        browser = await _get_browser(pw)
        _, page = await _get_page(browser, tab)

        if direction == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        elif direction == "down":
            await page.evaluate(f"window.scrollBy(0, {amount})")
        elif direction == "up":
            await page.evaluate(f"window.scrollBy(0, -{amount})")

        scroll_y = await page.evaluate("window.scrollY")
        scroll_height = await page.evaluate("document.body.scrollHeight")

        _output({
            "ok": True,
            "action": "scroll",
            "direction": direction,
            "scroll_y": scroll_y,
            "scroll_height": scroll_height,
        }, json_output)

    _run_browser_command(_run)


@app.command()
def close(
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Close the browser completely."""

    async def _run(pw):
        try:
            browser = await _get_browser(pw)
            await browser.close()
        except Exception:
            pass
        _CDP_FILE.unlink(missing_ok=True)
        _ACTIVE_PAGE_FILE.unlink(missing_ok=True)
        # Also kill any Chrome processes we started with our profile
        import subprocess
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
        _output({"ok": True, "action": "close"}, json_output)

    _run_browser_command(_run)


if __name__ == "__main__":
    app()
