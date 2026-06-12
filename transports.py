import subprocess
import time
import json
import logging
import hashlib
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("instagram_monitor")


@dataclass
class TransportResult:
    backend: str
    success: bool
    status_code: Optional[int]
    body: str
    headers: Dict[str, str]
    latency_ms: float
    response_size: int = 0
    response_hash: str = ""
    error: Optional[str] = None
    exit_code: Optional[int] = None
    stderr: Optional[str] = None
    command: Optional[str] = None


def build_proxy_args(proxy_url: Optional[str]) -> list:
    if not proxy_url:
        return []
    return ["--proxy", proxy_url]


def build_header_args(headers: Dict[str, str]) -> list:
    args = []
    for key, value in headers.items():
        args.extend(["-H", f"{key}: {value}"])
    return args


def parse_curl_output(raw: str) -> tuple:
    status_code = None
    resp_headers = {}
    body = ""

    if "\r\n\r\n" in raw:
        header_part, body = raw.split("\r\n\r\n", 1)
    elif "\n\n" in raw:
        header_part, body = raw.split("\n\n", 1)
    else:
        return None, {}, raw

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

    return status_code, resp_headers, body


def compute_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


# ─── CURL (subprocess) ───────────────────────────────────────────────

def transport_curl(
    url: str,
    headers: Dict[str, str],
    timeout: int,
    proxy_url: Optional[str] = None,
) -> TransportResult:
    cmd = [
        "curl", "-s", "-S", "-i",
        "--max-time", str(timeout),
        "--connect-timeout", str(min(timeout, 10)),
    ]
    cmd.extend(build_proxy_args(proxy_url))
    cmd.extend(build_header_args(headers))
    cmd.append(url)

    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        latency = (time.time() - start) * 1000
        raw = proc.stdout.decode("utf-8", errors="replace")
        stderr_out = proc.stderr.decode("utf-8", errors="replace").strip()
        status_code, resp_headers, body = parse_curl_output(raw)

        return TransportResult(
            backend="curl",
            success=status_code == 200,
            status_code=status_code,
            body=body,
            headers=resp_headers,
            latency_ms=latency,
            response_size=len(body),
            response_hash=compute_hash(body),
            exit_code=proc.returncode,
            stderr=stderr_out[:2000] if stderr_out else None,
            command=" ".join(cmd),
        )
    except subprocess.TimeoutExpired:
        latency = (time.time() - start) * 1000
        return TransportResult(
            backend="curl", success=False, status_code=None,
            body="", headers={}, latency_ms=latency,
            error="timeout", exit_code=-1,
        )
    except Exception as e:
        latency = (time.time() - start) * 1000
        return TransportResult(
            backend="curl", success=False, status_code=None,
            body="", headers={}, latency_ms=latency,
            error=str(e)[:500], exit_code=-1,
        )


# ─── PYCURL ──────────────────────────────────────────────────────────

def transport_pycurl(
    url: str,
    headers: Dict[str, str],
    timeout: int,
    proxy_url: Optional[str] = None,
) -> TransportResult:
    import pycurl
    from io import BytesIO

    buf = BytesIO()
    header_buf = BytesIO()
    c = pycurl.Curl()

    try:
        c.setopt(c.URL, url)
        c.setopt(c.WRITEDATA, buf)
        c.setopt(c.HEADERFUNCTION, header_buf.write)
        c.setopt(c.TIMEOUT, timeout)
        c.setopt(c.CONNECTTIMEOUT, min(timeout, 10))
        c.setopt(c.USERAGENT, headers.get("User-Agent", ""))
        c.setopt(c.HTTPHEADER, [f"{k}: {v}" for k, v in headers.items() if k != "User-Agent"])

        if proxy_url:
            c.setopt(c.PROXY, proxy_url)

        start = time.time()
        c.perform()
        latency = (time.time() - start) * 1000

        status_code = c.getinfo(pycurl.RESPONSE_CODE)
        body = buf.getvalue().decode("utf-8", errors="replace")
        raw_headers = header_buf.getvalue().decode("utf-8", errors="replace")

        resp_headers = {}
        for line in raw_headers.split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, val = line.partition(":")
                resp_headers[key.strip()] = val.strip()

        return TransportResult(
            backend="pycurl",
            success=status_code == 200,
            status_code=status_code,
            body=body,
            headers=resp_headers,
            latency_ms=latency,
            response_size=len(body),
            response_hash=compute_hash(body),
        )
    except pycurl.error as e:
        latency = (time.time() - start) * 1000 if "start" in dir() else 0
        return TransportResult(
            backend="pycurl", success=False, status_code=None,
            body="", headers={}, latency_ms=latency,
            error=str(e)[:500],
        )
    except Exception as e:
        latency = (time.time() - start) * 1000 if "start" in dir() else 0
        return TransportResult(
            backend="pycurl", success=False, status_code=None,
            body="", headers={}, latency_ms=latency,
            error=str(e)[:500],
        )
    finally:
        c.close()


# ─── CURL_CFFI ───────────────────────────────────────────────────────

def transport_curl_cffi(
    url: str,
    headers: Dict[str, str],
    timeout: int,
    proxy_url: Optional[str] = None,
) -> TransportResult:
    from curl_cffi import requests as cffi_requests

    start = time.time()
    try:
        resp = cffi_requests.get(
            url,
            headers=headers,
            timeout=timeout,
            proxies={"https": proxy_url, "http": proxy_url} if proxy_url else None,
            impersonate="chrome",
        )
        latency = (time.time() - start) * 1000
        body = resp.text
        resp_headers = dict(resp.headers)

        return TransportResult(
            backend="curl_cffi",
            success=resp.status_code == 200,
            status_code=resp.status_code,
            body=body,
            headers=resp_headers,
            latency_ms=latency,
            response_size=len(body),
            response_hash=compute_hash(body),
        )
    except Exception as e:
        latency = (time.time() - start) * 1000
        return TransportResult(
            backend="curl_cffi", success=False, status_code=None,
            body="", headers={}, latency_ms=latency,
            error=str(e)[:500],
        )


# ─── FACTORY ─────────────────────────────────────────────────────────

TRANSPORTS = {
    "curl": transport_curl,
    "pycurl": transport_pycurl,
    "curl_cffi": transport_curl_cffi,
}


def create_transport(name: str):
    if name not in TRANSPORTS:
        raise ValueError(f"Unknown transport: {name}. Available: {list(TRANSPORTS.keys())}")
    return TRANSPORTS[name]


def fetch_profile(
    transport_name: str,
    url: str,
    headers: Dict[str, str],
    timeout: int,
    proxy_url: Optional[str] = None,
) -> TransportResult:
    fn = create_transport(transport_name)
    return fn(url, headers, timeout, proxy_url)
