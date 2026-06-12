import subprocess
import time
import json
import logging
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("instagram_monitor")


@dataclass
class BackendResult:
    backend: str
    success: bool
    status_code: Optional[int]
    body: str
    headers: Dict[str, str]
    latency_ms: float
    error: Optional[str] = None
    exit_code: Optional[int] = None
    stderr: Optional[str] = None


def requests_backend(
    url: str,
    headers: Dict[str, str],
    timeout: int,
    proxy_url: Optional[str] = None,
) -> BackendResult:
    import requests

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    start = time.time()
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, proxies=proxies)
        latency = (time.time() - start) * 1000
        return BackendResult(
            backend="requests",
            success=resp.status_code == 200,
            status_code=resp.status_code,
            body=resp.text,
            headers=dict(resp.headers),
            latency_ms=latency,
        )
    except Exception as e:
        latency = (time.time() - start) * 1000
        return BackendResult(
            backend="requests",
            success=False,
            status_code=None,
            body="",
            headers={},
            latency_ms=latency,
            error=str(e)[:500],
        )


def httpx_backend(
    url: str,
    headers: Dict[str, str],
    timeout: int,
    proxy_url: Optional[str] = None,
) -> BackendResult:
    import httpx

    start = time.time()
    try:
        with httpx.Client(http2=True, timeout=timeout, proxy=proxy_url) as client:
            resp = client.get(url, headers=headers)
            latency = (time.time() - start) * 1000
            return BackendResult(
                backend="httpx",
                success=resp.status_code == 200,
                status_code=resp.status_code,
                body=resp.text,
                headers=dict(resp.headers),
                latency_ms=latency,
            )
    except Exception as e:
        latency = (time.time() - start) * 1000
        return BackendResult(
            backend="httpx",
            success=False,
            status_code=None,
            body="",
            headers={},
            latency_ms=latency,
            error=str(e)[:500],
        )


def curl_backend(
    url: str,
    headers: Dict[str, str],
    timeout: int,
    proxy_url: Optional[str] = None,
) -> BackendResult:
    cmd = [
        "curl",
        "-s",               # silent
        "-S",               # show errors
        "-i",               # include response headers
        "--max-time", str(timeout),
        "--connect-timeout", str(timeout),
    ]

    if proxy_url:
        cmd.extend(["--proxy", proxy_url])

    for key, value in headers.items():
        cmd.extend(["-H", f"{key}: {value}"])

    cmd.append(url)

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        latency = (time.time() - start) * 1000

        raw = proc.stdout
        stderr_out = proc.stderr.strip()

        # Parse -i output: headers end with \r\n\r\n or \n\n, then body
        status_code = None
        resp_headers = {}
        body = ""

        # Try multiple separators
        for sep in ["\r\n\r\n", "\n\n"]:
            if sep in raw:
                header_part, body = raw.split(sep, 1)
                break
        else:
            # No separator found - try to find JSON start
            json_start = raw.find("\n\n")
            if json_start == -1:
                json_start = raw.find("\r\n\r\n")
            if json_start != -1:
                # Skip past the separator
                sep_len = 2 if raw[json_start:json_start+2] == "\n\n" else 4
                header_part = raw[:json_start]
                body = raw[json_start + sep_len:]
            else:
                header_part = raw
                body = ""

        for line in header_part.split("\n"):
            line = line.strip()
            if line.startswith("HTTP/"):
                parts = line.split(" ", 2)
                if len(parts) >= 2:
                    try:
                        status_code = int(parts[1])
                    except ValueError:
                        pass
            elif ":" in line:
                key, _, val = line.partition(":")
                resp_headers[key.strip()] = val.strip()

        return BackendResult(
            backend="curl",
            success=status_code == 200,
            status_code=status_code,
            body=body,
            headers=resp_headers,
            latency_ms=latency,
            exit_code=proc.returncode,
            stderr=stderr_out[:2000] if stderr_out else None,
        )
    except subprocess.TimeoutExpired:
        latency = (time.time() - start) * 1000
        return BackendResult(
            backend="curl",
            success=False,
            status_code=None,
            body="",
            headers={},
            latency_ms=latency,
            error="curl timeout",
            exit_code=-1,
        )
    except Exception as e:
        latency = (time.time() - start) * 1000
        return BackendResult(
            backend="curl",
            success=False,
            status_code=None,
            body="",
            headers={},
            latency_ms=latency,
            error=str(e)[:500],
            exit_code=-1,
        )


BACKENDS = {
    "requests": requests_backend,
    "httpx": httpx_backend,
    "curl": curl_backend,
}


def get_backend(name: str):
    if name not in BACKENDS:
        raise ValueError(f"Unknown backend: {name}. Available: {list(BACKENDS.keys())}")
    return BACKENDS[name]


def make_request(
    backend_name: str,
    url: str,
    headers: Dict[str, str],
    timeout: int,
    proxy_url: Optional[str] = None,
) -> BackendResult:
    backend_fn = get_backend(backend_name)
    return backend_fn(url, headers, timeout, proxy_url)
