#!/usr/bin/env python3
from __future__ import annotations

import getpass
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from competitor_monitor_bot.config import (  # noqa: E402
    ConfigError,
    SECRET_KEYCHAIN_SERVICE,
    WEBHOOK_KEYCHAIN_SERVICE,
    load_keychain_credentials,
    validate_secret,
    validate_webhook,
)


Validator = Callable[[str], None]


def webhook_recovery_tip(value: str) -> str:
    if "](" in value or value.startswith("["):
        return (
            "你粘贴的可能是聊天消息里的超链接。请回到钉钉群机器人设置页，"
            "点击复制 Webhook，不要从聊天记录复制。"
        )
    if not value.startswith("https://"):
        return "Webhook 必须以 https:// 开头，请确认没有漏掉地址开头。"
    if "access_token=" not in value:
        return "地址中缺少 access_token，请重新复制完整 Webhook。"
    return "请从钉钉群的自定义机器人设置页重新复制完整 Webhook。"


def secret_recovery_tip(value: str) -> str:
    if not value.startswith("SEC"):
        return "请复制安全设置中“加签”下方、以 SEC 开头的完整密钥。"
    return "加签密钥似乎不完整，请重新复制，不要手动删改字符。"


def prompt_hidden(
    *,
    step: str,
    label: str,
    guidance: str,
    validator: Validator,
    recovery_tip: Callable[[str], str],
    attempts: int = 3,
) -> str:
    print(f"\n--- {step}：{label} ---")
    print(guidance)
    print("为保护凭据，粘贴后终端不会显示任何字符，这是正常现象。")

    for attempt in range(1, attempts + 1):
        try:
            value = getpass.getpass(f"请粘贴{label}，然后按回车：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消配置，没有保存任何新凭据。", file=sys.stderr)
            raise SystemExit(130) from None

        try:
            validator(value)
        except ConfigError as exc:
            print(f"[未通过] {label}格式校验失败。", file=sys.stderr)
            print(f"原因：{exc}", file=sys.stderr)
            print(f"如何处理：{recovery_tip(value)}", file=sys.stderr)
            remaining = attempts - attempt
            if remaining:
                print(f"还可以重试 {remaining} 次。", file=sys.stderr)
            continue

        print(f"[通过] {label}格式正确，内容不会显示。")
        return value

    print(
        f"{label}连续 {attempts} 次未通过。没有保存任何新凭据，请检查后重新运行向导。",
        file=sys.stderr,
    )
    raise SystemExit(1)


def store_keychain_value(service: str, label: str, value: str) -> None:
    security = shutil.which("security")
    if not security:
        print(
            "当前系统没有 macOS 钥匙串命令，无法安全保存凭据。\n"
            "请不要把凭据写进代码或聊天；改用部署平台的 Secret Manager，"
            "并同时配置 DINGTALK_WEBHOOK 与 DINGTALK_SECRET。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    result = subprocess.run(
        [
            security,
            "add-generic-password",
            "-U",
            "-a",
            getpass.getuser(),
            "-s",
            service,
            "-l",
            label,
            "-w",
        ],
        check=False,
        input=f"{value}\n",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        print(
            "macOS 钥匙串拒绝写入。请确认登录钥匙串已解锁，然后重新运行向导。",
            file=sys.stderr,
        )
        raise SystemExit(1)


def main() -> int:
    print("=" * 58)
    print("__PROJECT_DISPLAY_NAME__ - 钉钉机器人安全配置向导")
    print("=" * 58)
    print("本向导共 4 步，大约需要 2 分钟。")
    print("本向导不会发送钉钉消息，也不会把凭据写入项目文件或终端历史。")
    print("如果凭据曾经出现在聊天、截图或公开文件中，请先在钉钉中轮换。")

    webhook = prompt_hidden(
        step="第 1/4 步",
        label="完整 Webhook 地址",
        guidance=(
            "打开钉钉群 -> 群设置 -> 机器人 -> 对应自定义机器人，复制完整 Webhook。\n"
            "正确地址以 https://oapi.dingtalk.com/robot/send?access_token= 开头。"
        ),
        validator=validate_webhook,
        recovery_tip=webhook_recovery_tip,
    )
    secret = prompt_hidden(
        step="第 2/4 步",
        label="加签密钥",
        guidance=(
            "在同一机器人安全设置中确认已启用“加签”，复制以 SEC 开头的完整密钥。\n"
            "不要输入 AppSecret、机器人名称或 access_token。"
        ),
        validator=validate_secret,
        recovery_tip=secret_recovery_tip,
    )

    print("\n--- 第 3/4 步：安全保存 ---")
    print("正在写入 macOS 登录钥匙串。终端不会显示凭据内容。")
    store_keychain_value(
        WEBHOOK_KEYCHAIN_SERVICE,
        "__PROJECT_DISPLAY_NAME__ Webhook",
        webhook,
    )
    store_keychain_value(
        SECRET_KEYCHAIN_SERVICE,
        "__PROJECT_DISPLAY_NAME__ signing secret",
        secret,
    )
    print("[通过] 两项凭据已写入 macOS 登录钥匙串。")

    print("\n--- 第 4/4 步：读取校验 ---")
    credentials = load_keychain_credentials()
    print("[通过] 配置校验通过。")
    print(f"凭据来源：{credentials.source}")
    print("Webhook：已配置（内容已隐藏）")
    print("加签密钥：已配置（内容已隐藏）")
    print("\n下一步：回到 Codex 对话，回复“配置校验通过”。")
    print("在看到待发送的完整测试消息并确认前，不要执行真实发送命令。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
