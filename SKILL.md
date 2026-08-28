---
name: dingtalk-competitor-monitor
description: 逐步引导用户创建、配置、验证并定时运行适合任意行业的钉钉竞品监控机器人，生成完整可运行的本地 Python 项目，覆盖行业口径、国内外竞品与别名、新闻原文核验、分析版或快讯版日报、SQLite 去重、中文 Webhook 加签配置向导、真实消息确认和 Codex Desktop 自动化。用户提到钉钉竞品日报、行业情报机器人、Webhook 加签机器人、每日或每周竞品监控、定时群播报时使用。
---

# 钉钉竞品监控机器人

逐步完成需求澄清、项目生成、凭据配置、两轮真实验证和定时任务设置。生成完整可运行项目，不只给代码片段或说明。

## 必守边界

- 开始前检查是否有会实质改变结果的问题；有则先用 1-3 个简短问题确认。
- 先完整读取 [references/conversation-flow.md](references/conversation-flow.md) 和 [references/dingtalk-safety.md](references/dingtalk-safety.md)，再执行工作流。
- 永远不要索取、接收、回显或保存聊天中的 Webhook、access token 或加签密钥。用户主动贴出时要求先轮换。
- 生成与离线测试期间不得向钉钉发送消息。
- 任何人工测试或首次正式发送前，必须展示目标、完整消息和 `@` 行为，并等待明确确认。
- 保留用户已有机器人项目、凭据和自动化；不要覆盖非空目录或创建重复定时任务。

## 1. 收集需求

按照 `conversation-flow.md` 分组收集行业边界、关注动向、地区、竞品、别名、优先级、时间、星期、时区、回看窗口、条数配额和日报格式。说明原文核验、事实与推断分离、标题指纹加 SQLite 去重是默认质量规则。

信息完整后，展示包含派生项目 slug、绝对输出路径和查询式的完整摘要。等待用户明确确认生成。

## 2. 生成项目

确认后完整读取 [references/spec-schema.md](references/spec-schema.md)。在当前工作区的 `work/` 中写入不含凭据的规格 JSON。解析本 Skill 的绝对目录，并运行：

```bash
python3 <skill-dir>/scripts/scaffold_project.py \
  --spec <absolute-spec-path> \
  --output <confirmed-absolute-output-path>
```

脚手架报错时修正规格，不绕过校验。生成后创建 `.venv`、安装项目并运行全部单元测试。只运行 `preview`、`collect-preview` 或 `analysis-preview` 等无发送命令。

## 3. 配置凭据

把生成项目中的 `./scripts/configure_dingtalk.sh` 命令交给用户在终端运行。明确说明向导全程中文、输入不回显、最多重试三次，且不会发送消息。让用户只返回“配置校验通过”或不含凭据的错误信息。

这是强制停点。用户完成前不要继续测试发送，也不要尝试从会话历史复用旧凭据。

## 4. 验证连接和日报

配置通过后先运行本地 `preview`，完整展示连接测试的目标、标题、正文和“不 @ 任何人”。获得明确确认后才运行一次 `send`，然后等待用户确认收到。

接着采集首份日报，逐条打开原始来源，按生成项目的 `config/analysis_prompt.md` 核验和填写分析 JSON。运行 `analysis-preview` 并展示完整内容。再次确认目标、全文与 `@` 行为后才发送，随后等待用户确认收到。

## 5. 设置定时任务

只有连接测试和首份日报均确认收到后，完整读取 [references/automation-workflow.md](references/automation-workflow.md)。展示最终自动化摘要，让用户明确选择自动发送或只生成草稿，并等待确认。

使用 Codex App 原生自动化能力创建或更新任务，不写系统 `crontab`，不暴露原始调度表达式，不把凭据放入提示词。创建后核对自然语言时间、状态、项目路径和模式；不要为验证自动化而额外发送消息。

## 6. 交付

报告项目绝对路径、测试结果、凭据存储方式、钉钉两次验证状态、去重方式和自动化下一次运行时间。无法执行的环节要明确说明，不把“已生成草稿”表述为“已发送”。
