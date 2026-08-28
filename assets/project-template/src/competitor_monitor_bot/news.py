from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import re
import ssl
import unicodedata
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import certifi

from .monitoring import Competitor, MonitoringConfig


class NewsCollectionError(RuntimeError):
    """Raised when a news source cannot be read safely."""


@dataclass(frozen=True)
class NewsArticle:
    competitor_id: str
    competitor_name: str
    region: str
    priority: int
    title: str
    url: str
    source: str
    source_url: str
    published_at: datetime
    category: str
    fingerprint: str


@dataclass(frozen=True)
class CollectionResult:
    articles: tuple[NewsArticle, ...]
    errors: tuple[str, ...]


CATEGORY_RULES: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("融资/经营", ("融资", "营收", "利润", "上市", "收购", "funding", "acquisition"), 6),
    ("产品/AI", ("ai", "人工智能", "大模型", "课程", "产品", "发布", "launch"), 5),
    ("政策/市场", ("政策", "监管", "市场", "新规", "policy", "regulation"), 4),
    ("合作/渠道", ("合作", "签约", "入驻", "生态", "partner", "partnership"), 3),
    ("赛事/成果", ("竞赛", "获奖", "金牌", "赛事", "olympiad", "award"), 2),
)


def classify_title(title: str) -> tuple[str, int]:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    for category, keywords, weight in CATEGORY_RULES:
        if any(keyword.casefold() in normalized for keyword in keywords):
            return category, weight
    return "公司动态", 1


def title_fingerprint(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_google_news_url(competitor: Competitor, lookback_days: int) -> str:
    if competitor.region == "domestic":
        locale = "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    else:
        locale = "hl=en-US&gl=US&ceid=US:en"
    query = quote_plus(f"{competitor.query} when:{lookback_days}d")
    return f"https://news.google.com/rss/search?q={query}&{locale}"


def _matches_competitor(title: str, competitor: Competitor) -> bool:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return any(
        unicodedata.normalize("NFKC", alias).casefold() in normalized
        for alias in competitor.aliases
    )


def _parse_published(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise NewsCollectionError("新闻条目的发布时间无效。") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_google_news_feed(xml_data: bytes, competitor: Competitor) -> list[NewsArticle]:
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise NewsCollectionError("新闻来源返回了无效的 RSS XML。") from exc

    articles: list[NewsArticle] = []
    for item in root.findall("./channel/item"):
        raw_title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        source_node = item.find("source")
        source = (source_node.text or "").strip() if source_node is not None else ""
        source_url = (
            (source_node.attrib.get("url") or "").strip()
            if source_node is not None
            else ""
        )
        if not raw_title or not url or not published:
            continue
        if not _matches_competitor(raw_title, competitor):
            continue

        title = raw_title
        source_suffix = f" - {source}" if source else ""
        if source_suffix and title.endswith(source_suffix):
            title = title[: -len(source_suffix)].strip()
        category, _ = classify_title(title)
        articles.append(
            NewsArticle(
                competitor_id=competitor.id,
                competitor_name=competitor.name,
                region=competitor.region,
                priority=competitor.priority,
                title=title,
                url=url,
                source=source or "未知来源",
                source_url=source_url,
                published_at=_parse_published(published),
                category=category,
                fingerprint=title_fingerprint(title),
            )
        )
    return articles


def fetch_competitor_news(
    competitor: Competitor,
    lookback_days: int,
    *,
    timeout_seconds: int = 20,
) -> list[NewsArticle]:
    url = build_google_news_url(competitor, lookback_days)
    request = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 dingtalk-competitor-monitor/1.0"},
    )
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urlopen(request, timeout=timeout_seconds, context=context) as response:
            xml_data = response.read()
    except OSError as exc:
        raise NewsCollectionError(
            f"无法读取 {competitor.name} 的新闻：{type(exc).__name__}"
        ) from None
    return parse_google_news_feed(xml_data, competitor)


def _article_rank(article: NewsArticle) -> tuple[int, int, datetime]:
    _, category_weight = classify_title(article.title)
    return article.priority, category_weight, article.published_at


def collect_news(config: MonitoringConfig) -> CollectionResult:
    deduplicated: dict[str, NewsArticle] = {}
    errors: list[str] = []
    competitors = sorted(config.competitors, key=lambda item: item.priority, reverse=True)
    for competitor in competitors:
        try:
            articles = fetch_competitor_news(
                competitor,
                config.digest.lookback_days,
            )
        except NewsCollectionError as exc:
            errors.append(str(exc))
            continue
        for article in articles:
            existing = deduplicated.get(article.fingerprint)
            if existing is None or _article_rank(article) > _article_rank(existing):
                deduplicated[article.fingerprint] = article

    articles = tuple(
        sorted(deduplicated.values(), key=_article_rank, reverse=True)
    )
    return CollectionResult(articles=articles, errors=tuple(errors))
