# __PROJECT_DISPLAY_NAME__

这是一个可在本地 macOS 与 Codex Desktop 中运行的__INDUSTRY_NAME__竞品监控项目。它会采集公开新闻线索，要求在发送前核验原文，生成钉钉 Markdown 日报，并使用 SQLite 防止重复播报。

## 当前口径

- 行业：__INDUSTRY_NAME__
- 地区：__REGION_SCOPE__
- 竞品：__COMPETITOR_COUNT__ 个
__COMPETITOR_LIST__
- 时间：__SCHEDULE_DESCRIPTION__
- 结果复核：__RESULT_CHECK_DESCRIPTION__
- 采集窗口：近 __LOOKBACK_DAYS__ 天
- 日报格式：__DIGEST_FORMAT_LABEL__，最多 __MAX_ITEMS__ 条
- 发现来源：__DISCOVERY_SOURCE_LIST__（同级，不设主次）

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

由配置中启用的 Google News、百度搜索、360 搜索和微信公众号搜索同级采集公开线索并生成待核验 JSON，不会读取钉钉凭据或发送消息：

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli collect-json \
  --output data/analysis.json
```

四类来源都使用每个品牌的名称与全部已确认别名，不附加行业词；公众号不限官方账号。例如“猿编程”本身足够，不再要求同时命中“少儿编程”，海外品牌同样去掉额外的 coding kids 等限制。旧 query 字段的行业词不再用于收窄基础发现。同名噪声和行业相关性在原文核验时判断。登录、验证码或安全验证页面不得绕过；单一来源失败会记录脱敏原因并继续，所有启用来源都失败时停止生成日报。

`collect-json` 保留完整的可审阅候选池，条数可以超过日报上限；原文核验和事件去重后再选最多 __MAX_ITEMS__ 条。人事任免列为高优先级；未经官方确认的媒体报道须明确注明其消息性质。

按照 `config/analysis_prompt.md` 打开每条原始报道、公众号文章、竞品官网或官方公告，删除不可靠或重复内容，校正真实直链、来源、发布时间、日期精度和内容属性，并填写 JSON 中要求的摘要字段。随后校验并预览完整日报：

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli analysis-preview \
  --input data/analysis.json
```

未填必填字段、仍标记为未核验、标题与指纹不一致、非 HTTPS 链接、原文日期超窗、重复报道或超出条数上限时，校验会失败。只有至少一个来源成功且所有候选经原文核验后被排除，才允许生成空日报。

## 延期重要动态

`data/pending_articles.json` 保存已核验、但明确安排到未来播报的重要动态。`review_status=verified`、`queue_status=queued` 且到达 `send_after` 的条目会被 `collect-json` 强制合并进当期候选，即使搜索没有再次发现，也必须重新打开原文核验。未到日期不会提前发送；到期条目无法核验时必须停止，不能静默改成空日报。

从已完成原文核验的 `data/analysis.json` 条目复制文章字段，再添加以下队列字段；`fingerprint` 必须继续与标题匹配，日期使用真实来源日期：

```json
{
  "review_status": "verified",
  "queue_status": "queued",
  "send_after": "2026-09-03",
  "competitor_id": "example-competitor",
  "competitor_name": "示例竞品",
  "region": "domestic",
  "priority": 5,
  "title": "已核验的重要动态标题",
  "url": "https://example.com/original-article",
  "source": "真实来源名称",
  "source_url": "https://example.com",
  "published_at": "2026-09-01T00:00:00+08:00",
  "published_at_precision": "date",
  "content_type": "official_notice",
  "category": "法律/合规",
  "fingerprint": "由程序按标题生成的 64 位指纹"
}
```

发送成功后 `send-analysis` 才会把匹配队列条目标记为 `sent`。不要手工改写 SQLite 或跳过队列状态。

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

只有钉钉响应 `errcode=0` 后才会写入 `data/state.sqlite3` 并更新延期队列。同一天重复执行返回 `already_sent`，不会再次发送；已发送文章也会按规范化标题指纹排除。失败会释放本次占位，允许安全重试。

如用户明确要求同日追加遗漏动态，在原日报已成功的前提下，重新采集、核验，再以同一个 `--supplement-id` 依次运行 `analysis-preview`、`send-analysis` 与 `check-result`。补发标题标注“补充日报”，使用独立的 supplement_runs/supplement_articles 记录，不修改原日报，文章去重覆盖普通日报与补发。重复批次不再发送；没有新增返回 `no_new_articles` 而不发空补充消息。该参数仅用于用户明确授权的补发，定时任务不自动使用。

## 结果检查

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli check-result
```

命令按配置时区读取当天 SQLite 状态，不读取凭据、不联网、不发送，也不会创建缺失数据库。`sent`、`complete=true` 且退出码 0 表示成功记录、发送时间与文章去重记录一致；0 条空日报同样有效。`missing`、`in_progress`、`inconsistent`、`state_unavailable` 返回非零退出码，不能把漏执行、占位未完成或记录异常误判成成功。`not_scheduled` 表示不需要播报，退出码 0 但 `complete=false`。人工可通过 `--date YYYY-MM-DD` 检查历史，自动运行必须使用默认当天。

`send-analysis` 返回前会自动复核持久化结果，并输出 `delivery_check`。当日已发送也须核对真实记录；数据不一致时不自动重发。成功仅表示钉钉接口成功及状态一致，不表示群成员已阅读。

若需要稍后复核整轮漏执行的情况，在确认的规格中设置 `schedule.result_check_time`（同日、晚于播报时间，例如 10:30 播报、10:50 复核）。省略或设为 null 时，只做发送后的检查。`check-result` 的 `phase` 会按检查时刻返回 `delivery` 或 `result_check`；它只提供运行分支信息，不自行创建调度或发送消息。人工明确要求补发时可按完整核验流程执行，不受定时复核时段的限制。

一个会话只支持一个 heartbeat 自动化；将播报与复核放在该自动化中，复核触发只检查并报告，不自动补发。若两个时间跨小时，不要把小时列表与分钟列表直接组合，否则可能产生额外触发；使用平台支持的精确安排，或先确认可表达的复核时间。两次触发均依赖本机开机且 Codex 运行，结果检查不能保证永不漏执行。

首次测试消息和首次正式日报都确认收到后，再在 Codex Desktop 中创建定时任务。自动化每次都必须先核验来源、生成可发送日报，并使用上述受保护命令；不要用系统 `crontab` 保存任何凭据。
