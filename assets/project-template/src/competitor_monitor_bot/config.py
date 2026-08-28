from __future__ import annotations

from dataclasses import dataclass
import getpass
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit


WEBHOOK_ENV = "DINGTALK_WEBHOOK"
SECRET_ENV = "DINGTALK_SECRET"
WEBHOOK_KEYCHAIN_SERVICE = "dingtalk-competitor-monitor.__PROJECT_SLUG__.webhook"
SECRET_KEYCHAIN_SERVICE = "dingtalk-competitor-monitor.__PROJECT_SLUG__.secret"


class ConfigError(RuntimeError):
    """Raised when credentials are missing or invalid."""


@dataclass(frozen=True)
class DingTalkCredentials:
    webhook: str
    secret: str
    source: str


def validate_webhook(webhook: str) -> None:
    try:
        parsed = urlsplit(webhook)
    except ValueError as exc:
        raise ConfigError("Webhook 地址无法解析，请从钉钉机器人设置页重新复制。") from exc

    tokens = parse_qs(parsed.query).get("access_token", [])
    if (
        parsed.scheme != "https"
        or parsed.hostname != "oapi.dingtalk.com"
        or parsed.path != "/robot/send"
        or len(tokens) != 1
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", tokens[0])
    ):
        raise ConfigError(
            "Webhook 必须是 https://oapi.dingtalk.com/robot/send 地址，"
            "并且只包含一个纯文本 access_token；不要粘贴聊天中的超链接格式。"
        )


def validate_secret(secret: str) -> None:
    if not secret.startswith("SEC") or len(secret) < 16:
        raise ConfigError("加签密钥格式不正确：必须以 SEC 开头。")


def _read_keychain(service: str) -> str | None:
    security = shutil.which("security")
    if not security:
        return None

    result = subprocess.run(
        [
            security,
            "find-generic-password",
            "-a",
            getpass.getuser(),
            "-s",
            service,
            "-w",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip("\n")


def load_keychain_credentials() -> DingTalkCredentials:
    webhook = _read_keychain(WEBHOOK_KEYCHAIN_SERVICE)
    secret = _read_keychain(SECRET_KEYCHAIN_SERVICE)
    if not webhook or not secret:
        raise ConfigError("macOS 钥匙串中没有完整的钉钉机器人凭据。")
    validate_webhook(webhook)
    validate_secret(secret)
    return DingTalkCredentials(
        webhook=webhook,
        secret=secret,
        source="macOS Keychain",
    )


def load_credentials() -> DingTalkCredentials:
    webhook = os.environ.get(WEBHOOK_ENV)
    secret = os.environ.get(SECRET_ENV)

    if not webhook and not secret:
        try:
            return load_keychain_credentials()
        except ConfigError as exc:
            project_root = Path(__file__).resolve().parents[2]
            configure_script = project_root / "scripts" / "configure_dingtalk.sh"
            raise ConfigError(
                "尚未配置钉钉机器人凭据。请在终端运行中文配置向导："
                f"{configure_script}"
            ) from exc
    elif not webhook or not secret:
        raise ConfigError(
            f"{WEBHOOK_ENV} 与 {SECRET_ENV} 必须同时配置。"
        )

    validate_webhook(webhook)
    validate_secret(secret)
    return DingTalkCredentials(webhook=webhook, secret=secret, source="environment")
