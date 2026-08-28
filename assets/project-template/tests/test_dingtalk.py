from __future__ import annotations

import base64
import hashlib
import hmac
import ssl
import unittest
from urllib.parse import parse_qs, urlsplit

from competitor_monitor_bot.dingtalk import (
    build_markdown_payload,
    build_signed_webhook_url,
    build_ssl_context,
)


class SigningTests(unittest.TestCase):
    def test_ssl_context_requires_verified_hostnames(self) -> None:
        context = build_ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(len(context.get_ca_certs()), 0)

    def test_builds_hmac_sha256_signature_and_preserves_token(self) -> None:
        timestamp = 1_700_000_000_123
        secret = "SEC-test-signing-secret"
        webhook = (
            "https://oapi.dingtalk.com/robot/send?access_token=test-access-token"
        )

        signed_url = build_signed_webhook_url(
            webhook,
            secret,
            timestamp_ms=timestamp,
        )
        query = parse_qs(urlsplit(signed_url).query)

        expected = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                f"{timestamp}\n{secret}".encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("ascii")
        self.assertEqual(query["access_token"], ["test-access-token"])
        self.assertEqual(query["timestamp"], [str(timestamp)])
        self.assertEqual(query["sign"], [expected])

    def test_builds_markdown_payload_with_real_newlines(self) -> None:
        payload = build_markdown_payload(
            " 示例行业竞品日报 ",
            "## 今日动态\n\n第一条消息",
        )
        self.assertEqual(payload["markdown"]["title"], "示例行业竞品日报")
        self.assertIn("\n\n", payload["markdown"]["text"])
        self.assertFalse(payload["at"]["isAtAll"])

    def test_rejects_empty_message(self) -> None:
        with self.assertRaises(ValueError):
            build_markdown_payload("日报", "   ")


if __name__ == "__main__":
    unittest.main()
