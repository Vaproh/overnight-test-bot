"""Checker layer: curl_cffi primary, Playwright verification, screenshot service."""

import hashlib
import json
import logging
import os
import random
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


def is_profile_unavailable_snapshot(snapshot: str) -> bool:
    """Check if accessibility snapshot indicates a missing/banned/deactivated profile."""
    unavailable_phrases = [
        "Sorry, this page isn't available",
        "This page isn't available",
        "The link you followed may be broken",
        "Page Not Found",
        "The page you were looking for doesn't exist",
        "Profile isn't available",
        "no longer with us",
    ]
    return any(phrase in snapshot for phrase in unavailable_phrases)


def is_page_loaded_snapshot(snapshot: str) -> bool:
    """Check if accessibility snapshot shows profile data (posts, followers, following)."""
    indicators = ["posts", "followers", "following"]
    snapshot_lower = snapshot.lower()
    count = sum(1 for ind in indicators if ind in snapshot_lower)
    return count >= 2


def detect_overlay_snapshot(snapshot: str) -> Optional[str]:
    """Detect overlay buttons from accessibility snapshot.
    Returns button accessible name if found, None otherwise.
    Snapshot format: button "Close" [e123]
    """
    overlay_buttons = [
        "Close",
        "Decline optional cookies",
        "Deny all",
        "Reject all",
        "Rejeter tout",
        "Not now",
        "Allow all cookies",
        "Allow All Cookies",
        "Accept all",
        "Accept All",
        "Accept cookies",
        "Accept",
        "Log in later",
        "Cancel",
    ]
    for button_name in overlay_buttons:
        pattern = rf'button "{re.escape(button_name)}" \[(e\d+)\]'
        match = re.search(pattern, snapshot)
        if match:
            return button_name
    return None


def classify_playwright_response(snapshot: str, status_code: Optional[int]) -> str:
    """Classify profile status from Playwright accessibility snapshot.
    Uses the same approach as the screenshot service's Camofox client.
    """
    if status_code == 404:
        return "MISSING"

    if is_profile_unavailable_snapshot(snapshot):
        return "MISSING"

    if is_page_loaded_snapshot(snapshot):
        return "ACTIVE"

    return "UNKNOWN"


def check_with_curl_cffi(username: str, config: Config) -> Dict[str, Any]:
    from curl_cffi import requests as cffi_requests

    url = API_URL.format(username)
    user_agent = random.choice(config.user_agents)
    headers = build_headers(user_agent)
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
    import requests as _requests

    result = {
        "screenshot_path": None,
        "profile_data": {},
        "error": None,
    }

    service_url = config.screenshot_service_url
    if not service_url:
        result["error"] = "no_service"
        logger.warning("No screenshot_service_url configured, skipping screenshot")
        return result

    try:
        url = f"{service_url.rstrip('/')}/screenshot/{username}"
        resp = _requests.get(url, timeout=30)

        if resp.status_code == 200:
            content_type = resp.headers.get("content-type", "")
            if "image" in content_type or len(resp.content) > 1000:
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                screenshot_dir = os.path.join(config.screenshots_dir, date_str)
                os.makedirs(screenshot_dir, exist_ok=True)

                ts = datetime.now(timezone.utc).strftime("%H%M%S")
                filename = f"{username}_{status}_{ts}.png"
                screenshot_path = os.path.join(screenshot_dir, filename)

                with open(screenshot_path, "wb") as f:
                    f.write(resp.content)

                result["screenshot_path"] = screenshot_path
                logger.info(f"Screenshot captured for {username} via service")
            else:
                result["error"] = "not_image"
                logger.warning(f"Screenshot service returned non-image for {username}")
        elif resp.status_code == 404:
            result["error"] = "profile_unavailable"
            logger.info(f"Screenshot service: profile unavailable for {username}")
        elif resp.status_code == 400:
            result["error"] = "invalid_username"
            logger.warning(f"Screenshot service: invalid username for {username}")
        elif resp.status_code == 429:
            result["error"] = "rate_limited"
            logger.warning(f"Screenshot service: rate limited for {username}")
        elif resp.status_code == 503:
            result["error"] = "service_down"
            logger.error(f"Screenshot service: Camofox not available")
        elif resp.status_code == 504:
            result["error"] = "timeout"
            logger.error(f"Screenshot service: page load timeout for {username}")
        else:
            result["error"] = f"http_{resp.status_code}"
            logger.warning(f"Screenshot service returned {resp.status_code} for {username}")

    except _requests.exceptions.ConnectionError:
        result["error"] = "connection_refused"
        logger.error(f"Screenshot service unreachable at {service_url}")
    except _requests.exceptions.Timeout:
        result["error"] = "timeout"
        logger.error(f"Screenshot service timed out for {username}")
    except Exception as e:
        result["error"] = "unknown"
        logger.error(f"Screenshot service error for {username}: {e}")

    return result


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
            proxy_url = config.proxy.get_url()
            launch_args = {"headless": config.playwright.headless}
            if proxy_url:
                launch_args["proxy"] = {"server": proxy_url}
            browser = await p.chromium.launch(**launch_args)
            try:
                user_agent = random.choice(config.user_agents)
                context = await browser.new_context(
                    user_agent=user_agent,
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

                if response:
                    result["status_code"] = response.status

                for _ in range(10):
                    snapshot = await page.accessibility.snapshot()
                    if not snapshot:
                        break

                    snapshot_str = json.dumps(snapshot)

                    if is_profile_unavailable_snapshot(snapshot_str):
                        result["classification"] = "MISSING"
                        result["raw_response"] = {"url": url, "snapshot": snapshot_str[:2000]}
                        break

                    overlay_name = detect_overlay_snapshot(snapshot_str)
                    if overlay_name:
                        logger.debug(f"Dismissing '{overlay_name}' overlay for {username}")
                        try:
                            btn = page.get_by_role("button", name=overlay_name)
                            if await btn.is_visible(timeout=500):
                                await btn.click(timeout=1000)
                                await page.wait_for_timeout(1000)
                                continue
                        except Exception:
                            await page.wait_for_timeout(1000)
                            continue

                    if is_page_loaded_snapshot(snapshot_str):
                        result["classification"] = "ACTIVE"
                        result["raw_response"] = {"url": url, "snapshot": snapshot_str[:2000]}
                        break

                    break

                if result["classification"] == "ERROR":
                    snapshot = await page.accessibility.snapshot()
                    snapshot_str = json.dumps(snapshot) if snapshot else ""
                    result["response_size"] = len(snapshot_str)
                    result["raw_response"] = {"url": url, "snapshot": snapshot_str[:2000]}
                    logger.warning(f"No snapshot match for {username}, snapshot preview: {snapshot_str[:500]}")

                latency_ms = (time.time() - start) * 1000
                result["latency_ms"] = latency_ms
                result["response_size"] = len(json.dumps(result.get("raw_response", {})))
            finally:
                await browser.close()

    try:
        asyncio.run(asyncio.wait_for(_check(), timeout=config.playwright.timeout / 1000))
    except asyncio.TimeoutError:
        logger.warning(f"Playwright check timed out for {username}")
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
        raw = pw_result.get("raw_response", {})
        if isinstance(raw, dict):
            preview = raw.get("content_preview", "")[:500]
            logger.debug(f"Page preview for {username}: {preview}")

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
