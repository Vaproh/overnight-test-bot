"""Instagram Profile Checker — visits profiles, reads page text, returns account state."""

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ig_checker")

app = FastAPI(title="Instagram Profile Checker")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def _load_proxy_config() -> dict:
    """Load proxy settings from config.yaml."""
    try:
        with open(CONFIG_PATH, "r") as f:
            data = yaml.safe_load(f) or {}
        proxy = data.get("proxy", {})
        if proxy.get("enabled") and proxy.get("server"):
            return {
                "server": proxy["server"],
                "username": proxy.get("username", ""),
                "password": proxy.get("password", ""),
            }
    except Exception as e:
        logger.warning(f"Failed to load proxy config: {e}")
    return {}


_PROXY_CFG = _load_proxy_config()

if _PROXY_CFG.get("server"):
    PROXY = {"server": "http://127.0.0.1:8888"}
    USE_PROXY = True
    logger.info(f"Proxy enabled: routing through local wrapper -> {_PROXY_CFG['server']}")
else:
    PROXY = {}
    USE_PROXY = False
    logger.info("Proxy disabled: connecting directly")

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => false });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
        { name: 'Native Client', filename: 'internal-nacl-plugin' }
    ]
});
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
"""

VIEWPORT = {"width": 1366, "height": 768}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
LAUNCH_ARGS = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]


def _extract_stats(text: str) -> dict:
    followers = None
    following = None
    posts = None

    m = re.search(r"([\d,]+)\s*followers?", text, re.IGNORECASE)
    if m:
        followers = int(m.group(1).replace(",", ""))

    m = re.search(r"([\d,]+)\s*following", text, re.IGNORECASE)
    if m:
        following = int(m.group(1).replace(",", ""))

    m = re.search(r"([\d,]+)\s*posts?", text, re.IGNORECASE)
    if m:
        posts = int(m.group(1).replace(",", ""))

    return {"followers": followers, "following": following, "posts": posts}


async def check_profile(username: str) -> dict:
    result = {
        "username": username,
        "page_title": "",
        "page_text": "",
        "state": "error",
        "followers": None,
        "following": None,
        "posts": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=LAUNCH_ARGS,
        )
        try:
            proxy_settings = None
            if USE_PROXY and PROXY.get("server"):
                proxy_settings = {
                    "server": PROXY["server"],
                }
            context = await browser.new_context(
                viewport=VIEWPORT,
                user_agent=USER_AGENT,
                locale="en-US",
                timezone_id="America/New_York",
                proxy=proxy_settings,
            )
            await context.add_init_script(STEALTH_SCRIPT)

            page = await context.new_page()

            url = f"https://www.instagram.com/{username}/"
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                logger.warning(f"Navigation error for {username}: {e}")
                result["page_text"] = str(e)[:2000]
                return result

            await page.wait_for_timeout(2000)

            try:
                await page.evaluate("""
                    // Dismiss signup overlay (X button)
                    const xbtn = document.querySelector('div[role="button"] svg[aria-label="Close"]');
                    if(xbtn) xbtn.closest('div[role="button"]').click();
                """)
                await page.wait_for_timeout(500)
                try:
                    cookie_btn = page.get_by_role("button", name="Allow all cookies")
                    if await cookie_btn.is_visible(timeout=500):
                        await cookie_btn.click(timeout=1000)
                        await page.wait_for_timeout(500)
                except Exception:
                    pass
                try:
                    cookie_btn = page.get_by_role("button", name="Accept All")
                    if await cookie_btn.is_visible(timeout=500):
                        await cookie_btn.click(timeout=1000)
                        await page.wait_for_timeout(500)
                except Exception:
                    pass
            except Exception:
                pass

            result["page_title"] = await page.title()
            result["page_text"] = await page.evaluate("document.body.innerText")

            text = result["page_text"].lower()

            if "profile isn't available" in text or "sorry, this page isn't available" in text:
                result["state"] = "unavailable"
            elif "this profile is private" in text:
                result["state"] = "private"
                stats = _extract_stats(result["page_text"])
                result.update(stats)
            elif "followers" in text and "posts" in text:
                result["state"] = "active"
                stats = _extract_stats(result["page_text"])
                result.update(stats)
            else:
                result["state"] = "error"

        finally:
            await browser.close()

    return result


@app.get("/check/{username}")
async def check(username: str):
    if not username or len(username) > 30:
        raise HTTPException(status_code=400, detail="Invalid username")

    start = time.time()
    try:
        result = await check_profile(username)
        result["latency_ms"] = int((time.time() - start) * 1000)
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Error checking {username}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
