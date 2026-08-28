from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class MonitoringConfigError(RuntimeError):
    """Raised when the monitoring configuration is invalid."""


@dataclass(frozen=True)
class Competitor:
    id: str
    name: str
    region: str
    priority: int
    aliases: tuple[str, ...]
    query: str


@dataclass(frozen=True)
class DigestSettings:
    format: str
    max_items: int
    lookback_days: int
    preferred_domestic_items: int
    preferred_international_items: int


@dataclass(frozen=True)
class ScheduleSettings:
    timezone: str
    cron: str
    weekdays: tuple[int, ...]

    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class MonitoringConfig:
    industry_name: str
    digest_title: str
    schedule: ScheduleSettings
    digest: DigestSettings
    competitors: tuple[Competitor, ...]


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "monitoring.json"


def _positive_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise MonitoringConfigError(f"{key} 必须是正整数。")
    return value


def _nonnegative_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MonitoringConfigError(f"{key} 必须是非负整数。")
    return value


def load_monitoring_config(path: str | Path | None = None) -> MonitoringConfig:
    config_path = Path(path) if path else default_config_path()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitoringConfigError(
            f"无法读取监控配置：{config_path}"
        ) from exc

    industry_data = raw.get("industry", {})
    industry_name = industry_data.get("name")
    digest_title = industry_data.get("digest_title")
    if not isinstance(industry_name, str) or not industry_name.strip():
        raise MonitoringConfigError("必须填写行业名称。")
    if not isinstance(digest_title, str) or not digest_title.strip():
        raise MonitoringConfigError("必须填写日报标题。")

    schedule_data = raw.get("schedule", {})
    timezone = schedule_data.get("timezone")
    cron = schedule_data.get("cron")
    weekdays = schedule_data.get("weekdays")
    if not isinstance(timezone, str) or not isinstance(cron, str):
        raise MonitoringConfigError("必须填写时区和 Cron 表达式。")
    if (
        not isinstance(weekdays, list)
        or not weekdays
        or any(not isinstance(day, int) or day < 1 or day > 7 for day in weekdays)
    ):
        raise MonitoringConfigError("发送星期必须使用 1 至 7 的 ISO 星期数字。")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise MonitoringConfigError(f"无法识别时区：{timezone}") from exc

    digest_data = raw.get("digest", {})
    digest_format = digest_data.get("format", "analysis")
    if digest_format not in {"analysis", "brief"}:
        raise MonitoringConfigError("日报格式必须是 analysis 或 brief。")
    digest = DigestSettings(
        format=digest_format,
        max_items=_positive_int(digest_data, "max_items"),
        lookback_days=_positive_int(digest_data, "lookback_days"),
        preferred_domestic_items=_nonnegative_int(
            digest_data, "preferred_domestic_items"
        ),
        preferred_international_items=_nonnegative_int(
            digest_data, "preferred_international_items"
        ),
    )
    if (
        digest.preferred_domestic_items
        + digest.preferred_international_items
        != digest.max_items
    ):
        raise MonitoringConfigError(
            "国内与海外的优先条数之和必须等于 max_items。"
        )

    competitors_data = raw.get("competitors")
    if not isinstance(competitors_data, list) or not competitors_data:
        raise MonitoringConfigError("至少需要配置一个竞品。")

    competitors: list[Competitor] = []
    seen_ids: set[str] = set()
    for entry in competitors_data:
        if not isinstance(entry, dict):
            raise MonitoringConfigError("每个竞品都必须是 JSON 对象。")
        competitor_id = entry.get("id")
        aliases = entry.get("aliases")
        region = entry.get("region")
        if not isinstance(competitor_id, str) or not competitor_id:
            raise MonitoringConfigError("每个竞品都必须填写 id。")
        if competitor_id in seen_ids:
            raise MonitoringConfigError(f"竞品 id 重复：{competitor_id}")
        if region not in {"domestic", "international"}:
            raise MonitoringConfigError(
                f"竞品 {competitor_id} 的地区取值不支持：{region}"
            )
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) and alias for alias in aliases
        ):
            raise MonitoringConfigError(
                f"竞品 {competitor_id} 至少需要一个非空别名。"
            )
        seen_ids.add(competitor_id)
        competitors.append(
            Competitor(
                id=competitor_id,
                name=str(entry.get("name", "")).strip(),
                region=region,
                priority=_positive_int(entry, "priority"),
                aliases=tuple(aliases),
                query=str(entry.get("query", "")).strip(),
            )
        )
        if not competitors[-1].name or not competitors[-1].query:
            raise MonitoringConfigError(
                f"竞品 {competitor_id} 必须填写名称和搜索式。"
            )

    return MonitoringConfig(
        industry_name=industry_name.strip(),
        digest_title=digest_title.strip(),
        schedule=ScheduleSettings(
            timezone=timezone,
            cron=cron,
            weekdays=tuple(sorted(set(weekdays))),
        ),
        digest=digest,
        competitors=tuple(competitors),
    )
