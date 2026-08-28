from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "configure_dingtalk.py"
SPEC = importlib.util.spec_from_file_location("configure_dingtalk", SCRIPT_PATH)
assert SPEC and SPEC.loader
configure_dingtalk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_dingtalk)


class FriendlyCredentialGuidanceTests(unittest.TestCase):
    def test_explains_markdown_link_mistake_in_chinese(self) -> None:
        tip = configure_dingtalk.webhook_recovery_tip(
            "[Webhook](https://oapi.dingtalk.com/robot/send?access_token=example)"
        )
        self.assertIn("聊天消息里的超链接", tip)
        self.assertIn("机器人设置页", tip)

    def test_explains_missing_access_token_in_chinese(self) -> None:
        tip = configure_dingtalk.webhook_recovery_tip(
            "https://oapi.dingtalk.com/robot/send"
        )
        self.assertIn("缺少 access_token", tip)

    def test_explains_secret_prefix_in_chinese(self) -> None:
        tip = configure_dingtalk.secret_recovery_tip("wrong-secret")
        self.assertIn("以 SEC 开头", tip)

    @patch.object(configure_dingtalk.shutil, "which", return_value="/usr/bin/security")
    @patch.object(configure_dingtalk.subprocess, "run")
    def test_keychain_write_never_places_secret_in_arguments(
        self,
        run_mock,
        _which_mock,
    ) -> None:
        secret_value = "SEC-this-must-not-be-an-argument"
        run_mock.return_value.returncode = 0

        configure_dingtalk.store_keychain_value(
            "test.service",
            "测试凭据",
            secret_value,
        )

        command = run_mock.call_args.args[0]
        self.assertNotIn(secret_value, command)
        self.assertEqual(command[-1], "-w")
        self.assertEqual(run_mock.call_args.kwargs["input"], f"{secret_value}\n")


if __name__ == "__main__":
    unittest.main()
