#!/usr/bin/env python3
"""
Instagram Account Visibility Monitor - Overnight Test Harness

Usage:
    python main.py --config config.yaml
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
from checkers import check_profile, check_profile_verify, check_playwright, save_raw_response

logger = logging.getLogger("instagram_monitor")

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
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    root_logger.addHandler(console_handler)

    file_handler = logging.FileHandler(os.path.join(logs_dir, "monitor.log"), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s [%(name)s] %(message)s"))
    root_logger.addHandler(file_handler)


def load_config(config_path: str) -> Dict[str, Any]:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def ensure_dirs(config: Dict[str, Any]):
    for d in [
        config.get("output_dir", "./output"),
        config.get("raw_responses_dir", "./output/raw_responses"),
        config.get("logs_dir", "./output/logs"),
        config.get("screenshots", {}).get("dir", "./output/screenshots"),
    ]:
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
        self.transition_count = 0
        self.verify_mismatch_count = 0
        self.latencies: List[float] = []
        self.transport_counts: Dict[str, int] = {}
        self.transport_success: Dict[str, int] = {}
        self.transport_failure: Dict[str, int] = {}
        self.transport_latencies: Dict[str, List[float]] = {}

    def record_check(self, result: Dict[str, Any], transition: bool):
        self.total_requests += 1
        classification = result.get("classification", "ERROR")
        transport = result.get("transport", "unknown")

        self.transport_counts[transport] = self.transport_counts.get(transport, 0) + 1
        if result.get("success"):
            self.successful_requests += 1
            self.transport_success[transport] = self.transport_success.get(transport, 0) + 1
        else:
            self.failed_requests += 1
            self.transport_failure[transport] = self.transport_failure.get(transport, 0) + 1

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
            if len(self.latencies) > 1000:
                self.latencies = self.latencies[-500:]
            if transport not in self.transport_latencies:
                self.transport_latencies[transport] = []
            self.transport_latencies[transport].append(latency)
            if len(self.transport_latencies[transport]) > 1000:
                self.transport_latencies[transport] = self.transport_latencies[transport][-500:]

        if transition:
            self.transition_count += 1
        if result.get("verified") is False:
            self.verify_mismatch_count += 1

    def to_dict(self) -> Dict[str, Any]:
        latencies = self.latencies[-1000:] if self.latencies else [0]
        transport_summary = {}
        for t in self.transport_counts:
            t_lat = self.transport_latencies.get(t, [0])
            transport_summary[t] = {
                "total": self.transport_counts.get(t, 0),
                "success": self.transport_success.get(t, 0),
                "failure": self.transport_failure.get(t, 0),
                "latency_avg": sum(t_lat[-500:]) / len(t_lat[-500:]) if t_lat else 0,
            }

        return {
            "requests_total": self.total_requests,
            "requests_success": self.successful_requests,
            "requests_failed": self.failed_requests,
            "active_count": self.active_count,
            "missing_count": self.missing_count,
            "unknown_count": self.unknown_count,
            "error_count": self.error_count,
            "transition_count": self.transition_count,
            "verify_mismatch_count": self.verify_mismatch_count,
            "latency_avg": sum(latencies) / len(latencies) if latencies else 0,
            "latency_min": min(latencies) if latencies else 0,
            "latency_max": max(latencies) if latencies else 0,
            "transports": transport_summary,
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
    now = datetime.now(timezone.utc).isoformat()
    account_id = db.get_or_create_account(username)

    old_status = db.get_account_status(username)
    new_status = result.get("classification", "ERROR")
    has_been_checked = old_status is not None and db.was_account_checked(account_id)
    transition = has_been_checked and old_status != new_status

    if transition:
        logger.info(f"TRANSITION: {username} {old_status} -> {new_status}")

    raw_response_path = ""
    raw_response_blob = ""
    response_hash = result.get("response_hash", "")
    if config.get("save_raw_responses", True) and result.get("raw_response") is not None:
        raw_dir = config.get("raw_responses_dir", "./output/raw_responses")
        raw_response_path, _ = save_raw_response(
            raw_dir, username, result["raw_response"], result.get("transport", "unknown")
        )
        if not raw_response_path:
            raw_response_blob = json.dumps(result["raw_response"], ensure_ascii=False, default=str)[:50000]

    transport_name = result.get("transport", "unknown")

    check_data = {
        "account_id": account_id,
        "run_id": run_id,
        "timestamp": now,
        "mode": transport_name,
        "transport": transport_name,
        "backend": transport_name,
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
        "retry_count": 0,
        "response_size": result.get("response_size", 0),
        "response_hash": response_hash,
        "screenshot_path": result.get("screenshot"),
        "curl_stderr": result.get("curl_stderr"),
        "curl_exit_code": result.get("curl_exit_code"),
        "verified": 1 if result.get("verified") else (0 if result.get("verified") is not None else None),
        "verification_transport": result.get("verification_transport"),
    }

    try:
        check_id = db.save_check(check_data)
    except Exception as e:
        logger.error(f"Failed to save check to DB for {username}: {e}")
        check_id = None

    try:
        db.update_account_status(account_id, new_status, is_error=(new_status == "ERROR"))
    except Exception as e:
        logger.error(f"Failed to update account status for {username}: {e}")

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

    if transition:
        event_data = {
            "account_id": account_id,
            "check_id": check_id,
            "old_status": old_status,
            "new_status": new_status,
            "timestamp": now,
            "reason": f"Status changed from {old_status} to {new_status} via {result.get('transport')}",
            "screenshot_path": result.get("screenshot") or "",
        }
        try:
            db.save_event(event_data)
        except Exception as e:
            logger.error(f"Failed to save event for {username}: {e}")

    log_entry = {**check_data, "old_status": old_status, "transition": transition, "run_id": run_id}
    log_entry.pop("traceback", None)
    log_entry.pop("raw_response_blob", None)
    write_jsonl_log(config.get("logs_dir", "./output/logs"), log_entry)

    metrics.record_check(result, transition)
    return transition


def save_periodic_metrics(db: Database, config: Dict[str, Any], metrics: MetricsTracker, run_id: str):
    try:
        data = metrics.to_dict()
        data["transports_blob"] = json.dumps(data.pop("transports", {}), ensure_ascii=False)
        db.save_metrics({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "mode": config.get("mode", "unknown"),
            **data,
        })
    except Exception as e:
        logger.error(f"Failed to save metrics: {e}")


def save_checkpoint(db: Database, config: Dict[str, Any], iteration: int, run_id: str):
    try:
        db.save_checkpoint({
            "last_processed_index": iteration,
            "last_checkpoint_time": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "mode": config.get("mode", "unknown"),
            "counters_blob": json.dumps({"iteration": iteration}),
        })
    except Exception as e:
        logger.error(f"Failed to save checkpoint: {e}")


def log_heartbeat(metrics: MetricsTracker, iteration: int):
    m = metrics.to_dict()
    logger.info(
        f"HEARTBEAT iter={iteration} total={m['requests_total']} "
        f"ok={m['requests_success']} fail={m['requests_failed']} "
        f"ACTIVE={m['active_count']} MISSING={m['missing_count']} "
        f"UNKNOWN={m['unknown_count']} ERROR={m['error_count']} "
        f"transitions={m['transition_count']} mismatches={m['verify_mismatch_count']}"
    )
    for t, data in m.get("transports", {}).items():
        logger.info(f"  {t}: total={data['total']} ok={data['success']} fail={data['failure']} avg={data['latency_avg']:.0f}ms")


def run_monitor(config: Dict[str, Any]):
    global shutdown_requested, run_id

    ensure_dirs(config)
    db = Database(config.get("database_path", "./output/monitor.db"))

    accounts = config.get("accounts", [])
    if not accounts:
        logger.error("No accounts configured. Exiting.")
        return

    usernames = [a["username"] for a in accounts]
    transport_cfg = config.get("transport", {})
    primary = transport_cfg.get("primary", "curl")
    compare_all = transport_cfg.get("compare_all", False)
    verify_with = transport_cfg.get("verify_with", [])
    mode = config.get("mode", "api")

    logger.info(f"Starting monitor run_id={run_id} mode={mode} primary={primary} accounts={len(usernames)}")
    logger.info(f"Accounts: {', '.join(usernames)}")
    if compare_all:
        logger.info("COMPARE ALL MODE: running every transport per check")
    if verify_with:
        logger.info(f"VERIFY WITH: {verify_with}")

    checkpoint = db.load_checkpoint()
    if checkpoint:
        logger.info(f"Previous run: run_id={checkpoint.get('run_id')} last_checkpoint={checkpoint.get('last_checkpoint_time')}")

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

                if mode == "playwright":
                    result = check_playwright(username, config)
                    process_check_result(db, config, username, result, run_id, metrics)
                    logger.info(
                        f"  {username} [playwright]: {result.get('classification')} "
                        f"latency={result.get('latency_ms', 0):.0f}ms"
                    )

                elif compare_all:
                    all_transports = [primary] + [t for t in verify_with if t != primary]
                    for transport_name in all_transports:
                        result = check_profile(username, config, transport_name=transport_name)
                        process_check_result(db, config, username, result, run_id, metrics)
                        logger.info(
                            f"  {username} [{transport_name}]: {result.get('classification')} "
                            f"latency={result.get('latency_ms', 0):.0f}ms "
                            f"status_code={result.get('status_code', 'N/A')}"
                        )
                else:
                    result = check_profile(username, config, transport_name=primary)
                    result = check_profile_verify(username, config, result)
                    process_check_result(db, config, username, result, run_id, metrics)
                    logger.info(
                        f"  {username} [{primary}]: {result.get('classification')} "
                        f"latency={result.get('latency_ms', 0):.0f}ms "
                        f"status_code={result.get('status_code', 'N/A')} "
                        f"verified={result.get('verified', 'N/A')}"
                    )

            except Exception as e:
                logger.error(f"Unexpected error checking {username}: {e}")
                logger.debug(traceback.format_exc())

            if idx < len(usernames) - 1:
                delay = random.uniform(per_account_min, per_account_max)
                delay_end = time.time() + delay
                while time.time() < delay_end and not shutdown_requested:
                    time.sleep(min(1.0, max(0.1, delay_end - time.time())))

        logger.info(f"--- Iteration {iteration} complete ---")

        now = time.time()
        if now - last_heartbeat >= heartbeat_interval:
            log_heartbeat(metrics, iteration)
            last_heartbeat = now

        if now - last_checkpoint >= checkpoint_interval:
            save_periodic_metrics(db, config, metrics, run_id)
            save_checkpoint(db, config, iteration, run_id)
            last_checkpoint = now

        if max_runtime > 0:
            if (time.time() - start_time) / 3600 >= max_runtime:
                logger.info(f"Max runtime {max_runtime}h reached.")
                break

        sleep_time = random.uniform(sleep_min, sleep_max)
        jitter = config.get("jitter", 0.2)
        sleep_time += random.uniform(-jitter * sleep_time, jitter * sleep_time)
        logger.info(f"Sleeping {sleep_time:.1f}s before next iteration")
        sleep_end = time.time() + sleep_time
        while time.time() < sleep_end and not shutdown_requested:
            time.sleep(min(1.0, max(0.1, sleep_end - time.time())))

    logger.info("Saving final metrics and checkpoint...")
    save_periodic_metrics(db, config, metrics, run_id)
    save_checkpoint(db, config, iteration, run_id)

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
    logger.info(f"Verify mismatches: {m['verify_mismatch_count']}")
    logger.info(f"Avg latency: {m['latency_avg']:.0f}ms")
    for t, data in m.get("transports", {}).items():
        logger.info(f"  {t}: total={data['total']} ok={data['success']} fail={data['failure']} avg={data['latency_avg']:.0f}ms")

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
