from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any

from .digest import select_digest_articles
from .monitoring import Competitor, MonitoringConfig
from .news import (
    NewsArticle,
    VERIFIED_CONTENT_TYPES,
    VERIFIED_PUBLISHED_AT_PRECISIONS,
    is_discovery_url,
    title_fingerprint,
)


class AnalysisError(RuntimeError):
    """Raised when an analysis document is incomplete or inconsistent."""


@dataclass(frozen=True)
class AnalyzedArticle:
    article: NewsArticle
    fact_summary: str
    impact: str


CONTENT_TYPE_LABELS = {
    "independent_report": "独立报道",
    "official_website": "官方网站",
    "official_account": "官方账号",
    "official_notice": "官方公告",
    "brand_content": "品牌内容",
    "third_party_view": "第三方观点",
}


def _article_to_dict(article: NewsArticle) -> dict[str, Any]:
    data = asdict(article)
    data["published_at"] = article.published_at.isoformat()
    data["fact_summary"] = ""
    data["impact"] = ""
    return data


def build_analysis_template(
    articles: tuple[NewsArticle, ...],
    config: MonitoringConfig,
    *,
    now: datetime | None = None,
    required_fingerprints: set[str] | None = None,
) -> dict[str, Any]:
    zone = config.schedule.zoneinfo()
    generated_at = now.astimezone(zone) if now else datetime.now(zone)
    required = required_fingerprints or set()
    selected_required = [
        article for article in articles if article.fingerprint in required
    ]
    if len(selected_required) > config.digest.max_items:
        raise AnalysisError("到期延期候选超过日报条数上限，必须先人工处理。")
    selected_fingerprints = {article.fingerprint for article in selected_required}
    remaining = tuple(
        article for article in articles if article.fingerprint not in selected_fingerprints
    )
    remaining_slots = config.digest.max_items - len(selected_required)
    selected = selected_required + list(
        select_digest_articles(remaining, config)[:remaining_slots]
    )
    selected = sorted(
        selected,
        key=lambda item: item.published_at,
        reverse=True,
    )
    return {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "analysis_rules": "config/analysis_prompt.md",
        "articles": [_article_to_dict(article) for article in selected],
    }


def _validated_text(
    value: Any,
    field: str,
    index: int,
    *,
    allow_empty: bool = False,
) -> str:
    if allow_empty and (value is None or value == ""):
        return ""
    if not isinstance(value, str):
        raise AnalysisError(f"第 {index} 条的 {field} 必须是文本。")
    clean = " ".join(value.split())
    if len(clean) < 12 or len(clean) > 180:
        raise AnalysisError(
            f"第 {index} 条的 {field} 必须包含 12 至 180 个字符。"
        )
    if "http://" in clean or "https://" in clean:
        raise AnalysisError(f"第 {index} 条的 {field} 不能包含网址。")
    return clean


def _parse_article(data: dict[str, Any], index: int) -> NewsArticle:
    required_strings = (
        "competitor_id",
        "competitor_name",
        "region",
        "title",
        "url",
        "source",
        "source_url",
        "published_at",
        "category",
        "fingerprint",
        "published_at_precision",
        "content_type",
    )
    for field in required_strings:
        if not isinstance(data.get(field), str):
            raise AnalysisError(f"第 {index} 条缺少字段 {field}。")
    if data["region"] not in {"domestic", "international"}:
        raise AnalysisError(f"第 {index} 条包含不支持的地区。")
    if not re.fullmatch(r"[0-9a-f]{64}", data["fingerprint"]):
        raise AnalysisError(f"第 {index} 条的指纹无效。")
    if title_fingerprint(data["title"]) != data["fingerprint"]:
        raise AnalysisError(f"第 {index} 条的标题与指纹不一致。")
    if not data["url"].startswith("https://"):
        raise AnalysisError(f"第 {index} 条的网址必须使用 HTTPS。")
    if not data["source_url"].startswith("https://"):
        raise AnalysisError(f"第 {index} 条的来源网址必须使用 HTTPS。")
    if is_discovery_url(data["url"]):
        raise AnalysisError(f"第 {index} 条必须把搜索链接校正为原始报道直链。")
    if is_discovery_url(data["source_url"]):
        raise AnalysisError(f"第 {index} 条必须校正真实来源网址。")
    if data["published_at_precision"] not in VERIFIED_PUBLISHED_AT_PRECISIONS:
        raise AnalysisError(
            f"第 {index} 条必须打开原文并把 published_at_precision "
            "校正为 date 或 datetime。"
        )
    if data["content_type"] not in VERIFIED_CONTENT_TYPES:
        raise AnalysisError(
            f"第 {index} 条必须打开原文并填写受支持的 content_type。"
        )
    try:
        published_at = datetime.fromisoformat(data["published_at"])
    except ValueError as exc:
        raise AnalysisError(f"第 {index} 条的发布时间无效。") from exc
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    priority = data.get("priority")
    if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
        raise AnalysisError(f"第 {index} 条的优先级必须是正整数。")
    return NewsArticle(
        competitor_id=data["competitor_id"],
        competitor_name=data["competitor_name"],
        region=data["region"],
        priority=priority,
        title=data["title"],
        url=data["url"],
        source=data["source"],
        source_url=data["source_url"],
        published_at=published_at,
        category=data["category"],
        fingerprint=data["fingerprint"],
        published_at_precision=data["published_at_precision"],
        content_type=data["content_type"],
    )


def load_analysis_document(
    path: str | Path,
    *,
    digest_format: str = "analysis",
    max_items: int = 8,
    lookback_days: int | None = None,
    required_fingerprints: set[str] | None = None,
    competitors: tuple[Competitor, ...] | None = None,
) -> tuple[AnalyzedArticle, ...]:
    document_path = Path(path)
    try:
        document = json.loads(document_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"无法读取分析文档：{document_path}") from exc
    if digest_format not in {"analysis", "brief"}:
        raise AnalysisError("日报格式必须是 analysis 或 brief。")
    if not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 1:
        raise AnalysisError("日报条数上限必须是正整数。")
    if lookback_days is not None and (
        not isinstance(lookback_days, int)
        or isinstance(lookback_days, bool)
        or lookback_days < 1
    ):
        raise AnalysisError("采集回看天数必须是正整数。")
    if document.get("schema_version") != 1:
        raise AnalysisError("不支持当前分析文档版本。")
    raw_articles = document.get("articles")
    if not isinstance(raw_articles, list):
        raise AnalysisError("分析文档中的 articles 必须是列表。")
    if len(raw_articles) > max_items:
        raise AnalysisError(f"分析文档最多只能保留 {max_items} 条。")
    if not raw_articles:
        collection = document.get("collection")
        successful_sources = (
            collection.get("successful_sources")
            if isinstance(collection, dict)
            else None
        )
        if (
            not isinstance(successful_sources, list)
            or not successful_sources
            or any(
                not isinstance(source, str) or not source.strip()
                for source in successful_sources
            )
        ):
            raise AnalysisError(
                "空日报必须证明至少一个配置来源采集成功；全部来源失败时不得发送。"
            )
    try:
        generated_at = datetime.fromisoformat(str(document.get("generated_at", "")))
    except ValueError as exc:
        raise AnalysisError("分析文档的 generated_at 无效。") from exc
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)

    analyzed: list[AnalyzedArticle] = []
    fingerprints: set[str] = set()
    competitor_scope = (
        {competitor.id: competitor for competitor in competitors}
        if competitors is not None
        else None
    )
    for index, raw_article in enumerate(raw_articles, 1):
        if not isinstance(raw_article, dict):
            raise AnalysisError(f"第 {index} 条必须是 JSON 对象。")
        article = _parse_article(raw_article, index)
        if competitor_scope is not None:
            configured = competitor_scope.get(article.competitor_id)
            if configured is None or (
                article.competitor_name != configured.name
                or article.region != configured.region
                or article.priority != configured.priority
            ):
                raise AnalysisError(
                    f"第 {index} 条与当前竞品配置不一致。"
                )
        if lookback_days is not None:
            lower_bound = generated_at - timedelta(days=lookback_days)
            upper_bound = generated_at + timedelta(hours=1)
            if not lower_bound <= article.published_at <= upper_bound:
                raise AnalysisError(f"第 {index} 条的原文发布时间超出监控窗口。")
        if article.fingerprint in fingerprints:
            raise AnalysisError(f"第 {index} 条与前面的内容重复。")
        fingerprints.add(article.fingerprint)
        fact_summary = _validated_text(
            raw_article.get("fact_summary"), "fact_summary", index
        )
        if article.content_type == "brand_content" and not any(
            marker in fact_summary
            for marker in ("宣称", "发布", "推广", "品牌内容", "付费")
        ):
            raise AnalysisError(
                f"第 {index} 条是品牌内容，fact_summary 必须明确标注企业口径。"
            )
        analyzed.append(
            AnalyzedArticle(
                article=article,
                fact_summary=fact_summary,
                impact=_validated_text(
                    raw_article.get("impact"),
                    "impact",
                    index,
                    allow_empty=digest_format == "brief",
                ),
            )
        )
    missing_required = (required_fingerprints or set()) - fingerprints
    if missing_required:
        raise AnalysisError(
            f"分析文档缺少 {len(missing_required)} 条到期延期候选；"
            "无法重新核验时必须停止发送并报告原因。"
        )
    return tuple(analyzed)


def build_analysis_markdown(
    analyzed: tuple[AnalyzedArticle, ...],
    config: MonitoringConfig,
    *,
    now: datetime | None = None,
) -> str:
    zone = config.schedule.zoneinfo()
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    domestic_count = sum(item.article.region == "domestic" for item in analyzed)
    international_count = sum(
        item.article.region == "international" for item in analyzed
    )
    lines = [
        f"## {config.digest_title}｜{local_now:%Y-%m-%d}",
        "",
        (
            f"本期精选 {len(analyzed)} 条｜国内 {domestic_count} 条 / "
            f"海外 {international_count} 条"
        ),
    ]
    if not analyzed:
        lines.extend(["", "今日暂无经过核验的重要竞品动态。"])
        return "\n".join(lines)

    for index, item in enumerate(analyzed, 1):
        article = item.article
        region_label = "国内" if article.region == "domestic" else "海外"
        published = article.published_at.astimezone(zone)
        published_label = (
            f"{published:%m-%d}"
            if article.published_at_precision == "date"
            else f"{published:%m-%d %H:%M}"
        )
        content_type_label = CONTENT_TYPE_LABELS[article.content_type]
        detail_lines = [f"- **摘要：** {item.fact_summary}"]
        if config.digest.format == "analysis":
            detail_lines = [
                f"- **发生了什么：** {item.fact_summary}",
                f"- **值得关注：** {item.impact}",
            ]
        lines.extend(
            [
                "",
                f"### {index}. [{region_label}·{article.category}] {article.competitor_name}",
                "",
                f"**{article.title}**",
                "",
                *detail_lines,
                f"- **来源：** [{article.source}]({article.url})｜{published_label}｜{content_type_label}",
            ]
        )
    lines.extend(
        [
            "",
            (
                "> 事实摘要来自原始报道；“值得关注”为竞品分析判断，不代表已发生事实。"
                if config.digest.format == "analysis"
                else "> 摘要来自原始报道，重要结论请以竞品官方信息为准。"
            ),
        ]
    )
    return "\n".join(lines)
