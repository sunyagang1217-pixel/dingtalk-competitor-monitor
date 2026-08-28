from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import certifi

from .config import DingTalkCredentials


class DingTalkError(RuntimeError):
    """Raised when DingTalk rejects a message or the request fails."""


def build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def build_signed_webhook_url(
    webhook: str,
    secret: str,
    *,
    timestamp_ms: int | None = None,
) -> str:
    timestamp = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    ).decode("ascii")

    parsed = urlsplit(webhook)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"timestamp": str(timestamp), "sign": signature})
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def build_markdown_payload(
    title: str,
    text: str,
    *,
    at_all: bool = False,
) -> dict[str, Any]:
    clean_title = title.strip()
    clean_text = text.strip()
    if not clean_title:
        raise ValueError("消息标题不能为空。")
    if not clean_text:
        raise ValueError("消息正文不能为空。")

    return {
        "msgtype": "markdown",
        "markdown": {"title": clean_title, "text": clean_text},
        "at": {"isAtAll": at_all},
    }


class DingTalkClient:
    def __init__(
        self,
        credentials: DingTalkCredentials,
        timeout_seconds: int = 15,
        ssl_context: ssl.SSLContext | None = None,
    ):
        self._credentials = credentials
        self._timeout_seconds = timeout_seconds
        self._ssl_context = ssl_context or build_ssl_context()

    def send_markdown(
        self,
        title: str,
        text: str,
        *,
        at_all: bool = False,
    ) -> dict[str, Any]:
        payload = build_markdown_payload(title, text, at_all=at_all)
        signed_url = build_signed_webhook_url(
            self._credentials.webhook,
            self._credentials.secret,
        )
        request = Request(
            signed_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        try:
            with urlopen(
                request,
                timeout=self._timeout_seconds,
                context=self._ssl_context,
            ) as response:
                raw_response = response.read().decode("utf-8")
        except HTTPError as exc:
            raise DingTalkError(
                f"钉钉返回 HTTP 状态码 {exc.code}；凭据内容已隐藏。"
            ) from None
        except URLError as exc:
            reason_name = type(exc.reason).__name__
            raise DingTalkError(
                f"钉钉请求失败（{reason_name}）；凭据内容已隐藏。"
            ) from None

        try:
            result = json.loads(raw_response)
        except json.JSONDecodeError:
            raise DingTalkError("钉钉返回了无法解析的响应。") from None

        if result.get("errcode") != 0:
            error_code = result.get("errcode", "unknown")
            error_message = result.get("errmsg", "unknown error")
            raise DingTalkError(
                f"钉钉拒绝了消息：errcode={error_code}, "
                f"errmsg={error_message}"
            )
        return result
