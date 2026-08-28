from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from competitor_monitor_bot.analysis import build_analysis_template
from competitor_monitor_bot.dingtalk import DingTalkError
from competitor_monitor_bot.dispatch import dispatch_analysis
from competitor_monitor_bot.monitoring import load_monitoring_config
from competitor_monitor_bot.news import NewsArticle, title_fingerprint
from competitor_monitor_bot.state import DigestState


class FakeClient:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, str, bool]] = []

    def send_markdown(self, title, text, *, at_all=False):
        self.calls.append((title, text, at_all))
        if self.fail:
            raise DingTalkError("simulated failure")
        return {"errcode": 0, "errmsg": "ok"}


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "state.sqlite3"
        self.analysis_path = Path(self.temp_dir.name) / "analysis.json"
        self.config = load_monitoring_config()
        title = "示例科技发布AI新产品"
        article = NewsArticle(
            competitor_id="example-tech",
            competitor_name="示例科技",
            region="domestic",
            priority=3,
            title=title,
            url="https://www.news.cn/example",
            source="新华网",
            source_url="https://www.news.cn",
            published_at=datetime(2026, 8, 25, 1, 25, tzinfo=timezone.utc),
            category="产品/AI",
            fingerprint=title_fingerprint(title),
        )
        template = build_analysis_template(
            (article,),
            self.config,
            now=datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc),
        )
        template["articles"][0]["fact_summary"] = (
            "示例科技发布了一项新的人工智能产品以及配套服务框架。"
        )
        template["articles"][0]["impact"] = (
            "值得关注该产品定位是否带动同类企业调整功能与服务策略。"
        )
        self.analysis_path.write_text(
            json.dumps(template, ensure_ascii=False), encoding="utf-8"
        )
        self.fingerprint = article.fingerprint
        self.now = datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc)

    def test_records_articles_only_after_successful_send(self) -> None:
        client = FakeClient()
        result = dispatch_analysis(
            self.analysis_path,
            self.config,
            client,
            state_path=self.state_path,
            now=self.now,
        )
        state = DigestState(self.state_path)

        self.assertEqual(result.status, "sent")
        self.assertEqual(result.article_count, 1)
        self.assertEqual(state.run_status("2026-08-28"), "sent")
        self.assertEqual(state.sent_fingerprints((self.fingerprint,)), {self.fingerprint})
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(client.calls[0][2])

    def test_same_day_second_run_does_not_send_again(self) -> None:
        client = FakeClient()
        dispatch_analysis(
            self.analysis_path,
            self.config,
            client,
            state_path=self.state_path,
            now=self.now,
        )
        result = dispatch_analysis(
            self.analysis_path,
            self.config,
            client,
            state_path=self.state_path,
            now=self.now,
        )

        self.assertEqual(result.status, "already_sent")
        self.assertEqual(len(client.calls), 1)

    def test_failed_send_releases_claim_and_records_nothing(self) -> None:
        client = FakeClient(fail=True)
        with self.assertRaises(DingTalkError):
            dispatch_analysis(
                self.analysis_path,
                self.config,
                client,
                state_path=self.state_path,
                now=self.now,
            )
        state = DigestState(self.state_path)

        self.assertIsNone(state.run_status("2026-08-28"))
        self.assertEqual(state.sent_fingerprints((self.fingerprint,)), set())

    def test_previously_sent_article_is_not_repeated(self) -> None:
        from competitor_monitor_bot.analysis import load_analysis_document

        analyzed = load_analysis_document(
            self.analysis_path,
            digest_format=self.config.digest.format,
            max_items=self.config.digest.max_items,
        )
        state = DigestState(self.state_path)
        state.record_confirmed_send("2026-08-27", self.now, analyzed)
        client = FakeClient()

        result = dispatch_analysis(
            self.analysis_path,
            self.config,
            client,
            state_path=self.state_path,
            now=self.now,
        )

        self.assertEqual(result.article_count, 0)
        self.assertEqual(result.skipped_sent_articles, 1)
        self.assertIn("今日暂无经过核验的重要竞品动态", client.calls[0][1])


if __name__ == "__main__":
    unittest.main()
