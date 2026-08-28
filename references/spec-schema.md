# 项目规格

在生成项目之前，把已经与用户确认的口径写入一个不含凭据的 JSON 文件。使用 `scripts/scaffold_project.py` 做确定性校验和生成，不要手写 `monitoring.json`。

## 安全边界

- 规格文件中禁止出现 Webhook、`access_token`、加签密钥或任何 `SEC...` 值。
- 凭据只在项目生成后由用户本人通过中文终端向导录入。
- 输出目录必须不存在或为空；脚手架会拒绝覆盖已有文件。
- 所有 ID 与项目 slug 使用小写字母、数字和单个连字符。

## 完整示例

下面使用虚构竞品，仅用于说明结构：

```json
{
  "project": {
    "slug": "example-industry-monitor",
    "display_name": "示例行业竞品监控机器人"
  },
  "industry": {
    "name": "示例行业",
    "digest_title": "示例行业竞品日报"
  },
  "regions": ["domestic", "international"],
  "schedule": {
    "timezone": "Asia/Shanghai",
    "time": "10:30",
    "weekdays": [1, 2, 3, 4, 5]
  },
  "digest": {
    "format": "analysis",
    "max_items": 6,
    "lookback_days": 7,
    "preferred_domestic_items": 4,
    "preferred_international_items": 2
  },
  "competitors": [
    {
      "id": "example-tech",
      "name": "示例科技",
      "region": "domestic",
      "priority": 5,
      "aliases": ["示例科技", "示例科技公司"],
      "query": "\"示例科技\" OR \"示例科技公司\""
    },
    {
      "id": "example-labs",
      "name": "Example Labs",
      "region": "international",
      "priority": 4,
      "aliases": ["Example Labs", "ExampleLabs"],
      "query": "\"Example Labs\" OR ExampleLabs"
    }
  ]
}
```

## 字段规则

| 字段 | 规则 |
|---|---|
| `project.slug` | 1-63 字符，小写字母、数字、连字符 |
| `project.display_name` | 2-80 字符，用户可识别的项目名称 |
| `industry.name` | 明确到实际赛道，不用过宽泛的上位概念 |
| `industry.digest_title` | 钉钉消息主标题，不含日期 |
| `regions` | `domestic`、`international` 至少一个；每个已选地区至少配置一个竞品 |
| `schedule.time` | 24 小时制 `HH:MM` |
| `schedule.weekdays` | ISO 星期：周一为 1，周日为 7；不可重复 |
| `digest.format` | `analysis` 为事实摘要加影响判断；`brief` 为事实快讯 |
| `digest.max_items` | 1-20 |
| `digest.lookback_days` | 1-30 |
| 地区优先条数 | 两项之和必须等于 `max_items`；未选地区必须为 0 |
| `competitor.id` | 唯一、稳定的 ASCII slug |
| `competitor.priority` | 1-5，5 为最高优先级 |
| `competitor.aliases` | 至少包含正式名称；加入英文名、旧名、产品名等真实别名 |
| `competitor.query` | 必须包含至少一个别名，可使用引号和 `OR` 缩小噪声 |

## 生成命令

从 Skill 目录运行：

```bash
python3 scripts/scaffold_project.py \
  --spec /absolute/path/to/spec.json \
  --output /absolute/path/to/new-project
```

生成后检查终端中的中文摘要。脚手架只生成本地文件，不读取钉钉凭据，也不发送任何消息。
