"""Checker layer: curl_cffi primary, Playwright verification."""

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .config import Config

logger = logging.getLogger("monitor.checker")

API_URL = "https://i.instagram.com/api/v1/users/web_profile_info/?username={}"


def load_cookies(cookies_path: str) -> Optional[List[dict]]:
    if not os.path.exists(cookies_path):
        return None
    try:
        with open(cookies_path, "r") as f:
            cookies = json.load(f)
        if cookies:
            logger.info(f"Loaded {len(cookies)} cookies from {cookies_path}")
            return cookies
    except Exception as e:
        logger.error(f"Failed to load cookies: {e}")
    return None


def save_cookies(cookies_path: str, cookies: List[dict]):
    try:
        os.makedirs(os.path.dirname(cookies_path) if os.path.dirname(cookies_path) else ".", exist_ok=True)
        with open(cookies_path, "w") as f:
            json.dump(cookies, f, indent=2)
        logger.info(f"Saved {len(cookies)} cookies to {cookies_path}")
    except Exception as e:
        logger.error(f"Failed to save cookies: {e}")


def build_headers(user_agent: str) -> Dict[str, str]:
    return {
        "User-Agent": user_agent,
        "x-ig-app-id": "936619743392459",
        "Accept": "*/*",
        "Accept-Language": "en-US",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }


def get_response_hash(raw_response: Any) -> str:
    try:
        canonical = json.dumps(raw_response, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""


def classify_response(data: Dict[str, Any], status_code: Optional[int]) -> str:
    if status_code == 404:
        return "MISSING"
    if status_code == 429:
        return "RATE_LIMITED"
    if status_code and status_code >= 500:
        return "ERROR"

    body_str = json.dumps(data) if isinstance(data, dict) else str(data)
    if "Please wait a few minutes before you try again" in body_str:
        return "RATE_LIMITED"

    user_data = data.get("data", {}).get("user")
    if user_data is None:
        return "MISSING"
    if isinstance(user_data, dict):
        if user_data.get("is_private") is not None or user_data.get("username") is not None:
            return "ACTIVE"
        return "MISSING"
    return "MISSING"


def classify_playwright_response(page_content: str, status_code: Optional[int]) -> str:
    if status_code == 404:
        return "MISSING"

    has_profile_data = (
        '"edge_followed_by"' in page_content
        or '"edge_follow"' in page_content
        or '"is_private":' in page_content
        or re.search(r'"follower_count"\s*:\s*\d+', page_content)
        or re.search(r'"media_count"\s*:\s*\d+', page_content)
    )

    if has_profile_data:
        return "ACTIVE"

    has_login_prompt = (
        "Sorry, this page isn't available." in page_content
        or "The link you followed may be broken" in page_content
    )
    if has_login_prompt:
        return "MISSING"

    content_lower = page_content.lower()
    if "login" in content_lower and ("sign" in content_lower or "log in" in content_lower):
        if '"full_name"' in page_content and '"username"' in page_content:
            return "ACTIVE"
        return "MISSING"

    return "UNKNOWN"


def check_with_curl_cffi(username: str, config: Config) -> Dict[str, Any]:
    from curl_cffi import requests as cffi_requests

    url = API_URL.format(username)
    headers = build_headers(config.user_agent)
    proxy_url = config.proxy.get_url()

    result = {
        "username": username,
        "transport": "curl_cffi",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status_code": None,
        "latency_ms": 0,
        "response_size": 0,
        "response_hash": "",
        "raw_response": None,
        "raw_response_path": "",
        "classification": "ERROR",
        "error_message": None,
        "retry_count": 0,
    }

    start = time.time()
    try:
        resp = cffi_requests.get(
            url,
            headers=headers,
            timeout=config.request_timeout,
            proxies={"https": proxy_url, "http": proxy_url} if proxy_url else None,
            impersonate="chrome",
        )
        latency_ms = (time.time() - start) * 1000
        body = resp.text
        status_code = resp.status_code

        try:
            raw_response = json.loads(body)
        except json.JSONDecodeError:
            raw_response = {"raw_text": body[:10000]}

        response_hash = get_response_hash(raw_response)
        classification = classify_response(raw_response, status_code)

        result.update({
            "status_code": status_code,
            "latency_ms": latency_ms,
            "response_size": len(body),
            "response_hash": response_hash,
            "raw_response": raw_response,
            "classification": classification,
        })

        if config.raw_responses_dir:
            result["raw_response_path"] = _save_raw_response(
                config.raw_responses_dir, username, raw_response, "curl_cffi"
            )

    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        result.update({
            "latency_ms": latency_ms,
            "error_message": str(e)[:500],
        })
        logger.error(f"curl_cffi error for {username}: {e}")

    return result


def capture_profile_screenshot(username: str, config: Config, status: str = "unknown") -> dict:
    import asyncio
    from playwright.async_api import async_playwright

    result = {
        "screenshot_path": None,
        "profile_data": {},
    }

    url = f"https://www.instagram.com/{username}/"

    async def _capture():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=config.playwright.headless)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
                viewport={"width": 412, "height": 915},
                device_scale_factor=2.625,
                color_scheme="dark",
            )
            page = await context.new_page()

            await page.emulate_media(color_scheme="dark")

            if config.instagram_auth.enabled:
                cookies = load_cookies(config.instagram_auth.cookies_path)
                if cookies:
                    await page.context.add_cookies(cookies)

            await page.goto(url, wait_until="domcontentloaded", timeout=config.playwright.timeout)

            try:
                await page.wait_for_selector("header, main header, section main header", timeout=15000)
            except Exception:
                await page.wait_for_timeout(5000)

            await _dismiss_popups(page)
            await page.wait_for_timeout(1000)
            await _dismiss_popups(page)
            await page.wait_for_timeout(1000)

            profile_data = await _extract_profile_data_async(page)
            result["profile_data"] = profile_data

            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            screenshot_dir = os.path.join(config.screenshots_dir, date_str)
            os.makedirs(screenshot_dir, exist_ok=True)

            ts = datetime.now(timezone.utc).strftime("%H%M%S")
            filename = f"{username}_{status}_{ts}.png"
            screenshot_path = os.path.join(screenshot_dir, filename)

            if status == "active":
                tmp_path = screenshot_path + ".tmp.png"
                await page.screenshot(path=tmp_path, full_page=False)
                from PIL import Image
                img = Image.open(tmp_path)
                w, h = img.size
                scale = h / 915
                cropped = img.crop((0, int(30 * scale), w, int(300 * scale)))

                pixels = cropped.getdata()
                is_blank = all(p == (255, 255, 255) or p == (255, 255, 255, 255) for p in list(pixels)[:500])
                if is_blank:
                    logger.warning(f"Blank screenshot for {username}, skipping")
                    os.remove(tmp_path)
                else:
                    cropped.save(screenshot_path)
                    os.remove(tmp_path)
                    result["screenshot_path"] = screenshot_path

                await browser.close()
                return

            tmp_path = screenshot_path + ".tmp.png"
            await page.screenshot(path=tmp_path, full_page=False)
            from PIL import Image
            img = Image.open(tmp_path)
            w, h = img.size
            cropped = img.crop((0, 0, w, int(600 * (h / 915))))

            pixels = cropped.getdata()
            is_blank = all(p == (255, 255, 255) or p == (255, 255, 255, 255) for p in list(pixels)[:500])
            if is_blank:
                logger.warning(f"Blank screenshot for {username}, skipping")
                os.remove(tmp_path)
            else:
                cropped.save(screenshot_path)
                os.remove(tmp_path)
                result["screenshot_path"] = screenshot_path

            await browser.close()

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                loop.run_in_executor(pool, lambda: asyncio.run(_capture()))
        else:
            asyncio.run(_capture())
    except Exception as e:
        logger.error(f"Screenshot capture failed for {username}: {e}")

    return result


async def _dismiss_popups(page):
    for _ in range(3):
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass

    close_selectors = [
        'svg[aria-label="Close"]',
        'div[role="button"] svg[aria-label="Close"]',
        'button:has-text("Not Now")',
        'button:has-text("Cancel")',
        'button:has-text("OK")',
        'button:has-text("Got it")',
        'button:has-text("Allow")',
        'button:has-text("Decline")',
        'button:has-text("Turn Off")',
        'button:has-text("Accept All")',
        'button:has-text("Allow Essential")',
        '[data-testid="cookie-banner"] button',
    ]

    for selector in close_selectors:
        try:
            btns = await page.query_selector_all(selector)
            for btn in btns:
                if await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(500)
        except Exception:
            continue

    try:
        dialog = await page.query_selector('div[role="dialog"]')
        if dialog:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
    except Exception:
        pass


async def _extract_profile_data_async(page) -> dict:
    data = {}

    # Try extracting from page's visible text first (more accurate)
    try:
        # Look for the stats section with follower/following/posts
        stats_text = await page.evaluate("""() => {
            // Try to find the header section with stats
            const sections = document.querySelectorAll('section');
            for (const section of sections) {
                const text = section.innerText || '';
                if (text.includes('follower') || text.includes('Following')) {
                    return text;
                }
            }
            // Fallback to main header area
            const header = document.querySelector('header') || document.querySelector('main header');
            if (header) {
                return header.innerText || '';
            }
            return '';
        }""")

        if stats_text:
            # Parse "X posts  Y followers  Z following" format
            import re
            posts_match = re.search(r'([\d,.]+[KMB]?)\s*posts?', stats_text, re.IGNORECASE)
            followers_match = re.search(r'([\d,.]+[KMB]?)\s*followers?', stats_text, re.IGNORECASE)
            following_match = re.search(r'([\d,.]+[KMB]?)\s*following', stats_text, re.IGNORECASE)

            if posts_match:
                data["posts"] = posts_match.group(1)
            if followers_match:
                data["followers"] = followers_match.group(1)
            if following_match:
                data["following"] = following_match.group(1)
    except Exception:
        pass

    # Fallback to meta description if page extraction didn't work
    if not data.get("followers"):
        try:
            meta_desc = await page.query_selector('meta[name="description"]')
            if meta_desc:
                content = await meta_desc.get_attribute("content") or ""
                import re
                followers_match = re.search(r"([\d,.]+[KMB]?) Followers", content)
                following_match = re.search(r"([\d,.]+[KMB]?) Following", content)
                posts_match = re.search(r"([\d,.]+[KMB]?) Posts", content)

                if followers_match:
                    data["followers"] = followers_match.group(1)
                if following_match:
                    data["following"] = following_match.group(1)
                if posts_match:
                    data["posts"] = posts_match.group(1)
        except Exception:
            pass

    try:
        bio_elem = await page.query_selector("section main header section div div span")
        if bio_elem:
            bio = await bio_elem.inner_text()
            bio = bio.strip()
            if bio and len(bio) < 200:
                data["bio"] = bio
    except Exception:
        pass

    return data


def check_with_playwright(username: str, config: Config) -> Dict[str, Any]:
    import asyncio
    from playwright.async_api import async_playwright

    result = {
        "username": username,
        "transport": "playwright",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status_code": None,
        "latency_ms": 0,
        "response_size": 0,
        "response_hash": "",
        "raw_response": None,
        "raw_response_path": "",
        "classification": "ERROR",
        "error_message": None,
        "retry_count": 0,
    }

    url = f"https://www.instagram.com/{username}/"
    start = time.time()

    async def _check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=config.playwright.headless)
            context = await browser.new_context(
                user_agent=config.user_agent,
                viewport={"width": 1920, "height": 1080},
                color_scheme="dark",
            )
            page = await context.new_page()

            await page.emulate_media(color_scheme="dark")

            if config.instagram_auth.enabled:
                cookies = load_cookies(config.instagram_auth.cookies_path)
                if cookies:
                    await page.context.add_cookies(cookies)

            response = await page.goto(url, wait_until="domcontentloaded", timeout=config.playwright.timeout)
            await page.wait_for_timeout(3000)

            latency_ms = (time.time() - start) * 1000
            result["latency_ms"] = latency_ms

            if response:
                result["status_code"] = response.status

            page_content = await page.content()
            result["response_size"] = len(page_content)
            result["classification"] = classify_playwright_response(page_content, result.get("status_code"))

            result["raw_response"] = {
                "url": url,
                "title": await page.title(),
                "content_length": len(page_content),
                "content_preview": page_content[:2000],
            }

            await browser.close()

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                loop.run_in_executor(pool, lambda: asyncio.run(_check()))
        else:
            asyncio.run(_check())

    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        result["latency_ms"] = latency_ms
        result["error_message"] = str(e)[:500]
        logger.error(f"Playwright error for {username}: {e}")

    return result


def check_account(username: str, config: Config) -> Dict[str, Any]:
    max_retries = config.retry.attempts
    backoff_list = config.retry.backoff_seconds

    last_result = None
    for attempt in range(max_retries):
        result = check_with_curl_cffi(username, config)
        result["retry_count"] = attempt

        classification = result["classification"]

        if classification == "RATE_LIMITED":
            backoff = backoff_list[min(attempt, len(backoff_list) - 1)]
            logger.warning(f"Rate limited for {username}, attempt {attempt + 1}/{max_retries}, retry in {backoff}s")
            time.sleep(backoff)
            last_result = result
            continue

        if classification == "ERROR" and result.get("error_message"):
            err = result["error_message"].lower()
            if any(kw in err for kw in ["timeout", "connection", "proxy", "network"]):
                backoff = backoff_list[min(attempt, len(backoff_list) - 1)]
                logger.warning(f"Retryable error for {username}: {result['error_message']}, retry in {backoff}s")
                time.sleep(backoff)
                last_result = result
                continue

        return result

    return last_result or {
        "username": username,
        "classification": "ERROR",
        "error_message": "All retries exhausted",
    }


def verify_with_playwright(username: str, curl_result: Dict[str, Any], config: Config) -> Dict[str, Any]:
    if not config.playwright.enabled:
        return curl_result

    curl_class = curl_result.get("classification")
    if curl_class != "MISSING":
        return curl_result

    logger.info(f"Verifying {username} with Playwright (curl said MISSING)")
    pw_result = check_with_playwright(username, config)
    pw_class = pw_result.get("classification")

    curl_result["verification_status"] = pw_class

    if pw_class == "MISSING":
        curl_result["classification"] = "MISSING"
        logger.info(f"Playwright confirms {username} is MISSING")
    elif pw_class == "ACTIVE":
        curl_result["classification"] = "SUSPECT"
        logger.warning(f"Disagreement: curl=MISSING, playwright=ACTIVE for {username} -> SUSPECT")
    else:
        logger.warning(f"Playwright returned {pw_class} for {username}, keeping curl result")

    return curl_result


def _save_raw_response(raw_dir: str, username: str, raw_response: Any, transport: str) -> str:
    try:
        os.makedirs(raw_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{username}_{transport}_{ts}.json"
        filepath = os.path.join(raw_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(raw_response, f, ensure_ascii=False, indent=2, default=str)
        return filepath
    except Exception as e:
        logger.error(f"Failed to save raw response: {e}")
        return ""
