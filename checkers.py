import requests
import time
import random
import hashlib
import json
import os
import traceback
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, Callable

logger = logging.getLogger("instagram_monitor")


def get_user_agent(config: Dict[str, Any]) -> str:
    if config.get("fixed_user_agent"):
        return config["fixed_user_agent"]
    agents = config.get("user_agent_rotation", [])
    if not agents:
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    return random.choice(agents)


def build_api_url(username: str) -> str:
    return f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"


def build_headers(user_agent: str) -> Dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "X-IG-App-ID": "936619743392459",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/",
    }


def classify_api_response(data: Any, status_code: int) -> str:
    try:
        if status_code == 404:
            return "MISSING"
        if status_code == 401 or status_code == 403:
            return "UNKNOWN"
        if status_code == 429:
            return "UNKNOWN"
        if status_code >= 500:
            return "ERROR"

        if not isinstance(data, dict):
            return "UNKNOWN"

        user_data = data.get("data", {}).get("user")
        if user_data is None:
            if "message" in data:
                msg = data["message"].lower()
                if "not found" in msg or "doesn't exist" in msg:
                    return "MISSING"
            return "MISSING"

        if isinstance(user_data, dict):
            if user_data.get("is_private") is not None or user_data.get("full_name") is not None:
                return "ACTIVE"
            if user_data.get("error") or user_data.get("status") == "fail":
                return "UNKNOWN"

        return "ACTIVE"
    except Exception:
        return "UNKNOWN"


def classify_playwright_response(page_content: str, url: str) -> str:
    content_lower = page_content.lower()

    # Clearly missing
    if "sorry, this page isn't available" in content_lower:
        return "MISSING"
    if "the link you followed may be broken" in content_lower:
        return "MISSING"
    if "page not found" in content_lower and "instagram" in content_lower:
        return "MISSING"

    # Clearly active - look for Instagram-specific profile data markers
    if '"edge_followed_by"' in content_lower or '"followed_by"' in content_lower:
        return "ACTIVE"
    if '"edge_follow"' in content_lower or '"edge_followed_by"' in content_lower:
        return "ACTIVE"
    if '"profile_pic_url_hd"' in content_lower or '"profile_pic_url"' in content_lower:
        return "ACTIVE"
    if '"is_private":true' in content_lower or '"is_private":false' in content_lower:
        return "ACTIVE"
    if 'profilePage_' in content_lower and '"username"' in content_lower:
        return "ACTIVE"

    # Challenge or verification wall
    if "challenge" in content_lower and "instagram" in content_lower:
        return "UNKNOWN"
    if "/accounts/login" in content_lower and "checkpoint" in content_lower:
        return "UNKNOWN"

    # Rate limit
    if "rate limit" in content_lower or "too many requests" in content_lower:
        return "UNKNOWN"

    # Too small to be a real page
    if len(page_content) < 500:
        return "UNKNOWN"

    # Login page without profile data - ambiguous
    if "login" in content_lower and "sign up" in content_lower:
        return "UNKNOWN"

    return "UNKNOWN"


def save_raw_response(raw_dir: str, username: str, response_data: Any, mode: str) -> Tuple[str, str]:
    os.makedirs(raw_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{username}_{mode}_{timestamp}.json"
    filepath = os.path.join(raw_dir, filename)

    try:
        content = json.dumps(response_data, ensure_ascii=False, indent=2) if not isinstance(response_data, str) else response_data
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath, hashlib.sha256(content.encode()).hexdigest()
    except Exception as e:
        logger.error(f"Failed to save raw response: {e}")
        return "", ""


def check_api_direct(username: str, config: Dict[str, Any]) -> Dict[str, Any]:
    user_agent = get_user_agent(config)
    headers = build_headers(user_agent)
    url = build_api_url(username)
    timeout = config.get("request_timeout", 30)
    proxy_enabled = config.get("proxy", {}).get("enabled", False)

    proxies = None
    if proxy_enabled:
        proxy_cfg = config.get("proxy", {})
        proxy_url = proxy_cfg.get("server", "")
        if proxy_cfg.get("username") and proxy_cfg.get("password"):
            # Strip protocol prefix to get host:port
            host_port = proxy_url
            for prefix in ("https://", "http://", "socks5://", "socks4://"):
                if host_port.startswith(prefix):
                    host_port = host_port[len(prefix):]
                    break
            proto = "socks5" if "socks" in proxy_url else "http"
            proxy_url = f"{proto}://{proxy_cfg['username']}:{proxy_cfg['password']}@{host_port}"
        proxies = {"http": proxy_url, "https": proxy_url}

    start_time = time.time()
    result = {
        "username": username,
        "mode": "api_direct" if not proxy_enabled else "api_proxy",
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
    }

    try:
        response = requests.get(url, headers=headers, timeout=timeout, proxies=proxies)
        end_time = time.time()
        latency = (end_time - start_time) * 1000

        result["status_code"] = response.status_code
        result["latency_ms"] = latency
        result["headers"] = dict(response.headers)
        result["response_size"] = len(response.content)
        result["success"] = response.status_code == 200

        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {"raw_text": response.text[:5000]}

        result["raw_response"] = data
        result["classification"] = classify_api_response(data, response.status_code)

    except requests.exceptions.Timeout:
        end_time = time.time()
        result["latency_ms"] = (end_time - start_time) * 1000
        result["error_message"] = "Request timeout"
        result["exception_type"] = "Timeout"
        result["classification"] = "ERROR"
        result["traceback"] = traceback.format_exc()

    except requests.exceptions.ConnectionError as e:
        end_time = time.time()
        result["latency_ms"] = (end_time - start_time) * 1000
        result["error_message"] = str(e)[:500]
        result["exception_type"] = "ConnectionError"
        result["classification"] = "ERROR" if not proxy_enabled else "UNKNOWN"
        result["traceback"] = traceback.format_exc()

    except Exception as e:
        end_time = time.time()
        result["latency_ms"] = (end_time - start_time) * 1000
        result["error_message"] = str(e)[:500]
        result["exception_type"] = type(e).__name__
        result["classification"] = "ERROR"
        result["traceback"] = traceback.format_exc()

    return result


def check_playwright_direct(username: str, config: Dict[str, Any]) -> Dict[str, Any]:
    user_agent = get_user_agent(config)
    timeout = config.get("request_timeout", 30) * 1000
    proxy_enabled = config.get("proxy", {}).get("enabled", False)

    start_time = time.time()
    result = {
        "username": username,
        "mode": "playwright_direct" if not proxy_enabled else "playwright_proxy",
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
        "screenshot": None,
    }

    browser = None
    context = None
    page = None

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            launch_args = {
                "headless": True,
                "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            }

            proxy_cfg = None
            if proxy_enabled:
                proxy_settings = config.get("proxy", {})
                proxy_cfg = {"server": proxy_settings.get("server", "")}
                if proxy_settings.get("username") and proxy_settings.get("password"):
                    proxy_cfg["username"] = proxy_settings["username"]
                    proxy_cfg["password"] = proxy_settings["password"]
                launch_args["proxy"] = proxy_cfg

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            url = f"https://www.instagram.com/{username}/"
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)

            if response:
                result["status_code"] = response.status
                result["headers"] = dict(response.headers)

            page.wait_for_timeout(3000)

            page_content = page.content()
            result["raw_response"] = page_content
            result["response_size"] = len(page_content.encode("utf-8"))
            result["response_hash"] = hashlib.sha256(page_content.encode()).hexdigest()
            result["success"] = True
            result["classification"] = classify_playwright_response(page_content, url)

            if config.get("save_screenshots", False):
                should_screenshot = False
                if result["classification"] in ("UNKNOWN", "ERROR") and config.get("screenshot_on_unknown", True):
                    should_screenshot = True
                if config.get("screenshot_on_error", True) and result["classification"] == "ERROR":
                    should_screenshot = True

                if should_screenshot:
                    screenshots_dir = config.get("screenshots_dir", "./output/screenshots")
                    os.makedirs(screenshots_dir, exist_ok=True)
                    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    screenshot_path = os.path.join(screenshots_dir, f"{username}_{timestamp}.png")
                    page.screenshot(path=screenshot_path, full_page=True)
                    result["screenshot"] = screenshot_path

    except Exception as e:
        end_time = time.time()
        result["latency_ms"] = (end_time - start_time) * 1000
        result["error_message"] = str(e)[:500]
        result["exception_type"] = type(e).__name__
        result["classification"] = "ERROR"
        result["traceback"] = traceback.format_exc()
        return result

    finally:
        try:
            if page:
                page.close()
        except Exception:
            pass
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass

    end_time = time.time()
    result["latency_ms"] = (end_time - start_time) * 1000
    return result


def check_account(username: str, config: Dict[str, Any], should_stop: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    mode = config.get("mode", "api_direct")
    max_retries = config.get("retry_count", 3)
    backoff = config.get("retry_backoff", 2.0)

    for attempt in range(max_retries):
        if should_stop and should_stop():
            return {
                "username": username,
                "mode": mode,
                "classification": "ERROR",
                "error_message": "Shutdown requested during retry",
                "success": False,
                "retry_count": attempt,
                "latency_ms": 0,
                "start_time": datetime.now(timezone.utc).isoformat(),
            }
        try:
            if mode in ("api_direct", "api_proxy"):
                result = check_api_direct(username, config)
            elif mode in ("playwright_direct", "playwright_proxy"):
                result = check_playwright_direct(username, config)
            else:
                return {
                    "username": username,
                    "mode": mode,
                    "classification": "ERROR",
                    "error_message": f"Unknown mode: {mode}",
                    "success": False,
                }

            result["retry_count"] = attempt

            if result["classification"] == "ERROR" and attempt < max_retries - 1:
                wait_time = backoff * (2 ** attempt) + random.uniform(0, 1)
                logger.warning(f"Retry {attempt + 1}/{max_retries} for {username} after {wait_time:.1f}s")
                time.sleep(wait_time)
                continue

            return result

        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = backoff * (2 ** attempt) + random.uniform(0, 1)
                logger.warning(f"Retry {attempt + 1}/{max_retries} for {username} after error: {e}")
                time.sleep(wait_time)
                continue

            return {
                "username": username,
                "mode": mode,
                "classification": "ERROR",
                "error_message": str(e)[:500],
                "exception_type": type(e).__name__,
                "traceback": traceback.format_exc(),
                "success": False,
                "retry_count": attempt,
                "latency_ms": 0,
                "start_time": datetime.now(timezone.utc).isoformat(),
            }

    return {
        "username": username,
        "mode": mode,
        "classification": "ERROR",
        "error_message": "Max retries exceeded",
        "success": False,
        "retry_count": max_retries,
        "latency_ms": 0,
        "start_time": datetime.now(timezone.utc).isoformat(),
    }
