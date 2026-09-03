from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import hashlib
import re
import ssl
import unicodedata
from urllib.parse import quote_plus, urljoin, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

import certifi

from .monitoring import Competitor, DiscoverySource, MonitoringConfig


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
    published_at_precision: str = "candidate"
    content_type: str = "unverified"


@dataclass(frozen=True)
class CollectionResult:
    articles: tuple[NewsArticle, ...]
    errors: tuple[str, ...]
    successful_sources: tuple[str, ...] = ()

    @property
    def has_successful_source(self) -> bool:
        return bool(self.successful_sources)


VERIFIED_PUBLISHED_AT_PRECISIONS = {"date", "datetime"}
VERIFIED_CONTENT_TYPES = {
    "independent_report",
    "official_website",
    "official_account",
    "official_notice",
    "brand_content",
    "third_party_view",
}

DISCOVERY_HOSTS = {
    "news.google.com",
    "www.baidu.com",
    "m.baidu.com",
    "www.so.com",
    "weixin.sogou.com",
}


CATEGORY_RULES: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("融资/经营", ("融资", "营收", "利润", "上市", "收购", "funding", "acquisition"), 6),
    ("法律/合规", ("法院", "裁判", "判决", "纠纷", "退款", "诉讼", "court", "lawsuit", "settlement"), 5),
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


def is_discovery_url(value: str) -> bool:
    hostname = (urlsplit(value).hostname or "").casefold()
    return hostname in DISCOVERY_HOSTS


def build_google_news_url(competitor: Competitor, lookback_days: int) -> str:
    if competitor.region == "domestic":
        locale = "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    else:
        locale = "hl=en-US&gl=US&ceid=US:en"
    query = quote_plus(f"{competitor.query} when:{lookback_days}d")
    return f"https://news.google.com/rss/search?q={query}&{locale}"


def build_baidu_search_url(competitor: Competitor, lookback_days: int) -> str:
    del lookback_days
    query = quote_plus(competitor.query)
    return f"https://www.baidu.com/s?wd={query}&rn=50"


def build_360_search_url(competitor: Competitor, lookback_days: int) -> str:
    del lookback_days
    query = quote_plus(competitor.query)
    return f"https://www.so.com/s?q={query}&pn=1"


def build_wechat_search_url(competitor: Competitor, lookback_days: int) -> str:
    del lookback_days
    alias_query = " OR ".join(f'"{alias}"' for alias in competitor.aliases)
    query = quote_plus(alias_query)
    return f"https://weixin.sogou.com/weixin?type=2&query={query}"


def _matches_competitor(title: str, competitor: Competitor) -> bool:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return any(
        unicodedata.normalize("NFKC", alias).casefold() in normalized
        for alias in competitor.aliases
    )


def _as_utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


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

        try:
            published_at = _parse_published(published)
        except NewsCollectionError:
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
                published_at=published_at,
                category=category,
                fingerprint=title_fingerprint(title),
            )
        )
    return articles


@dataclass
class _SearchResult:
    title: str = ""
    url: str = ""
    direct_url: str = ""
    snippet: str = ""
    publisher: str = ""
    date_text: str = ""
    source_text: str = ""
    raw_text: str = ""


class _SearchResultHTMLParser(HTMLParser):
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self, source_id: str):
        super().__init__(convert_charrefs=True)
        self.source_id = source_id
        self.results: list[_SearchResult] = []
        self._depth = 0
        self._current: _SearchResult | None = None
        self._result_tag = ""
        self._result_depth: int | None = None
        self._capture_field = ""
        self._capture_depth: int | None = None
        self._capture_parts: list[str] = []
        self._raw_parts: list[str] = []
        self._ignore_depth = 0

    def _is_result_container(self, tag: str, classes: set[str]) -> bool:
        if self.source_id == "wechat_articles":
            return tag == "div" and "txt-box" in classes
        if self.source_id == "360":
            return tag == "li" and "res-list" in classes
        return tag == "div" and any(
            token == "result" or token.startswith("result-") for token in classes
        )

    def _start_capture(self, field: str) -> None:
        if self._capture_field:
            return
        self._capture_field = field
        self._capture_depth = self._depth
        self._capture_parts = []

    def _finish_capture(self) -> None:
        if not self._current or not self._capture_field:
            return
        value = _clean_text("".join(self._capture_parts))
        if self._capture_field == "title":
            self._current.title = value
        elif self._capture_field == "snippet":
            self._current.snippet = value
        elif self._capture_field == "publisher":
            self._current.publisher = value
        elif self._capture_field == "date":
            self._current.date_text = value
        elif self._capture_field == "source":
            self._current.source_text = value
        self._capture_field = ""
        self._capture_depth = None
        self._capture_parts = []

    def _finish_result(self) -> None:
        if not self._current:
            return
        self._current.raw_text = _clean_text(
            " ".join(
                part
                for part in (
                    " ".join(self._raw_parts),
                    self._current.title,
                    self._current.snippet,
                    self._current.publisher,
                    self._current.date_text,
                    self._current.source_text,
                )
                if part
            )
        )
        self.results.append(self._current)
        self._current = None
        self._result_tag = ""
        self._result_depth = None
        self._raw_parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        classes = set((attributes.get("class") or "").split())
        self._depth += 1

        if self._current is None and self._is_result_container(tag, classes):
            self._current = _SearchResult()
            self._result_tag = tag
            self._result_depth = self._depth

        if self._current is None:
            if tag in self._VOID_TAGS:
                self._depth = max(0, self._depth - 1)
            return

        if tag in {"script", "style"}:
            self._ignore_depth += 1

        if tag == "h3" and not self._current.title:
            self._start_capture("title")
        elif (
            ("res-desc" in classes or "c-abstract" in classes or "txt-info" in classes)
            and not self._current.snippet
        ):
            self._start_capture("snippet")
        elif "all-time-y2" in classes and not self._current.publisher:
            self._start_capture("publisher")
        elif "s2" in classes and not self._current.date_text:
            self._start_capture("date")
        elif (
            "c-showurl" in classes
            or "g-linkinfo" in classes
            or "res-linkinfo" in classes
        ) and not self._current.source_text:
            self._start_capture("source")

        if tag == "a":
            direct_url = attributes.get("data-mdurl", "").strip()
            href = (direct_url or attributes.get("href", "")).strip()
            if direct_url and not self._current.direct_url:
                self._current.direct_url = direct_url
            if href and (self._capture_field == "title" or not self._current.url):
                self._current.url = href

        if tag in self._VOID_TAGS:
            self._depth = max(0, self._depth - 1)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self._current:
            return
        if self._capture_field:
            self._capture_parts.append(data)
        if self._ignore_depth == 0:
            self._raw_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if (
            self._capture_field
            and self._capture_depth == self._depth
        ):
            self._finish_capture()

        if tag in {"script", "style"} and self._ignore_depth:
            self._ignore_depth -= 1

        if (
            self._current
            and self._result_depth == self._depth
            and self._result_tag == tag
        ):
            if self._capture_field:
                self._finish_capture()
            self._finish_result()

        self._depth = max(0, self._depth - 1)

    def close(self) -> None:
        super().close()
        if self._capture_field:
            self._finish_capture()
        if self._current:
            self._finish_result()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _decode_html(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _fallback_h3_results(html_text: str) -> list[_SearchResult]:
    matches = list(re.finditer(r"<h3\b[^>]*>(.*?)</h3>", html_text, flags=re.I | re.S))
    fallback: list[_SearchResult] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(html_text)
        block = html_text[match.start():next_start]
        title_html = match.group(1)
        title = _clean_text(re.sub(r"<[^>]+>", " ", title_html))
        link_match = re.search(
            r"<a\b[^>]+(?:data-mdurl|href)=[\"']([^\"']+)[\"'][^>]*>",
            title_html,
            flags=re.I,
        )
        url = link_match.group(1) if link_match else ""
        visible = _clean_text(re.sub(r"<script\b.*?</script>|<[^>]+>", " ", block, flags=re.I | re.S))
        fallback.append(_SearchResult(title=title, url=url, raw_text=visible))
    return fallback


def _parse_search_results(html_text: str, source_id: str) -> list[_SearchResult]:
    parser = _SearchResultHTMLParser(source_id)
    try:
        parser.feed(html_text)
        parser.close()
    except (ValueError, TypeError) as exc:
        raise NewsCollectionError("搜索来源返回了无法解析的 HTML。") from exc
    return parser.results or _fallback_h3_results(html_text)


def _page_title(html_text: str) -> str:
    match = re.search(r"<title\b[^>]*>(.*?)</title>", html_text, flags=re.I | re.S)
    if not match:
        return ""
    return _clean_text(re.sub(r"<[^>]+>", " ", match.group(1)))


def _looks_like_challenge(html_text: str, source_id: str) -> bool:
    title = _page_title(html_text)
    head = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", html_text[:8000], flags=re.I | re.S)
    visible = _clean_text(head)
    if source_id == "baidu" and "百度安全验证" in title + visible:
        return True
    if "安全验证" in title or "验证码" in title:
        return True
    if source_id == "baidu" and re.search(r"captcha|verify", title + visible, flags=re.I):
        return True
    if source_id in {"360", "wechat_articles"} and re.search(
        r"访问过于频繁|异常请求|验证码|安全验证",
        title + visible,
        flags=re.I,
    ):
        return True
    return False


def _normalise_url(value: str, base_url: str) -> str:
    clean = value.strip()
    if not clean:
        return ""
    absolute = urljoin(base_url, clean)
    parsed = urlsplit(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return absolute


def _origin_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _source_label(value: str) -> str:
    clean = _clean_text(value)
    if not clean:
        return ""
    clean = re.sub(r"(?:反馈|快照)\s*$", "", clean).strip()
    parsed = urlsplit(clean if "://" in clean else f"https://{clean}")
    if parsed.hostname:
        return parsed.hostname.removeprefix("www.")
    return clean


def _parse_search_datetime(value: str, *, now: datetime | None = None) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    current = _as_utc(now)

    timestamp_match = re.search(r"timeConvert\(\s*['\"](\d{9,13})['\"]\s*\)", text)
    if timestamp_match:
        timestamp_value = int(timestamp_match.group(1))
        if timestamp_value > 10_000_000_000:
            timestamp_value /= 1000
        try:
            return datetime.fromtimestamp(timestamp_value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    relative_match = re.search(r"(\d+)\s*(分钟|小时|天|周)前", text)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        if unit == "分钟":
            return current - timedelta(minutes=amount)
        if unit == "小时":
            return current - timedelta(hours=amount)
        if unit == "天":
            return current - timedelta(days=amount)
        return current - timedelta(weeks=amount)
    if "刚刚" in text or "刚才" in text:
        return current
    if "今天" in text:
        return current
    if "昨天" in text:
        return current - timedelta(days=1)

    full_date = re.search(
        r"(?<!\d)(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?",
        text,
    )
    if full_date:
        year, month, day = (int(part) for part in full_date.groups())
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None

    month_day = re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日?", text)
    if month_day:
        month, day = (int(part) for part in month_day.groups())
        try:
            candidate = datetime(current.year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
        if candidate > current + timedelta(days=2):
            candidate = candidate.replace(year=current.year - 1)
        return candidate

    slash_date = re.search(r"(?<!\d)(\d{1,2})[/-](\d{1,2})(?!\d)", text)
    if slash_date:
        month, day = (int(part) for part in slash_date.groups())
        try:
            candidate = datetime(current.year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
        if candidate > current + timedelta(days=2):
            candidate = candidate.replace(year=current.year - 1)
        return candidate

    if re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),", text, flags=re.I):
        try:
            return _parse_published(text)
        except NewsCollectionError:
            return None
    return None


def _articles_from_search_results(
    results: list[_SearchResult],
    competitor: Competitor,
    *,
    source_id: str,
    base_url: str,
    source_name: str,
    now: datetime | None = None,
) -> list[NewsArticle]:
    articles: list[NewsArticle] = []
    for result in results:
        title = _clean_text(result.title)
        if not title or not _matches_competitor(title, competitor):
            continue
        url = _normalise_url(result.direct_url or result.url, base_url)
        if not url:
            continue
        published_at = next(
            (
                parsed
                for value in (result.date_text, result.snippet, result.raw_text)
                if value
                for parsed in (_parse_search_datetime(value, now=now),)
                if parsed is not None
            ),
            None,
        )
        if published_at is None:
            continue
        if source_id == "wechat_articles":
            source = result.publisher or "微信公众号"
            source_url = "https://weixin.sogou.com"
        else:
            source = (
                result.source_text
                or result.publisher
                or _source_label(url)
                or source_name
            )
            source_url = _origin_url(url) or base_url
        category, _ = classify_title(title)
        articles.append(
            NewsArticle(
                competitor_id=competitor.id,
                competitor_name=competitor.name,
                region=competitor.region,
                priority=competitor.priority,
                title=title,
                url=url,
                source=_source_label(source) or source_name,
                source_url=source_url,
                published_at=published_at,
                category=category,
                fingerprint=title_fingerprint(title),
            )
        )
    return articles


def parse_baidu_search(
    html_data: bytes | str,
    competitor: Competitor,
    *,
    now: datetime | None = None,
) -> list[NewsArticle]:
    html_text = _decode_html(html_data)
    if _looks_like_challenge(html_text, "baidu"):
        raise NewsCollectionError("百度搜索返回安全验证页面。")
    results = _parse_search_results(html_text, "baidu")
    return _articles_from_search_results(
        results,
        competitor,
        source_id="baidu",
        base_url="https://www.baidu.com",
        source_name="百度搜索",
        now=now,
    )


def parse_360_search(
    html_data: bytes | str,
    competitor: Competitor,
    *,
    now: datetime | None = None,
) -> list[NewsArticle]:
    html_text = _decode_html(html_data)
    if _looks_like_challenge(html_text, "360"):
        raise NewsCollectionError("360搜索返回安全验证页面。")
    results = _parse_search_results(html_text, "360")
    return _articles_from_search_results(
        results,
        competitor,
        source_id="360",
        base_url="https://www.so.com",
        source_name="360搜索",
        now=now,
    )


def parse_wechat_search(
    html_data: bytes | str,
    competitor: Competitor,
    *,
    now: datetime | None = None,
) -> list[NewsArticle]:
    html_text = _decode_html(html_data)
    results = _parse_search_results(html_text, "wechat_articles")
    if _looks_like_challenge(html_text, "wechat_articles") and not results:
        raise NewsCollectionError("微信公众号搜索返回安全验证页面。")
    return _articles_from_search_results(
        results,
        competitor,
        source_id="wechat_articles",
        base_url="https://weixin.sogou.com",
        source_name="微信公众号搜索",
        now=now,
    )


def _fetch_source_page(url: str, timeout_seconds: int) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "Mozilla/5.0 competitor-news-bot/0.1",
        },
    )
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urlopen(request, timeout=timeout_seconds, context=context) as response:
            return response.read()
    except OSError as exc:
        raise NewsCollectionError(
            f"请求失败：{type(exc).__name__}"
        ) from None


def fetch_competitor_news(
    competitor: Competitor,
    lookback_days: int,
    *,
    timeout_seconds: int = 20,
    now: datetime | None = None,
) -> list[NewsArticle]:
    url = build_google_news_url(competitor, lookback_days)
    xml_data = _fetch_source_page(url, timeout_seconds)
    del now
    return parse_google_news_feed(xml_data, competitor)


def fetch_baidu_news(
    competitor: Competitor,
    lookback_days: int,
    *,
    timeout_seconds: int = 20,
    now: datetime | None = None,
) -> list[NewsArticle]:
    url = build_baidu_search_url(competitor, lookback_days)
    html_data = _fetch_source_page(url, timeout_seconds)
    return parse_baidu_search(html_data, competitor, now=now)


def fetch_360_news(
    competitor: Competitor,
    lookback_days: int,
    *,
    timeout_seconds: int = 20,
    now: datetime | None = None,
) -> list[NewsArticle]:
    url = build_360_search_url(competitor, lookback_days)
    html_data = _fetch_source_page(url, timeout_seconds)
    return parse_360_search(html_data, competitor, now=now)


def fetch_wechat_news(
    competitor: Competitor,
    lookback_days: int,
    *,
    timeout_seconds: int = 20,
    now: datetime | None = None,
) -> list[NewsArticle]:
    url = build_wechat_search_url(competitor, lookback_days)
    html_data = _fetch_source_page(url, timeout_seconds)
    return parse_wechat_search(html_data, competitor, now=now)


fetch_baidu_search = fetch_baidu_news
fetch_360_search = fetch_360_news
fetch_wechat_articles = fetch_wechat_news


def fetch_source_news(
    source_id: str,
    competitor: Competitor,
    lookback_days: int,
    *,
    timeout_seconds: int = 20,
    now: datetime | None = None,
) -> list[NewsArticle]:
    fetchers = {
        "google_news": fetch_competitor_news,
        "baidu": fetch_baidu_news,
        "360": fetch_360_news,
        "wechat_articles": fetch_wechat_news,
    }
    fetcher = fetchers.get(source_id)
    if fetcher is None:
        raise NewsCollectionError(f"不支持的采集来源：{source_id}")
    return fetcher(
        competitor,
        lookback_days,
        timeout_seconds=timeout_seconds,
        now=now,
    )


def _article_rank(article: NewsArticle) -> tuple[int, int, datetime]:
    _, category_weight = classify_title(article.title)
    return article.priority, category_weight, article.published_at


def _deduplication_rank(article: NewsArticle) -> tuple[int, int, int, int, datetime]:
    _, category_weight = classify_title(article.title)
    return (
        article.priority,
        category_weight,
        int(not is_discovery_url(article.url)),
        int(not is_discovery_url(article.source_url)),
        article.published_at,
    )


def _default_sources() -> tuple[DiscoverySource, ...]:
    return (
        DiscoverySource(
            id="google_news",
            name="Google News",
            enabled=True,
            scope="国内+海外",
            regions=("domestic", "international"),
        ),
        DiscoverySource(
            id="baidu",
            name="百度搜索",
            enabled=True,
            scope="国内+海外",
            regions=("domestic", "international"),
        ),
        DiscoverySource(
            id="360",
            name="360搜索",
            enabled=True,
            scope="国内+海外",
            regions=("domestic", "international"),
        ),
        DiscoverySource(
            id="wechat_articles",
            name="微信公众号搜索",
            enabled=True,
            scope="国内+海外，所有提及竞品的公众号文章",
            regions=("domestic", "international"),
        ),
    )


def _is_within_window(
    published_at: datetime,
    now: datetime,
    lookback_days: int,
) -> bool:
    published = _as_utc(published_at)
    lower_bound = now - timedelta(days=lookback_days)
    upper_bound = now + timedelta(hours=1)
    return lower_bound <= published <= upper_bound


def collect_news(
    config: MonitoringConfig,
    *,
    now: datetime | None = None,
    timeout_seconds: int = 20,
) -> CollectionResult:
    current = _as_utc(now)
    deduplicated: dict[str, NewsArticle] = {}
    errors: list[str] = []
    successful_sources: list[str] = []
    configured_sources = getattr(config, "discovery_sources", ()) or _default_sources()
    competitors = sorted(config.competitors, key=lambda item: item.priority, reverse=True)

    for source in configured_sources:
        if not source.enabled:
            continue
        source_succeeded = False
        source_errors: set[str] = set()
        for competitor in competitors:
            if not source.supports(competitor):
                continue
            try:
                source_articles = fetch_source_news(
                    source.id,
                    competitor,
                    config.digest.lookback_days,
                    timeout_seconds=timeout_seconds,
                    now=current,
                )
            except NewsCollectionError as exc:
                source_errors.add(str(exc))
                if "安全验证" in str(exc) or "验证码" in str(exc):
                    break
                continue
            source_succeeded = True
            for article in source_articles:
                if not _is_within_window(
                    article.published_at,
                    current,
                    config.digest.lookback_days,
                ):
                    continue
                existing = deduplicated.get(article.fingerprint)
                if existing is None or _deduplication_rank(
                    article
                ) > _deduplication_rank(existing):
                    deduplicated[article.fingerprint] = article
        if source_succeeded:
            successful_sources.append(source.id)
        for error in sorted(source_errors):
            errors.append(f"{source.name}：{error}")

    articles = tuple(sorted(deduplicated.values(), key=_article_rank, reverse=True))
    return CollectionResult(
        articles=articles,
        errors=tuple(errors),
        successful_sources=tuple(successful_sources),
    )
