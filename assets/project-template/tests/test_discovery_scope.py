from dataclasses import replace
from datetime import datetime, timezone
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from competitor_monitor_bot.analysis import build_analysis_template
from competitor_monitor_bot.monitoring import Competitor, load_monitoring_config
from competitor_monitor_bot.news import (
    build_google_news_url, build_bing_search_url, build_baidu_search_url, build_360_search_url,
    build_wechat_search_url, collect_news, parse_google_news_feed, title_fingerprint,
)


class KeywordDiscoveryTests(unittest.TestCase):
    def test_all_sources_use_brand_names_without_old_industry_suffixes(self):
        for name, aliases, old_query in (
            ("猿编程", ("猿编程",), '"猿编程" 少儿编程'),
            ("小码王", ("小码王",), '"小码王" 少儿编程'),
            ("Tynker", ("Tynker",), '"Tynker" coding kids'),
            ("编程猫", ("编程猫", "CodeMao"), '"编程猫" OR "CodeMao" education'),
        ):
            competitor = Competitor("example", name, "domestic", 3, aliases, old_query)
            expected = " OR ".join(f'"{alias}"' for alias in aliases)
            for build, field in (
                (build_google_news_url, "q"), (build_baidu_search_url, "wd"),
                (build_bing_search_url, "q"), (build_360_search_url, "q"),
                (build_wechat_search_url, "query"),
            ):
                with self.subTest(name=name, source=build.__name__):
                    query = parse_qs(urlsplit(build(competitor, 7)).query)[field][0]
                    self.assertEqual(query.removesuffix(" when:7d"), expected)

    def test_personnel_story_is_discovered_without_industry_words(self):
        company = Competitor("yuan", "猿编程", "domestic", 3, ("猿编程",), '"猿编程" 少儿编程')
        rss = """<rss><channel><item>
        <title>传猿编程创始人被免去负责人职务 - 示例媒体</title>
        <link>https://example.com/personnel</link>
        <pubDate>Mon, 07 Sep 2026 12:00:00 GMT</pubDate>
        <source url="https://example.com">示例媒体</source>
        </item></channel></rss>""".encode()
        loaded = load_monitoring_config()
        google = next(s for s in loaded.discovery_sources if s.id == "google_news")
        config = replace(loaded, competitors=(company,), discovery_sources=(google,))

        def fetch(url, timeout_seconds):
            query = parse_qs(urlsplit(url).query)["q"][0]
            return b"<rss><channel/></rss>" if "少儿编程" in query else rss

        with patch("competitor_monitor_bot.news._fetch_source_page", side_effect=fetch):
            collection = collect_news(config, now=datetime(2026, 9, 9, tzinfo=timezone.utc))
        self.assertEqual(len(collection.articles), 1)
        self.assertEqual(collection.articles[0].category, "人事/组织")

        article = parse_google_news_feed(rss, company)[0]
        candidates = tuple(
            replace(article, title=f"猿编程动态{i}", fingerprint=title_fingerprint(f"猿编程动态{i}"))
            for i in range(config.digest.max_items + 2)
        )
        template = build_analysis_template(candidates, config)
        self.assertEqual(len(template["articles"]), len(candidates))


if __name__ == "__main__":
    unittest.main()
