# 钉钉竞品监控 Skill

简体中文 | [English](README.md)

这是一个可复用的 Codex Skill，会从行业口径澄清开始，逐步帮助用户生成一套完整的本地 Python 竞品监控项目，并把经过原文核验、分析和去重的日报安全发送到钉钉群，而不是只返回零散代码片段。

![钉钉竞品监控 Skill 工作流](docs/skill-overview.png)

## 它能做什么

- 通过分步对话确认行业边界、地区、关注动向、竞品、别名和优先级。
- 适配任意行业，可同时覆盖国内和海外公开动向，不绑定固定竞品名单。
- Google News、百度搜索、360 搜索和微信公众号搜索四类来源同级采集，不区分主次。
- 微信公众号使用全部竞品名称及别名检索，覆盖所有提及文章，不限官方账号。
- 搜索结果只用于发现线索，发送前必须打开新闻原文、公众号文章、竞品官网或官方公告核验。
- 把事实与推断分开表达，支持分析版日报和简洁快讯版日报。
- 使用规范化标题指纹和 SQLite 状态防止重复播报。
- 支持把已核验的重要动态延期到指定发送日，并在发送前强制重新核验。
- 提供全中文钉钉 Webhook 与加签密钥终端配置向导。
- 本地凭据保存到 macOS 登录钥匙串，不进入对话、项目文件、日志或 Git。
- 每次连接测试和首份正式日报发送前，完整预览目标、消息全文和 `@` 行为。
- 只有两轮真实消息都确认收到后，才会创建 Codex Desktop 定时任务。

## 工作流程

| 阶段 | 结果 |
| --- | --- |
| 1. 定义口径 | 确认行业、地区、竞品、别名、优先级、时间和日报格式。 |
| 2. 生成项目 | 创建包含四源采集、原文核验、延期队列、发送、去重和测试的完整 Python 项目。 |
| 3. 配置凭据 | 在友好的中文终端流程中校验 Webhook 与加签密钥。 |
| 4. 两轮验证 | 分别预览并确认一条连接测试和一份分析日报。 |
| 5. 设置定时 | 在 Codex Desktop 中按工作日或每周生成草稿，或在授权后自动发送。 |

## 快速开始

### 环境要求

- 支持本地 Skill 的 Codex Desktop、Codex CLI 或 Codex IDE 扩展。
- 生成的机器人项目首版需要 macOS 和 Python 3.11 或更高版本。
- 拥有一个可以配置自定义机器人的钉钉群。
- 可以访问 GitHub；本仓库为公开仓库。

### 安装到个人 Skill 目录

把本仓库克隆到个人 Codex Skill 目录：

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
git clone https://github.com/sunyagang1217-pixel/dingtalk-competitor-monitor.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/dingtalk-competitor-monitor"
```

Codex 通常会自动检测 Skill 变化。如果列表中没有出现，重新启动 Codex。

### 启动分步创建流程

显式调用 Skill：

```text
使用 $dingtalk-competitor-monitor 帮我创建一个适合所在行业的
钉钉竞品监控机器人，并一步步引导我完成。
```

Skill 会先询问监控范围和发送计划，然后展示完整的派生规格；只有你确认后才会生成文件。

项目生成完成后，在自己的终端中运行中文凭据向导：

```bash
./scripts/configure_dingtalk.sh
```

不要把 Webhook、access token 或加签密钥粘贴到对话中。向导会隐藏输入，识别常见复制错误，并把两项凭据写入 macOS 登录钥匙串。

## 生成项目包含什么

| 组件 | 用途 |
| --- | --- |
| `config/monitoring.json` | 行业范围、竞品、别名、优先级、地区和时间配置。 |
| `config/analysis_prompt.md` | 新闻原文核验和日报撰写规则。 |
| `data/pending_articles.json` | 已核验但需延期播报的重要候选队列。 |
| `src/competitor_monitor_bot/` | 四源采集、分析、延期、钉钉加签、发送和 SQLite 状态管理。 |
| `scripts/configure_dingtalk.sh` | 全中文凭据配置与校验向导。 |
| `tests/` | 覆盖配置、加签、分析、采集、发送和去重的单元测试。 |

生成项目中的常用无发送命令：

```bash
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli check-config
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli collect-json \
  --output data/analysis.json
PYTHONPATH=src .venv/bin/python -m competitor_monitor_bot.cli analysis-preview \
  --input data/analysis.json
```

`collect-json` 会输出启用来源、成功来源和脱敏失败原因。候选的搜索时间与内容属性默认标为未核验，只有校正为真实原文日期和受支持的内容类型后才能通过 `analysis-preview`。这些命令不会发送钉钉消息；真实发送仍受分步流程中的明确确认保护。

## 安全边界

| 边界 | 行为 |
| --- | --- |
| 凭据 | 不在对话、文件、截图、日志或 Git 中索取或保存 Webhook、Token 和加签密钥。 |
| 来源 | 四类来源同级；登录或安全验证不绕过。全部来源失败时停发，不能伪装成空日报。 |
| 核验 | 原文无法读取、时间超窗或正文不支持标题结论时删除；公众号记录真实名称和内容属性。 |
| 内容 | 把核验事实与判断分开；企业自述明确标成品牌内容，法院和监管公告注明公告性质与主体。 |
| 延期 | 到期条目必须重新核验，不能静默删除；钉钉成功后才把队列状态改为 `sent`。 |
| 发送 | 真实发送前展示绑定群目标、完整标题、完整正文和 `@` 行为。 |
| `@` 行为 | 默认不 `@` 任何人。 |
| 去重 | 仅在发送成功后记录状态；拦截同日重复运行和跨期重复文章。 |
| 自动化 | 未明确授权已确认的范围与时间时，只生成草稿，不进行无人值守发送。 |

## 仓库结构

```text
.
├── SKILL.md                         # Codex 运行时工作流
├── agents/openai.yaml               # Skill 展示元数据
├── assets/project-template/         # 生成的 Python 项目模板
├── references/                      # 对话、安全和自动化规则
├── scripts/scaffold_project.py      # 确定性的项目脚手架生成器
└── docs/skill-overview.png          # README 使用的工作流总览图
```

## 开发与校验

在仓库根目录运行项目模板的完整测试：

```bash
PYTHONPATH=assets/project-template/src python3 -m unittest discover \
  -s assets/project-template/tests -v
python3 -m unittest discover -s tests -v
python3 -m compileall -q assets/project-template/src \
  assets/project-template/tests scripts tests
```

修改 `SKILL.md` 或 `agents/openai.yaml` 后，还应使用 Codex `skill-creator` Skill 自带的 `quick_validate.py` 校验整个 Skill 目录。

## 官方参考

- [为 ChatGPT 和 Codex 构建 Skill](https://learn.chatgpt.com/docs/build-skills)
- [钉钉机器人概述](https://open.dingtalk.com/document/orgapp/robot-overview)
- [钉钉自定义机器人接入](https://open.dingtalk.com/document/robots/custom-robot-access)
