from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class MonitoringConfigError(RuntimeError):
    """Raised when the monitoring configuration is invalid."""


SUPPORTED_DISCOVERY_SOURCES = {
    "google_news",
    "baidu",
    "360",
    "wechat_articles",
}


@dataclass(frozen=True)
class Competitor:
    id: str
    name: str
    region: str
    priority: int
    aliases: tuple[str, ...]
    query: str


@dataclass(frozen=True)
class DiscoverySource:
    id: str
    name: str
    enabled: bool
    scope: str
    regions: tuple[str, ...]

    def supports(self, competitor: Competitor) -> bool:
        return competitor.region in self.regions


@dataclass(frozen=True)
class VerificationSettings:
    require_original: bool
    allow_official_public_notices: bool
    promotional_content_label_required: bool
    exclude_unreadable: bool
    empty_digest_requires_successful_source: bool


@dataclass(frozen=True)
class CarryoverSettings:
    enabled: bool
    path: Path
    reverify_before_send: bool
    send_after_next_scheduled_run: bool


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
    discovery_sources: tuple[DiscoverySource, ...]
    verification: VerificationSettings
    carryover: CarryoverSettings


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


def _boolean(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise MonitoringConfigError(f"{key} 必须是布尔值。")
    return value


def _load_discovery_sources(raw: dict[str, Any]) -> tuple[DiscoverySource, ...]:
    sources_root = raw.get("sources")
    if not isinstance(sources_root, dict):
        raise MonitoringConfigError("sources 必须是 JSON 对象。")
    sources_data = sources_root.get("discovery")
    if not isinstance(sources_data, list) or not sources_data:
        raise MonitoringConfigError("至少需要配置一个发现来源。")

    discovery_sources: list[DiscoverySource] = []
    seen_ids: set[str] = set()
    for entry in sources_data:
        if not isinstance(entry, dict):
            raise MonitoringConfigError("每个发现来源都必须是 JSON 对象。")
        source_id = entry.get("id")
        name = entry.get("name")
        enabled = entry.get("enabled")
        scope = entry.get("scope")
        regions = entry.get("regions")
        if not isinstance(source_id, str) or source_id not in SUPPORTED_DISCOVERY_SOURCES:
            raise MonitoringConfigError(f"不支持的发现来源：{source_id}。")
        if source_id in seen_ids:
            raise MonitoringConfigError(f"发现来源重复：{source_id}。")
        if not isinstance(name, str) or not name.strip():
            raise MonitoringConfigError(f"发现来源 {source_id} 缺少名称。")
        if not isinstance(enabled, bool):
            raise MonitoringConfigError(f"发现来源 {source_id} 的 enabled 必须是布尔值。")
        if not isinstance(scope, str) or not scope.strip():
            raise MonitoringConfigError(f"发现来源 {source_id} 缺少范围说明。")
        if (
            not isinstance(regions, list)
            or not regions
            or any(region not in {"domestic", "international"} for region in regions)
        ):
            raise MonitoringConfigError(
                f"发现来源 {source_id} 的 regions 只能包含 domestic 或 international。"
            )
        seen_ids.add(source_id)
        discovery_sources.append(
            DiscoverySource(
                id=source_id,
                name=name.strip(),
                enabled=enabled,
                scope=scope.strip(),
                regions=tuple(dict.fromkeys(regions)),
            )
        )

    if not any(source.enabled for source in discovery_sources):
        raise MonitoringConfigError("至少需要启用一个发现来源。")
    return tuple(discovery_sources)


def _load_verification(raw: dict[str, Any]) -> VerificationSettings:
    sources_root = raw.get("sources", {})
    verification_data = sources_root.get("verification", {})
    if not isinstance(verification_data, dict):
        raise MonitoringConfigError("sources.verification 必须是 JSON 对象。")
    settings = VerificationSettings(
        require_original=_boolean(verification_data, "require_original", True),
        allow_official_public_notices=_boolean(
            verification_data, "allow_official_public_notices", True
        ),
        promotional_content_label_required=_boolean(
            verification_data, "promotional_content_label_required", True
        ),
        exclude_unreadable=_boolean(verification_data, "exclude_unreadable", True),
        empty_digest_requires_successful_source=_boolean(
            verification_data, "empty_digest_requires_successful_source", True
        ),
    )
    if not all(
        (
            settings.require_original,
            settings.allow_official_public_notices,
            settings.promotional_content_label_required,
            settings.exclude_unreadable,
            settings.empty_digest_requires_successful_source,
        )
    ):
        raise MonitoringConfigError(
            "sources.verification 中的核验与失败停发规则必须全部启用。"
        )
    return settings


def _load_carryover(
    raw: dict[str, Any],
    *,
    project_root: Path,
) -> CarryoverSettings:
    carryover_data = raw.get("carryover", {})
    if not isinstance(carryover_data, dict):
        raise MonitoringConfigError("carryover 必须是 JSON 对象。")
    enabled = _boolean(carryover_data, "enabled", True)
    raw_path = carryover_data.get("path", "data/pending_articles.json")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise MonitoringConfigError("carryover.path 必须是非空相对路径。")
    relative_path = Path(raw_path)
    if relative_path.is_absolute():
        raise MonitoringConfigError("carryover.path 必须位于项目目录内。")
    resolved_path = (project_root / relative_path).resolve()
    if not resolved_path.is_relative_to(project_root.resolve()):
        raise MonitoringConfigError("carryover.path 不能离开项目目录。")
    settings = CarryoverSettings(
        enabled=enabled,
        path=resolved_path,
        reverify_before_send=_boolean(
            carryover_data, "reverify_before_send", True
        ),
        send_after_next_scheduled_run=_boolean(
            carryover_data, "send_after_next_scheduled_run", True
        ),
    )
    if settings.enabled and not (
        settings.reverify_before_send and settings.send_after_next_scheduled_run
    ):
        raise MonitoringConfigError(
            "启用延期队列时必须在下个发送日重新核验后再发送。"
        )
    return settings


def load_monitoring_config(path: str | Path | None = None) -> MonitoringConfig:
    config_path = (Path(path) if path else default_config_path()).expanduser().resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitoringConfigError(f"无法读取监控配置：{config_path}") from exc
    if not isinstance(raw, dict):
        raise MonitoringConfigError("监控配置必须是 JSON 对象。")

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
        raise MonitoringConfigError("国内与海外的优先条数之和必须等于 max_items。")

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
            raise MonitoringConfigError(f"竞品 {competitor_id} 的地区取值不支持：{region}")
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) and alias for alias in aliases
        ):
            raise MonitoringConfigError(f"竞品 {competitor_id} 至少需要一个非空别名。")
        seen_ids.add(competitor_id)
        priority = _positive_int(entry, "priority")
        if priority > 5:
            raise MonitoringConfigError(
                f"竞品 {competitor_id} 的优先级必须是 1 至 5。"
            )
        name = str(entry.get("name", "")).strip()
        query = str(entry.get("query", "")).strip()
        if name.casefold() not in {alias.casefold() for alias in aliases}:
            raise MonitoringConfigError(
                f"竞品 {competitor_id} 的别名必须包含正式名称。"
            )
        if not any(alias.casefold() in query.casefold() for alias in aliases):
            raise MonitoringConfigError(
                f"竞品 {competitor_id} 的搜索式必须包含至少一个别名。"
            )
        competitors.append(
            Competitor(
                id=competitor_id,
                name=name,
                region=region,
                priority=priority,
                aliases=tuple(aliases),
                query=query,
            )
        )
        if not competitors[-1].name or not competitors[-1].query:
            raise MonitoringConfigError(f"竞品 {competitor_id} 必须填写名称和搜索式。")

    project_root = config_path.parent.parent
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
        discovery_sources=_load_discovery_sources(raw),
        verification=_load_verification(raw),
        carryover=_load_carryover(raw, project_root=project_root),
    )
