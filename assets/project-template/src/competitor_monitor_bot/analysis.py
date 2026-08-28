from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .digest import select_digest_articles
from .monitoring import MonitoringConfig
from .news import NewsArticle, title_fingerprint


class AnalysisError(RuntimeError):
    """Raised when an analysis document is incomplete or inconsistent."""


@dataclass(frozen=True)
class AnalyzedArticle:
    article: NewsArticle
    fact_summary: str
    impact: str


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
) -> dict[str, Any]:
    zone = config.schedule.zoneinfo()
    generated_at = now.astimezone(zone) if now else datetime.now(zone)
    selected = select_digest_articles(articles, config)
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
    )


def load_analysis_document(
    path: str | Path,
    *,
    digest_format: str = "analysis",
    max_items: int = 8,
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
    if document.get("schema_version") != 1:
        raise AnalysisError("不支持当前分析文档版本。")
    raw_articles = document.get("articles")
    if not isinstance(raw_articles, list):
        raise AnalysisError("分析文档中的 articles 必须是列表。")
    if len(raw_articles) > max_items:
        raise AnalysisError(f"分析文档最多只能保留 {max_items} 条。")

    analyzed: list[AnalyzedArticle] = []
    fingerprints: set[str] = set()
    for index, raw_article in enumerate(raw_articles, 1):
        if not isinstance(raw_article, dict):
            raise AnalysisError(f"第 {index} 条必须是 JSON 对象。")
        article = _parse_article(raw_article, index)
        if article.fingerprint in fingerprints:
            raise AnalysisError(f"第 {index} 条与前面的内容重复。")
        fingerprints.add(article.fingerprint)
        analyzed.append(
            AnalyzedArticle(
                article=article,
                fact_summary=_validated_text(
                    raw_article.get("fact_summary"), "fact_summary", index
                ),
                impact=_validated_text(
                    raw_article.get("impact"),
                    "impact",
                    index,
                    allow_empty=digest_format == "brief",
                ),
            )
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
                f"- **来源：** [{article.source}]({article.url})｜{published:%m-%d %H:%M}",
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
