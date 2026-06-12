"""Instagram account visibility checkers using pluggable transport layer."""

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from transports import fetch_profile, TransportResult

logger = logging.getLogger("instagram_monitor")

ANDROID_USER_AGENTS = [
    "Instagram 320.0.0.0 Android (33; 33; SM-S908B; SM-S908B; 33; 33; exynos2200; en_US; 701237498)",
    "Instagram 320.0.0.0 Android (30; 30; SM-G991B; SM-G991B; 30; 30; exynos2100; en_US; 701237498)",
    "Instagram 319.0.0.0 Android (33; 33; Pixel 7; Pixel 7; 33; 33; google; en_US; 701237498)",
    "Instagram 318.0.0.0 Android (31; 31; Pixel 6; Pixel 6; 31; 31; google; en_US; 701237498)",
    "Instagram 320.0.0.0 Android (14; 14; SM-A546B; SM-A546B; 14; 14; samsungexynos2200; en_US; 701237498)",
    "Instagram 319.0.0.0 Android (14; 14; SM-S926B; SM-S926B; 14; 14; samsungexynos2400; en_US; 701237498)",
]


def get_user_agent(config: Dict[str, Any]) -> str:
    ua_cfg = config.get("user_agents", {})
    mode = ua_cfg.get("mode", "android_only")

    if mode == "fixed":
        return ua_cfg.get("fixed", ANDROID_USER_AGENTS[0])
    elif mode == "custom" and ua_cfg.get("list"):
        return ua_cfg["list"][len(ANDROID_USER_AGENTS) % len(ua_cfg["list"])]
    else:
        return ANDROID_USER_AGENTS[0]


def build_headers(user_agent: str) -> Dict[str, str]:
    return {
        "User-Agent": user_agent,
        "x-ig-app-id": "936619743392459",
        "Accept": "*/*",
        "Accept-Language": "en-US",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }


def build_api_url(username: str) -> str:
    return f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"


def build_proxy_url(config: Dict[str, Any]) -> Optional[str]:
    proxy_cfg = config.get("proxy", {})
    if not proxy_cfg.get("enabled"):
        return None
    server = proxy_cfg.get("server", "")
    username = proxy_cfg.get("username", "")
    password = proxy_cfg.get("password", "")
    if not server:
        return None
    if username and password:
        if "://" in server:
            scheme, rest = server.split("://", 1)
            return f"{scheme}://{username}:{password}@{rest}"
        return f"http://{username}:{password}@{server}"
    return server


def classify_response(data: Dict[str, Any], status_code: Optional[int]) -> str:
    if status_code == 404:
        return "MISSING"
    if status_code == 401 or status_code == 403:
        return "ACTIVE"
    if status_code == 429:
        return "UNKNOWN"
    if status_code and status_code >= 500:
        return "UNKNOWN"
    if status_code and status_code < 200:
        return "ERROR"

    if "status" in data:
        s = str(data.get("status", "")).lower()
        if s in ("ok", "success"):
            pass
        elif s in ("fail", "error"):
            return "UNKNOWN"

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
    content_lower = page_content.lower()

    if "login" in content_lower and ("sign" in content_lower or "log in" in content_lower):
        return "ACTIVE"
    if '"edge_followed_by"' in page_content or '"edge_follow"' in page_content:
        return "ACTIVE"
    if '"is_private":' in page_content:
        return "ACTIVE"
    if '"full_name"' in page_content and '"username"' in page_content:
        return "ACTIVE"
    if "Sorry, this page isn't available." in page_content:
        return "MISSING"
    if "The link you followed may be broken" in page_content:
        return "MISSING"
    if re.search(r'"follower_count"\s*:\s*\d+', page_content):
        return "ACTIVE"
    if re.search(r'"media_count"\s*:\s*\d+', page_content):
        return "ACTIVE"

    return "UNKNOWN"


def get_response_hash(raw_response: Any) -> str:
    try:
        canonical = json.dumps(raw_response, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""


def save_raw_response(raw_dir: str, username: str, raw_response: Any, backend: str) -> Tuple[str, str]:
    try:
        os.makedirs(raw_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{username}_{backend}_{ts}.json"
        filepath = os.path.join(raw_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(raw_response, f, ensure_ascii=False, indent=2, default=str)
        return filepath, filename
    except Exception as e:
        logger.error(f"Failed to save raw response: {e}")
        return "", ""


def check_profile(
    username: str,
    config: Dict[str, Any],
    transport_name: Optional[str] = None,
    should_stop=None,
) -> Dict[str, Any]:
    if transport_name is None:
        transport_name = config.get("transport", {}).get("primary", "curl") or "curl"
    assert transport_name is not None

    user_agent = get_user_agent(config)
    headers = build_headers(user_agent)
    url = build_api_url(username)
    timeout = config.get("request_timeout", 30)
    proxy_url = build_proxy_url(config)
    proxy_enabled = proxy_url is not None

    retry_cfg = config.get("retry", {})
    max_retries = retry_cfg.get("attempts", 3) if retry_cfg.get("enabled") else 1
    backoff_list = retry_cfg.get("backoff_seconds", [5, 15, 45])

    result = {
        "username": username,
        "transport": transport_name,
        "proxy_enabled": proxy_enabled,
        "user_agent": user_agent,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "success": False,
        "status_code": None,
        "latency_ms": 0,
        "classification": "ERROR",
        "raw_response": None,
        "headers": None,
        "error_message": None,
        "exception_type": None,
        "traceback": None,
        "response_size": 0,
        "response_hash": "",
        "curl_stderr": None,
        "curl_exit_code": None,
        "command": None,
        "verified": None,
        "verification_transport": None,
    }

    last_error = None
    for attempt in range(max_retries):
        if should_stop and should_stop():
            result["error_message"] = "Shutdown requested"
            result["classification"] = "ERROR"
            return result

        try:
            tr = fetch_profile(transport_name, url, headers, timeout, proxy_url)

            result["latency_ms"] = tr.latency_ms
            result["status_code"] = tr.status_code
            result["headers"] = tr.headers
            result["response_size"] = tr.response_size
            result["response_hash"] = tr.response_hash
            result["success"] = tr.success
            result["curl_stderr"] = tr.stderr
            result["curl_exit_code"] = tr.exit_code
            result["command"] = tr.command

            if tr.error:
                result["error_message"] = tr.error
                result["exception_type"] = "TransportError"
                last_error = tr.error
                if attempt < max_retries - 1:
                    backoff = backoff_list[min(attempt, len(backoff_list) - 1)]
                    logger.warning(f"{transport_name} attempt {attempt + 1} failed for {username}: {tr.error}, retry in {backoff}s")
                    time.sleep(backoff)
                    continue
                result["classification"] = "ERROR"
                return result

            try:
                raw_response = json.loads(tr.body) if tr.body else {}
            except json.JSONDecodeError as e:
                result["error_message"] = f"JSON decode error: {e}"
                result["exception_type"] = "JSONDecodeError"
                result["classification"] = "UNKNOWN"
                return result

            result["raw_response"] = raw_response
            result["classification"] = classify_response(raw_response, tr.status_code)
            return result

        except Exception as e:
            last_error = str(e)
            result["exception_type"] = type(e).__name__
            result["error_message"] = str(e)
            import traceback
            result["traceback"] = traceback.format_exc()
            logger.error(f"Exception checking {username} with {transport_name}: {e}")
            if attempt < max_retries - 1:
                backoff = backoff_list[min(attempt, len(backoff_list) - 1)]
                logger.warning(f"Attempt {attempt + 1} failed for {username}, retry in {backoff}s")
                time.sleep(backoff)

    if last_error:
        result["error_message"] = last_error
    result["classification"] = "ERROR"
    return result


def check_profile_verify(
    username: str,
    config: Dict[str, Any],
    primary_result: Dict[str, Any],
) -> Dict[str, Any]:
    verify_cfg = config.get("transport", {}).get("verify_with", [])
    if not verify_cfg:
        return primary_result

    primary_class = primary_result.get("classification", "ERROR")
    if primary_class not in ("ACTIVE", "MISSING"):
        return primary_result

    for v_transport in verify_cfg:
        if v_transport == primary_result.get("transport"):
            continue

        v_result = check_profile(username, config, transport_name=v_transport)
        v_class = v_result.get("classification", "ERROR")

        if v_class == primary_class:
            primary_result["verified"] = True
            primary_result["verification_transport"] = v_transport
            logger.info(f"Verification: {username} {primary_class} confirmed by {v_transport}")
            return primary_result
        elif v_class in ("ACTIVE", "MISSING") and v_class != primary_class:
            primary_result["verified"] = False
            primary_result["verification_transport"] = v_transport
            logger.warning(f"Verification MISMATCH: {username} primary={primary_class} verify={v_class} ({v_transport})")
            return primary_result

    return primary_result


def check_playwright(
    username: str,
    config: Dict[str, Any],
    should_stop=None,
) -> Dict[str, Any]:
    result = {
        "username": username,
        "transport": "playwright",
        "proxy_enabled": False,
        "user_agent": "",
        "start_time": datetime.now(timezone.utc).isoformat(),
        "success": False,
        "status_code": None,
        "latency_ms": 0,
        "classification": "ERROR",
        "raw_response": None,
        "headers": None,
        "error_message": None,
        "exception_type": None,
        "traceback": None,
        "response_size": 0,
        "response_hash": "",
        "curl_stderr": None,
        "curl_exit_code": None,
        "screenshot": None,
        "verified": None,
        "verification_transport": None,
    }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["error_message"] = "playwright not installed"
        result["classification"] = "ERROR"
        return result

    url = f"https://www.instagram.com/{username}/"
    start = time.time()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=get_user_agent(config),
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            elapsed = (time.time() - start) * 1000
            result["latency_ms"] = elapsed
            result["success"] = True

            if response:
                result["status_code"] = response.status
                result["headers"] = dict(response.headers)

            page_content = page.content()
            result["response_size"] = len(page_content)

            result["classification"] = classify_playwright_response(page_content, result.get("status_code"))

            raw_dir = config.get("raw_responses_dir", "./output/raw_responses")
            os.makedirs(raw_dir, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            screenshot_path = os.path.join(raw_dir, f"{username}_playwright_{ts}.png")
            page.screenshot(path=screenshot_path)
            result["screenshot"] = screenshot_path

            result["raw_response"] = {
                "url": url,
                "title": page.title(),
                "content_length": len(page_content),
                "content_preview": page_content[:2000],
            }

            browser.close()

    except Exception as e:
        elapsed = (time.time() - start) * 1000
        result["latency_ms"] = elapsed
        result["error_message"] = str(e)
        result["exception_type"] = type(e).__name__
        import traceback
        result["traceback"] = traceback.format_exc()
        logger.error(f"Playwright error checking {username}: {e}")

    return result
