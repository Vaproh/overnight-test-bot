"""Checker layer: curl_cffi primary, checker service verification, screenshot service."""

import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests as _requests

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
        os.makedirs(
            os.path.dirname(cookies_path) if os.path.dirname(cookies_path) else ".",
            exist_ok=True,
        )
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
        if (
            user_data.get("is_private") is not None
            or user_data.get("username") is not None
        ):
            return "ACTIVE"
        return "MISSING"
    return "MISSING"


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

        result.update(
            {
                "status_code": status_code,
                "latency_ms": latency_ms,
                "response_size": len(body),
                "response_hash": response_hash,
                "raw_response": raw_response,
                "classification": classification,
            }
        )

        if config.raw_responses_dir:
            result["raw_response_path"] = _save_raw_response(
                config.raw_responses_dir, username, raw_response, "curl_cffi"
            )

    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        result.update(
            {
                "latency_ms": latency_ms,
                "error_message": str(e)[:500],
            }
        )
        logger.error(f"curl_cffi error for {username}: {e}")

    return result


def generate_profile_card(
    username: str, config: Config, status: str = "unknown"
) -> dict:
    result = {
        "screenshot_path": None,
        "profile_data": {},
        "error": None,
    }

    service_url = config.screenshot_service_url
    if not service_url:
        result["error"] = "no_service"
        logger.warning("No screenshot_service_url configured, skipping profile card")
        return result

    try:
        url = f"{service_url.rstrip('/')}/profile/{username}"
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
                logger.info(f"Profile card generated for {username}")
            else:
                result["error"] = "not_image"
                logger.warning(
                    f"Profile card service returned non-image for {username}"
                )
        elif resp.status_code == 404:
            result["error"] = "profile_unavailable"
            logger.info(f"Profile card service: profile unavailable for {username}")
        elif resp.status_code == 400:
            result["error"] = "invalid_username"
            logger.warning(f"Profile card service: invalid username for {username}")
        elif resp.status_code == 429:
            result["error"] = "rate_limited"
            logger.warning(f"Profile card service: rate limited for {username}")
        elif resp.status_code == 500:
            result["error"] = "service_down"
            logger.error(f"Profile card service: internal error")
        else:
            result["error"] = f"http_{resp.status_code}"
            logger.warning(
                f"Profile card service returned {resp.status_code} for {username}"
            )

    except _requests.exceptions.ConnectionError:
        result["error"] = "connection_refused"
        logger.error(f"Profile card service unreachable at {service_url}")
    except _requests.exceptions.Timeout:
        result["error"] = "timeout"
        logger.error(f"Profile card service timed out for {username}")
    except Exception as e:
        result["error"] = "unknown"
        logger.error(f"Profile card service error for {username}: {e}")

    return result


def check_with_service(username: str, config: Config) -> Dict[str, Any]:
    """Verify account status via the standalone checker service (Playwright-based)."""
    result = {
        "username": username,
        "transport": "checker_service",
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

    service_url = config.checker_service_url
    if not service_url:
        result["error_message"] = "no_checker_service_url"
        logger.warning("No checker_service_url configured, skipping verification")
        return result

    url = f"{service_url.rstrip('/')}/check/{username}"
    start = time.time()

    try:
        resp = _requests.get(url, timeout=30)
        latency_ms = (time.time() - start) * 1000

        if resp.status_code == 200:
            data = resp.json()
            state = data.get("state", "error")

            state_map = {
                "unavailable": "MISSING",
                "active": "ACTIVE",
                "private": "ACTIVE",
                "error": "ERROR",
            }
            classification = state_map.get(state, "ERROR")

            result.update(
                {
                    "status_code": 200,
                    "latency_ms": latency_ms,
                    "response_size": len(resp.content),
                    "response_hash": get_response_hash(data),
                    "raw_response": data,
                    "classification": classification,
                }
            )

            if config.raw_responses_dir:
                result["raw_response_path"] = _save_raw_response(
                    config.raw_responses_dir, username, data, "checker_service"
                )

            logger.info(
                f"Checker service: {username} -> state={state}, classification={classification}"
            )
        else:
            result.update(
                {
                    "status_code": resp.status_code,
                    "latency_ms": latency_ms,
                    "error_message": f"HTTP {resp.status_code}: {resp.text[:500]}",
                }
            )
            logger.warning(
                f"Checker service returned {resp.status_code} for {username}"
            )

    except _requests.exceptions.ConnectionError:
        latency_ms = (time.time() - start) * 1000
        result.update(
            {
                "latency_ms": latency_ms,
                "error_message": "connection_refused",
            }
        )
        logger.error(f"Checker service unreachable at {service_url}")
    except _requests.exceptions.Timeout:
        latency_ms = (time.time() - start) * 1000
        result.update(
            {
                "latency_ms": latency_ms,
                "error_message": "timeout",
            }
        )
        logger.error(f"Checker service timed out for {username}")
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        result.update(
            {
                "latency_ms": latency_ms,
                "error_message": str(e)[:500],
            }
        )
        logger.error(f"Checker service error for {username}: {e}")

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
            logger.warning(
                f"Rate limited for {username}, attempt {attempt + 1}/{max_retries}, retry in {backoff}s"
            )
            time.sleep(backoff)
            last_result = result
            continue

        if classification == "ERROR" and result.get("error_message"):
            err = result["error_message"].lower()
            if any(kw in err for kw in ["timeout", "connection", "proxy", "network"]):
                backoff = backoff_list[min(attempt, len(backoff_list) - 1)]
                logger.warning(
                    f"Retryable error for {username}: {result['error_message']}, retry in {backoff}s"
                )
                time.sleep(backoff)
                last_result = result
                continue

        return result

    return last_result or {
        "username": username,
        "classification": "ERROR",
        "error_message": "All retries exhausted",
    }


def verify_with_service(
    username: str, curl_result: Dict[str, Any], config: Config
) -> Dict[str, Any]:
    """Verify MISSING result via the checker service. Maps service states to monitor states."""
    if not config.playwright.enabled:
        return curl_result

    curl_class = curl_result.get("classification")
    if curl_class != "MISSING":
        return curl_result

    logger.info(f"Verifying {username} with checker service (curl said MISSING)")
    svc_result = check_with_service(username, config)
    svc_class = svc_result.get("classification")

    curl_result["verification_status"] = svc_class

    if svc_class == "MISSING":
        curl_result["classification"] = "MISSING"
        logger.info(f"Checker service confirms {username} is MISSING")
    elif svc_class == "ACTIVE":
        curl_result["classification"] = "SUSPECT"
        logger.warning(
            f"Disagreement: curl=MISSING, service=ACTIVE for {username} -> SUSPECT"
        )
    else:
        logger.warning(
            f"Checker service returned {svc_class} for {username}, keeping curl result"
        )
        raw = svc_result.get("raw_response", {})
        if isinstance(raw, dict):
            preview = raw.get("page_text", "")[:500]
            logger.debug(f"Page text for {username}: {preview}")

    return curl_result


def _save_raw_response(
    raw_dir: str, username: str, raw_response: Any, transport: str
) -> str:
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
