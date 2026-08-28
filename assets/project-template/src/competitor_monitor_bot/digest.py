from __future__ import annotations

from datetime import datetime

from .monitoring import MonitoringConfig
from .news import NewsArticle, classify_title


def select_digest_articles(
    articles: tuple[NewsArticle, ...],
    config: MonitoringConfig,
) -> tuple[NewsArticle, ...]:
    domestic = [article for article in articles if article.region == "domestic"]
    international = [
        article for article in articles if article.region == "international"
    ]
    selected = domestic[: config.digest.preferred_domestic_items]
    selected.extend(
        international[: config.digest.preferred_international_items]
    )

    selected_fingerprints = {article.fingerprint for article in selected}
    for article in articles:
        if len(selected) >= config.digest.max_items:
            break
        if article.fingerprint not in selected_fingerprints:
            selected.append(article)
            selected_fingerprints.add(article.fingerprint)

    return tuple(sorted(selected, key=lambda item: item.published_at, reverse=True))


def build_digest_markdown(
    articles: tuple[NewsArticle, ...],
    config: MonitoringConfig,
    *,
    now: datetime | None = None,
) -> str:
    zone = config.schedule.zoneinfo()
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    selected = select_digest_articles(articles, config)
    monitored_domestic = sum(
        competitor.region == "domestic" for competitor in config.competitors
    )
    monitored_international = sum(
        competitor.region == "international" for competitor in config.competitors
    )
    selected_domestic = sum(article.region == "domestic" for article in selected)
    selected_international = sum(
        article.region == "international" for article in selected
    )
    lines = [
        f"## {config.digest_title}｜{local_now:%Y-%m-%d}",
        "",
        (
            f"监测范围：国内 {monitored_domestic} 家 / 海外 "
            f"{monitored_international} 家｜近 {config.digest.lookback_days} 天精选 "
            f"{len(selected)} 条（国内 {selected_domestic} / 海外 "
            f"{selected_international}）"
        ),
    ]

    if not selected:
        lines.extend(["", "今日暂无符合条件的重要竞品动态。"])
        return "\n".join(lines)

    for index, article in enumerate(selected, 1):
        region_label = "国内" if article.region == "domestic" else "海外"
        local_published = article.published_at.astimezone(zone)
        category, _ = classify_title(article.title)
        lines.extend(
            [
                "",
                f"### {index}. [{region_label}·{category}] {article.competitor_name}",
                "",
                f"**{article.title}**",
                "",
                f"来源：{article.source}｜{local_published:%m-%d %H:%M}",
                "",
                f"[查看报道]({article.url})",
            ]
        )

    lines.extend(
        [
            "",
            *(
                ["> 本期海外监测池暂无高相关新增。", ""]
                if monitored_international > 0 and selected_international == 0
                else []
            ),
            "> 自动采集结果仅作线索，重要结论以原始报道和竞品官方信息为准。",
        ]
    )
    return "\n".join(lines)
