# __PROJECT_DISPLAY_NAME__

这是一个可在本地 macOS 与 Codex Desktop 中运行的__INDUSTRY_NAME__竞品监控项目。它会采集公开新闻线索，要求在发送前核验原文，生成钉钉 Markdown 日报，并使用 SQLite 防止重复播报。

## 当前口径

- 行业：__INDUSTRY_NAME__
- 地区：__REGION_SCOPE__
- 竞品：__COMPETITOR_COUNT__ 个
__COMPETITOR_LIST__
- 时间：__SCHEDULE_DESCRIPTION__
- 采集窗口：近 __LOOKBACK_DAYS__ 天
- 日报格式：__DIGEST_FORMAT_LABEL__，最多 __MAX_ITEMS__ 条

完整配置在 `config/monitoring.json`，原文核验规则在 `config/analysis_prompt.md`。

## 安装

需要 macOS 和 Python 3.11 或更高版本。在项目目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

## 安全配置钉钉凭据

不要把 Webhook 或加签密钥粘贴到聊天、命令参数、项目文件或截图中。运行中文终端向导：

```bash
./scripts/configure_dingtalk.sh
```

向导会逐步说明从哪里复制、应该粘贴什么、为什么输入不可见，以及格式错误后如何恢复。两项凭据只会通过标准输入写入 macOS 登录钥匙串，不会进入终端历史。配置后可再次校验，输出只显示是否已配置：

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli check-config
```

云端部署时可改用 Secret Manager 注入以下两个环境变量；两项必须同时存在：

```text
DINGTALK_WEBHOOK
DINGTALK_SECRET
```

## 采集与分析预览

采集公开线索并生成待核验 JSON，不会读取钉钉凭据或发送消息：

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli collect-json \
  --output data/analysis.json
```

按照 `config/analysis_prompt.md` 打开每条原始报道，删除不可靠或重复内容，并填写 JSON 中要求的摘要字段。随后校验并预览完整日报：

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli analysis-preview \
  --input data/analysis.json
```

未填必填字段、标题与指纹不一致、非 HTTPS 链接、重复报道或超出条数上限时，校验会失败。

## 连接测试

先预览完整测试消息。下面的命令不会读取凭据或发送：

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli preview \
  --title "__DIGEST_TITLE__｜连接测试" \
  --text $'## 机器人连接测试\n\n__PROJECT_DISPLAY_NAME__ 已完成配置。'
```

只有在 Codex 已展示目标群、完整标题、完整正文和 `@` 行为，并获得用户明确确认后，才可运行真实发送命令：

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli send \
  --title "已确认的标题" \
  --text "已确认的正文" \
  --confirm SEND_TO_DINGTALK
```

默认不 `@` 任何人。不要把 Webhook、access token 或加签密钥放入命令。

## 日报发送与去重

经过原文核验和预览确认的日报可由以下命令发送：

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli send-analysis \
  --input data/analysis.json \
  --confirm SEND_TO_DINGTALK
```

成功后才会写入 `data/state.sqlite3`。同一天重复执行不会再次发送，已发送文章也会按规范化标题指纹排除；失败会释放本次占位，允许重试。

首次测试消息和首次正式日报都确认收到后，再在 Codex Desktop 中创建定时任务。自动化每次都必须先核验来源、生成可发送日报，并使用上述受保护命令；不要用系统 `crontab` 保存任何凭据。
