#!/usr/bin/env python3
"""
Instagram Account Visibility Monitor - Overnight Test Harness

Usage:
    python main.py --config config.yaml

Run modes:
    - api_direct: Direct API requests
    - api_proxy: API requests through proxy
    - playwright_direct: Playwright browser direct
    - playwright_proxy: Playwright browser through proxy
"""

import argparse
import json
import logging
import os
import random
import signal
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import yaml

from db import Database
from checkers import check_account, save_raw_response, get_user_agent

logger = logging.getLogger("instagram_monitor")

# Global state for graceful shutdown
shutdown_requested = False
run_id = str(uuid.uuid4())[:8]


def signal_handler(signum, frame):
    global shutdown_requested
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_requested = True


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def setup_logging(config: Dict[str, Any]):
    log_level = config.get("logging_level", "INFO").upper()
    logs_dir = config.get("logs_dir", "./output/logs")
    os.makedirs(logs_dir, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))

    # Clear existing handlers to avoid duplicates on restart
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    file_handler = logging.FileHandler(os.path.join(logs_dir, "monitor.log"), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter("[%(asctime)s] %(levelname)s [%(name)s] %(message)s")
    file_handler.setFormatter(file_fmt)
    root_logger.addHandler(file_handler)


def load_config(config_path: str) -> Dict[str, Any]:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def ensure_dirs(config: Dict[str, Any]):
    dirs = [
        config.get("output_dir", "./output"),
        config.get("raw_responses_dir", "./output/raw_responses"),
        config.get("screenshots_dir", "./output/screenshots"),
        config.get("logs_dir", "./output/logs"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)


class MetricsTracker:
    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.active_count = 0
        self.missing_count = 0
        self.unknown_count = 0
        self.error_count = 0
        self.latencies: List[float] = []
        self.rate_limit_count = 0
        self.timeout_count = 0
        self.proxy_error_count = 0
        self.browser_error_count = 0
        self.transition_count = 0

    def record_check(self, result: Dict[str, Any], transition: bool):
        self.total_requests += 1
        classification = result.get("classification", "ERROR")

        if result.get("success", False):
            self.successful_requests += 1
        else:
            self.failed_requests += 1

        if classification == "ACTIVE":
            self.active_count += 1
        elif classification == "MISSING":
            self.missing_count += 1
        elif classification == "UNKNOWN":
            self.unknown_count += 1
        elif classification == "ERROR":
            self.error_count += 1

        latency = result.get("latency_ms", 0)
        if latency > 0:
            self.latencies.append(latency)

        exc_type = result.get("exception_type", "")
        if exc_type == "Timeout" or result.get("error_message", "") == "Request timeout":
            self.timeout_count += 1
        if "rate limit" in str(result.get("error_message", "")).lower():
            self.rate_limit_count += 1
        if result.get("proxy_enabled") and result.get("classification") == "ERROR":
            self.proxy_error_count += 1
        if "playwright" in result.get("mode", "") and result.get("classification") == "ERROR":
            self.browser_error_count += 1
        if transition:
            self.transition_count += 1

    def to_dict(self) -> Dict[str, Any]:
        latencies = self.latencies[-1000:] if self.latencies else [0]
        return {
            "requests_total": self.total_requests,
            "requests_success": self.successful_requests,
            "requests_failed": self.failed_requests,
            "active_count": self.active_count,
            "missing_count": self.missing_count,
            "unknown_count": self.unknown_count,
            "error_count": self.error_count,
            "latency_avg": sum(latencies) / len(latencies) if latencies else 0,
            "latency_min": min(latencies) if latencies else 0,
            "latency_max": max(latencies) if latencies else 0,
            "rate_limit_count": self.rate_limit_count,
            "timeout_count": self.timeout_count,
            "proxy_error_count": self.proxy_error_count,
            "browser_error_count": self.browser_error_count,
            "transition_count": self.transition_count,
        }


def write_jsonl_log(logs_dir: str, data: Dict[str, Any]):
    try:
        os.makedirs(logs_dir, exist_ok=True)
        log_file = os.path.join(logs_dir, f"checks_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        logger.error(f"Failed to write JSONL log: {e}")


def process_check_result(
    db: Database,
    config: Dict[str, Any],
    username: str,
    result: Dict[str, Any],
    run_id: str,
    metrics: MetricsTracker,
) -> bool:
    """Process a single check result. Returns True if a transition occurred."""
    now = datetime.now(timezone.utc).isoformat()
    account_id = db.get_or_create_account(username)

    old_status = db.get_account_status(username)
    new_status = result.get("classification", "ERROR")
    # Don't treat the very first check as a transition (account just created, no prior check)
    has_been_checked_before = old_status is not None and db.was_account_checked(account_id)
    transition = has_been_checked_before and old_status != new_status

    if transition:
        logger.info(f"TRANSITION: {username} {old_status} -> {new_status}")

    # Save raw response if configured
    raw_response_path = ""
    raw_response_blob = ""
    response_hash = ""
    if config.get("save_raw_responses", True) and result.get("raw_response") is not None:
        raw_dir = config.get("raw_responses_dir", "./output/raw_responses")
        raw_response_path, response_hash = save_raw_response(
            raw_dir, username, result["raw_response"], result.get("mode", "unknown")
        )
        if not raw_response_path:
            raw_response_blob = json.dumps(result["raw_response"], ensure_ascii=False, default=str)[:50000]

    # Build check record
    check_data = {
        "account_id": account_id,
        "run_id": run_id,
        "timestamp": now,
        "mode": result.get("mode", "unknown"),
        "proxy_enabled": 1 if result.get("proxy_enabled") else 0,
        "user_agent": result.get("user_agent", ""),
        "status_code": result.get("status_code"),
        "latency_ms": result.get("latency_ms", 0),
        "success": 1 if result.get("success") else 0,
        "classification": new_status,
        "raw_response_path": raw_response_path,
        "raw_response_blob": raw_response_blob,
        "headers_blob": json.dumps(result.get("headers"), ensure_ascii=False, default=str) if result.get("headers") else None,
        "error_message": result.get("error_message"),
        "exception_type": result.get("exception_type"),
        "traceback": result.get("traceback"),
        "retry_count": result.get("retry_count", 0),
        "response_size": result.get("response_size", 0),
        "response_hash": response_hash,
        "screenshot_path": result.get("screenshot"),
    }

    try:
        check_id = db.save_check(check_data)
    except Exception as e:
        logger.error(f"Failed to save check to DB for {username}: {e}")
        check_id = None

    is_error = new_status in ("ERROR",)
    try:
        db.update_account_status(account_id, new_status, is_error=is_error)
    except Exception as e:
        logger.error(f"Failed to update account status for {username}: {e}")

    # Handle missing/restored tracking
    if new_status == "MISSING":
        try:
            db.set_missing_since(account_id, now)
        except Exception:
            pass
    elif old_status == "MISSING" and new_status != "MISSING":
        try:
            db.clear_missing_since(account_id)
        except Exception:
            pass

    # Save transition event
    if transition:
        screenshot_path = result.get("screenshot") or ""
        event_data = {
            "account_id": account_id,
            "check_id": check_id,
            "old_status": old_status,
            "new_status": new_status,
            "timestamp": now,
            "reason": f"Status changed from {old_status} to {new_status}",
            "screenshot_path": screenshot_path,
        }
        try:
            db.save_event(event_data)
        except Exception as e:
            logger.error(f"Failed to save event for {username}: {e}")

        # Take screenshot on transition if configured
        if config.get("save_screenshots") and config.get("screenshot_on_transition") and not result.get("screenshot"):
            logger.info(f"Screenshot requested on transition for {username} (would need re-check)")

    # Write JSONL log
    log_entry = {
        **check_data,
        "old_status": old_status,
        "transition": transition,
        "run_id": run_id,
    }
    log_entry.pop("traceback", None)
    log_entry.pop("raw_response_blob", None)
    write_jsonl_log(config.get("logs_dir", "./output/logs"), log_entry)

    # Record metrics
    metrics.record_check(result, transition)

    return transition


def save_periodic_metrics(db: Database, config: Dict[str, Any], metrics: MetricsTracker, run_id: str):
    now = datetime.now(timezone.utc).isoformat()
    metrics_data = {
        "timestamp": now,
        "run_id": run_id,
        "mode": config.get("mode", "unknown"),
        **metrics.to_dict(),
    }
    try:
        db.save_metrics(metrics_data)
    except Exception as e:
        logger.error(f"Failed to save metrics: {e}")


def save_checkpoint(db: Database, config: Dict[str, Any], index: int, run_id: str):
    now = datetime.now(timezone.utc).isoformat()
    checkpoint_data = {
        "last_processed_index": index,
        "last_checkpoint_time": now,
        "run_id": run_id,
        "mode": config.get("mode", "unknown"),
        "counters_blob": json.dumps({"checkpoint_index": index}),
    }
    try:
        db.save_checkpoint(checkpoint_data)
    except Exception as e:
        logger.error(f"Failed to save checkpoint: {e}")


def log_heartbeat(metrics: MetricsTracker, iteration: int):
    m = metrics.to_dict()
    logger.info(
        f"HEARTBEAT iter={iteration} total={m['requests_total']} "
        f"ok={m['requests_success']} fail={m['requests_failed']} "
        f"ACTIVE={m['active_count']} MISSING={m['missing_count']} "
        f"UNKNOWN={m['unknown_count']} ERROR={m['error_count']} "
        f"transitions={m['transition_count']}"
    )


def run_monitor(config: Dict[str, Any]):
    global shutdown_requested, run_id

    ensure_dirs(config)
    db_path = config.get("database_path", "./output/monitor.db")
    db = Database(db_path)

    accounts = config.get("accounts", [])
    if not accounts:
        logger.error("No accounts configured. Exiting.")
        return

    usernames = [a["username"] for a in accounts]
    logger.info(f"Starting monitor run_id={run_id} mode={config.get('mode')} accounts={len(usernames)}")
    logger.info(f"Accounts: {', '.join(usernames)}")

    # Log checkpoint info for debugging
    checkpoint = db.load_checkpoint()
    if checkpoint:
        logger.info(f"Previous run found: run_id={checkpoint.get('run_id')} last_checkpoint={checkpoint.get('last_checkpoint_time')}")

    max_runtime = config.get("max_runtime_hours", 0)
    start_time = time.time()
    iteration = 0
    metrics = MetricsTracker()
    last_heartbeat = time.time()
    last_checkpoint = time.time()

    heartbeat_interval = config.get("heartbeat_interval", 300)
    checkpoint_interval = config.get("checkpoint_interval", 60)
    sleep_min = config.get("random_sleep_min", 30)
    sleep_max = config.get("random_sleep_max", 90)
    per_account_min = config.get("per_account_delay_min", 5)
    per_account_max = config.get("per_account_delay_max", 15)

    while not shutdown_requested:
        iteration += 1
        logger.info(f"--- Iteration {iteration} start ---")

        for idx, username in enumerate(usernames):
            if shutdown_requested:
                break

            try:
                logger.info(f"Checking {username} ({idx + 1}/{len(usernames)})")
                result = check_account(username, config, should_stop=lambda: shutdown_requested)
                process_check_result(db, config, username, result, run_id, metrics)

                classification = result.get("classification", "ERROR")
                latency = result.get("latency_ms", 0)
                logger.info(
                    f"  {username}: {classification} "
                    f"latency={latency:.0f}ms "
                    f"status_code={result.get('status_code', 'N/A')} "
                    f"retries={result.get('retry_count', 0)}"
                )

            except Exception as e:
                logger.error(f"Unexpected error checking {username}: {e}")
                logger.debug(traceback.format_exc())

            # Per-account delay (interruptible)
            if idx < len(usernames) - 1:
                delay = random.uniform(per_account_min, per_account_max)
                logger.debug(f"Sleeping {delay:.1f}s before next account")
                delay_end = time.time() + delay
                while time.time() < delay_end and not shutdown_requested:
                    time.sleep(min(1.0, max(0.1, delay_end - time.time())))

        logger.info(f"--- Iteration {iteration} complete ---")

        # Periodic heartbeat
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            log_heartbeat(metrics, iteration)
            last_heartbeat = now

        # Periodic metrics save
        if now - last_checkpoint >= checkpoint_interval:
            save_periodic_metrics(db, config, metrics, run_id)
            save_checkpoint(db, config, iteration, run_id)
            last_checkpoint = now

        # Check max runtime
        if max_runtime > 0:
            elapsed_hours = (time.time() - start_time) / 3600
            if elapsed_hours >= max_runtime:
                logger.info(f"Max runtime {max_runtime}h reached. Stopping.")
                break

        # Sleep between iterations
        sleep_time = random.uniform(sleep_min, sleep_max)
        jitter = config.get("jitter", 0.2)
        sleep_time += random.uniform(-jitter * sleep_time, jitter * sleep_time)
        logger.info(f"Sleeping {sleep_time:.1f}s before next iteration")
        
        # Sleep in small chunks to allow graceful shutdown
        sleep_end = time.time() + sleep_time
        while time.time() < sleep_end and not shutdown_requested:
            remaining = sleep_end - time.time()
            time.sleep(min(1.0, max(0.1, remaining)))

    # Final save
    logger.info("Saving final metrics and checkpoint...")
    save_periodic_metrics(db, config, metrics, run_id)
    save_checkpoint(db, config, iteration, run_id)

    # Final summary
    m = metrics.to_dict()
    logger.info("=== FINAL SUMMARY ===")
    logger.info(f"Run ID: {run_id}")
    logger.info(f"Total requests: {m['requests_total']}")
    logger.info(f"Successful: {m['requests_success']}")
    logger.info(f"Failed: {m['requests_failed']}")
    logger.info(f"ACTIVE: {m['active_count']}")
    logger.info(f"MISSING: {m['missing_count']}")
    logger.info(f"UNKNOWN: {m['unknown_count']}")
    logger.info(f"ERROR: {m['error_count']}")
    logger.info(f"Transitions: {m['transition_count']}")
    logger.info(f"Avg latency: {m['latency_avg']:.0f}ms")
    logger.info(f"Rate limits: {m['rate_limit_count']}")
    logger.info(f"Timeouts: {m['timeout_count']}")

    db.close()
    logger.info("Monitor stopped.")


def main():
    parser = argparse.ArgumentParser(description="Instagram Account Visibility Monitor")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Config file not found: {args.config}")
        sys.exit(1)

    config = load_config(args.config)
    setup_logging(config)

    logger.info(f"Instagram Monitor starting with config: {args.config}")

    try:
        run_monitor(config)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        logger.critical(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
