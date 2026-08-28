from __future__ import annotations

from datetime import datetime, timezone
import unittest

from competitor_monitor_bot.digest import build_digest_markdown
from competitor_monitor_bot.monitoring import Competitor, load_monitoring_config
from competitor_monitor_bot.news import (
    NewsArticle,
    classify_title,
    parse_google_news_feed,
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

    def test_title_fingerprint_ignores_spacing_and_punctuation(self) -> None:
        self.assertEqual(
            title_fingerprint("示例科技：AI 产品"),
            title_fingerprint("示例科技 AI产品"),
        )

    def test_classifies_funding_before_generic_product(self) -> None:
        category, weight = classify_title("Example Corp announces new funding round")
        self.assertEqual(category, "融资/经营")
        self.assertEqual(weight, 6)

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


if __name__ == "__main__":
    unittest.main()
