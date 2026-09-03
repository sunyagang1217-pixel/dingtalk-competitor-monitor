from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from competitor_monitor_bot.cli import _require_collection_success
from competitor_monitor_bot.digest import build_digest_markdown
from competitor_monitor_bot.monitoring import Competitor, load_monitoring_config
from competitor_monitor_bot.news import (
    NewsCollectionError,
    NewsArticle,
    build_wechat_search_url,
    classify_title,
    collect_news,
    parse_360_search,
    parse_baidu_search,
    parse_google_news_feed,
    parse_wechat_search,
    title_fingerprint,
)


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>示例科技发布AI新产品 - 新华网</title>
    <link>https://news.google.com/articles/example</link>
    <pubDate>Tue, 25 Aug 2026 01:25:36 GMT</pubDate>
    <source url="https://www.news.cn">新华网</source>
  </item>
  <item>
    <title>无关公司新闻 - 示例网</title>
    <link>https://news.google.com/articles/irrelevant</link>
    <pubDate>Tue, 25 Aug 2026 01:25:36 GMT</pubDate>
    <source url="https://example.com">示例网</source>
  </item>
</channel></rss>""".encode("utf-8")

SAMPLE_BAIDU = """
<html><head><title>百度搜索</title></head><body>
<div class="result c-container">
  <h3 class="t"><a href="https://example.com/product">示例科技发布AI课程</a></h3>
  <div class="c-abstract">2026年9月1日，示例科技推出新的人工智能课程。</div>
  <span class="c-showurl">example.com</span>
</div>
<div class="result c-container">
  <h3 class="t"><a href="https://example.com/other">无关公司新闻</a></h3>
  <div class="c-abstract">2026年9月1日，其他公司发布消息。</div>
</div>
</body></html>
"""

SAMPLE_360 = """
<html><head><title>360搜索</title></head><body>
<ul>
  <li class="res-list">
    <h3 class="res-title"><a href="https://www.so.com/link?url=redirect"
      data-mdurl="https://example.com/partner">示例科技合作发布新课程</a></h3>
    <p class="res-desc">2026-09-01 示例科技公布新的课程合作计划。</p>
  </li>
</ul>
</body></html>
"""

SAMPLE_WECHAT = """
<html><head><title>示例科技的相关微信公众号文章</title></head><body>
<div class="txt-box">
  <h3><a href="/link?url=wechat-article">示例科技发布AI学习工具</a></h3>
  <p class="txt-info">示例科技介绍面向用户的人工智能学习工具。</p>
  <div class="s-p"><span class="all-time-y2">行业观察</span>
    <span class="s2"><script>document.write(timeConvert('1788220800'))</script></span>
  </div>
</div>
</body></html>
"""


class NewsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.competitor = Competitor(
            id="example-tech",
            name="示例科技",
            region="domestic",
            priority=3,
            aliases=("示例科技",),
            query='"示例科技"',
        )

    def test_parses_feed_and_filters_irrelevant_titles(self) -> None:
        articles = parse_google_news_feed(SAMPLE_RSS, self.competitor)
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].source, "新华网")
        self.assertEqual(articles[0].category, "产品/AI")
        self.assertNotIn(" - 新华网", articles[0].title)
        self.assertEqual(articles[0].published_at_precision, "candidate")
        self.assertEqual(articles[0].content_type, "unverified")

    def test_title_fingerprint_ignores_spacing_and_punctuation(self) -> None:
        self.assertEqual(
            title_fingerprint("示例科技：AI 产品"),
            title_fingerprint("示例科技 AI产品"),
        )

    def test_classifies_funding_before_generic_product(self) -> None:
        category, weight = classify_title("Example Corp announces new funding round")
        self.assertEqual(category, "融资/经营")
        self.assertEqual(weight, 6)

    def test_classifies_court_notice_as_legal_compliance(self) -> None:
        category, weight = classify_title("示例科技教育培训合同纠纷法院公告")
        self.assertEqual(category, "法律/合规")
        self.assertEqual(weight, 5)

    def test_builds_markdown_digest_without_sending(self) -> None:
        config = load_monitoring_config()
        title = "示例科技发布AI新产品"
        article = NewsArticle(
            competitor_id=self.competitor.id,
            competitor_name=self.competitor.name,
            region=self.competitor.region,
            priority=self.competitor.priority,
            title=title,
            url="https://news.google.com/articles/example",
            source="新华网",
            source_url="https://www.news.cn",
            published_at=datetime(2026, 8, 25, 1, 25, tzinfo=timezone.utc),
            category="产品/AI",
            fingerprint=title_fingerprint(title),
        )
        markdown = build_digest_markdown(
            (article,),
            config,
            now=datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc),
        )
        self.assertIn(f"{config.digest_title}｜2026-08-28", markdown)
        self.assertIn("[国内·产品/AI] 示例科技", markdown)
        self.assertIn("[查看报道]", markdown)

    def test_parses_baidu_search_results(self) -> None:
        articles = parse_baidu_search(
            SAMPLE_BAIDU,
            self.competitor,
            now=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, "示例科技发布AI课程")
        self.assertEqual(articles[0].source, "example.com")
        self.assertEqual(articles[0].published_at.date().isoformat(), "2026-09-01")

    def test_parses_360_search_direct_url(self) -> None:
        articles = parse_360_search(
            SAMPLE_360,
            self.competitor,
            now=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].url, "https://example.com/partner")
        self.assertEqual(articles[0].source, "example.com")

    def test_parses_wechat_article_and_publisher(self) -> None:
        articles = parse_wechat_search(
            SAMPLE_WECHAT,
            self.competitor,
            now=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].source, "行业观察")
        self.assertEqual(articles[0].source_url, "https://weixin.sogou.com")
        self.assertEqual(articles[0].published_at.date().isoformat(), "2026-09-01")

    def test_rejects_baidu_security_verification_page(self) -> None:
        with self.assertRaises(NewsCollectionError):
            parse_baidu_search(
                "<html><head><title>百度安全验证</title></head></html>",
                self.competitor,
            )

    def test_wechat_query_uses_all_aliases_without_industry_terms(self) -> None:
        competitor = Competitor(
            id="example-tech",
            name="示例科技",
            region="domestic",
            priority=3,
            aliases=("示例科技", "Example Tech"),
            query='"示例科技" OR "Example Tech" 行业培训',
        )
        url = build_wechat_search_url(competitor, 7)
        self.assertIn("%E7%A4%BA%E4%BE%8B%E7%A7%91%E6%8A%80", url)
        self.assertIn("Example+Tech", url)
        self.assertNotIn("%E8%A1%8C%E4%B8%9A%E5%9F%B9%E8%AE%AD", url)

    def test_collects_all_enabled_sources_without_source_priority(self) -> None:
        config = load_monitoring_config()
        calls: list[str] = []

        def fake_fetch(source_id, competitor, lookback_days, **kwargs):
            del competitor, lookback_days, kwargs
            calls.append(source_id)
            return []

        with patch(
            "competitor_monitor_bot.news.fetch_source_news",
            side_effect=fake_fetch,
        ):
            result = collect_news(
                config,
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

        expected_calls = [
            source.id
            for source in config.discovery_sources
            if source.enabled
            for competitor in sorted(
                config.competitors,
                key=lambda item: item.priority,
                reverse=True,
            )
            if source.supports(competitor)
        ]
        self.assertEqual(calls, expected_calls)
        self.assertEqual(
            result.successful_sources,
            ("google_news", "baidu", "360", "wechat_articles"),
        )
        self.assertEqual(result.errors, ())

    def test_one_failed_source_does_not_block_other_sources(self) -> None:
        config = load_monitoring_config()

        def fake_fetch(source_id, competitor, lookback_days, **kwargs):
            del competitor, lookback_days, kwargs
            if source_id == "baidu":
                raise NewsCollectionError("百度搜索返回安全验证页面。")
            return []

        with patch(
            "competitor_monitor_bot.news.fetch_source_news",
            side_effect=fake_fetch,
        ):
            result = collect_news(
                config,
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

        self.assertEqual(
            result.successful_sources,
            ("google_news", "360", "wechat_articles"),
        )
        self.assertEqual(len(result.errors), 1)

    def test_duplicate_title_prefers_direct_article_without_source_priority(self) -> None:
        config = load_monitoring_config()

        def candidate(competitor, *, url, source_url):
            title = f"{competitor.name}发布AI课程"
            return NewsArticle(
                competitor_id=competitor.id,
                competitor_name=competitor.name,
                region=competitor.region,
                priority=competitor.priority,
                title=title,
                url=url,
                source="示例来源",
                source_url=source_url,
                published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                category="产品/AI",
                fingerprint=title_fingerprint(title),
            )

        def fake_fetch(source_id, competitor, lookback_days, **kwargs):
            del lookback_days, kwargs
            if source_id == "google_news":
                return [
                    candidate(
                        competitor,
                        url="https://news.google.com/articles/example",
                        source_url="https://example.com",
                    )
                ]
            if source_id == "baidu":
                return [
                    candidate(
                        competitor,
                        url="https://example.com/original",
                        source_url="https://example.com",
                    )
                ]
            return []

        with patch(
            "competitor_monitor_bot.news.fetch_source_news",
            side_effect=fake_fetch,
        ):
            result = collect_news(
                config,
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

        self.assertEqual(len(result.articles), len(config.competitors))
        self.assertTrue(
            all(
                article.url == "https://example.com/original"
                for article in result.articles
            )
        )

    def test_all_failed_sources_stop_digest_generation(self) -> None:
        config = load_monitoring_config()
        with patch(
            "competitor_monitor_bot.news.fetch_source_news",
            side_effect=NewsCollectionError("请求失败：TimeoutError"),
        ):
            result = collect_news(
                config,
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
            )

        self.assertFalse(result.has_successful_source)
        self.assertEqual(len(result.errors), 4)
        with self.assertRaisesRegex(NewsCollectionError, "所有配置采集来源均失败"):
            _require_collection_success(result)


if __name__ == "__main__":
    unittest.main()
