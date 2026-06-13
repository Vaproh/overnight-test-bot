"""Configuration system for the production monitoring bot."""

import os
from dataclasses import dataclass, field
from typing import List, Optional

import yaml


@dataclass
class ProxyConfig:
    enabled: bool = False
    server: str = ""
    username: str = ""
    password: str = ""

    def get_url(self) -> Optional[str]:
        if not self.enabled or not self.server:
            return None
        if self.username and self.password:
            if "://" in self.server:
                scheme, rest = self.server.split("://", 1)
                return f"{scheme}://{self.username}:{self.password}@{rest}"
            return f"http://{self.username}:{self.password}@{self.server}"
        return self.server


@dataclass
class RetryConfig:
    attempts: int = 3
    backoff_seconds: List[int] = field(default_factory=lambda: [5, 15, 45])


@dataclass
class PlaywrightConfig:
    enabled: bool = True
    headless: bool = True
    timeout: int = 30000


@dataclass
class InstagramAuth:
    enabled: bool = False
    cookies_path: str = "./data/cookies.json"


@dataclass
class Config:
    telegram_token: str = ""
    telegram_chat_id: str = ""
    check_interval: int = 300
    request_timeout: int = 30
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    playwright: PlaywrightConfig = field(default_factory=PlaywrightConfig)
    instagram_auth: InstagramAuth = field(default_factory=InstagramAuth)
    database_path: str = "./data/monitor.db"
    raw_responses_dir: str = "./data/raw_responses"
    logs_dir: str = "./data/logs"
    screenshots_dir: str = "./data/screenshots"
    log_level: str = "INFO"
    accounts: List[str] = field(default_factory=list)
    test_accounts: List[str] = field(default_factory=list)
    user_agent: str = "Instagram 320.0.0.0 Android (33; 33; SM-S908B; SM-S908B; 33; 33; exynos2200; en_US; 701237498)"

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        proxy_data = data.get("proxy", {})
        retry_data = data.get("retry", {})
        pw_data = data.get("playwright", {})
        ig_data = data.get("instagram_auth", {})

        return cls(
            telegram_token=data.get("telegram_token", ""),
            telegram_chat_id=data.get("telegram_chat_id", ""),
            check_interval=data.get("check_interval", 300),
            request_timeout=data.get("request_timeout", 30),
            proxy=ProxyConfig(
                enabled=proxy_data.get("enabled", False),
                server=proxy_data.get("server", ""),
                username=proxy_data.get("username", ""),
                password=proxy_data.get("password", ""),
            ),
            retry=RetryConfig(
                attempts=retry_data.get("attempts", 3),
                backoff_seconds=retry_data.get("backoff_seconds", [5, 15, 45]),
            ),
            playwright=PlaywrightConfig(
                enabled=pw_data.get("enabled", True),
                headless=pw_data.get("headless", True),
                timeout=pw_data.get("timeout", 30000),
            ),
            instagram_auth=InstagramAuth(
                enabled=ig_data.get("enabled", False),
                cookies_path=ig_data.get("cookies_path", "./data/cookies.json"),
            ),
            database_path=data.get("database_path", "./data/monitor.db"),
            raw_responses_dir=data.get("raw_responses_dir", "./data/raw_responses"),
            logs_dir=data.get("logs_dir", "./data/logs"),
            screenshots_dir=data.get("screenshots_dir", "./data/screenshots"),
            log_level=data.get("log_level", "INFO"),
            accounts=data.get("accounts", []),
            test_accounts=data.get("test_accounts", []),
            user_agent=data.get("user_agent", cls.user_agent),
        )
