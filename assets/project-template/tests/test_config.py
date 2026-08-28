from __future__ import annotations

import unittest

from competitor_monitor_bot.config import ConfigError, validate_secret, validate_webhook


class ConfigValidationTests(unittest.TestCase):
    def test_accepts_expected_dingtalk_webhook(self) -> None:
        validate_webhook(
            "https://oapi.dingtalk.com/robot/send?access_token="
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )

    def test_rejects_non_dingtalk_host(self) -> None:
        with self.assertRaises(ConfigError):
            validate_webhook(
                "https://example.com/robot/send?access_token=test-token"
            )

    def test_rejects_missing_access_token(self) -> None:
        with self.assertRaises(ConfigError):
            validate_webhook("https://oapi.dingtalk.com/robot/send")

    def test_rejects_markdown_link_inside_access_token(self) -> None:
        malformed = (
            "https://oapi.dingtalk.com/robot/send?access_token="
            "0123456789abcdef0123456789abcdef"
            "](https://oapi.dingtalk.com/robot/send?access_token="
            "0123456789abcdef0123456789abcdef)"
        )
        with self.assertRaises(ConfigError):
            validate_webhook(malformed)

    def test_validates_secret_prefix_without_leaking_value(self) -> None:
        invalid_secret = "not-a-dingtalk-secret"
        with self.assertRaises(ConfigError) as context:
            validate_secret(invalid_secret)
        self.assertNotIn(invalid_secret, str(context.exception))


if __name__ == "__main__":
    unittest.main()
