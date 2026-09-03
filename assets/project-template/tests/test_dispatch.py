from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from competitor_monitor_bot.analysis import build_analysis_template
from competitor_monitor_bot.dingtalk import DingTalkError
from competitor_monitor_bot.dispatch import dispatch_analysis
from competitor_monitor_bot.monitoring import Competitor, load_monitoring_config
from competitor_monitor_bot.news import NewsArticle, title_fingerprint
from competitor_monitor_bot.state import DigestState


class FakeClient:
    def __init__(self, *, fail: bool = False, errcode: int = 0):
        self.fail = fail
        self.errcode = errcode
        self.calls: list[tuple[str, str, bool]] = []

    def send_markdown(self, title, text, *, at_all=False):
        self.calls.append((title, text, at_all))
        if self.fail:
            raise DingTalkError("simulated failure")
        return {
            "errcode": self.errcode,
            "errmsg": "ok" if self.errcode == 0 else "simulated rejection",
        }


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "state.sqlite3"
        self.analysis_path = Path(self.temp_dir.name) / "analysis.json"
        self.config = load_monitoring_config()
        title = "示例科技发布AI新产品"
        competitor = Competitor(
            id="example-tech",
            name="示例科技",
            region="domestic",
            priority=3,
            aliases=("示例科技",),
            query='"示例科技"',
        )
        self.config = replace(self.config, competitors=(competitor,))
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
            published_at_precision="datetime",
            content_type="independent_report",
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

    def test_nonzero_response_releases_claim_and_records_nothing(self) -> None:
        client = FakeClient(errcode=310000)
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

    def test_successful_send_marks_matching_due_carryover_sent(self) -> None:
        title = "示例科技发布AI新产品"
        queue_path = Path(self.temp_dir.name) / "pending_articles.json"
        queue_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "queue_rules": "config/analysis_prompt.md",
                    "articles": [
                        {
                            "review_status": "verified",
                            "queue_status": "queued",
                            "send_after": "2026-08-28",
                            "competitor_id": "example-tech",
                            "competitor_name": "示例科技",
                            "region": "domestic",
                            "priority": 3,
                            "title": title,
                            "url": "https://www.news.cn/example",
                            "source": "新华网",
                            "source_url": "https://www.news.cn",
                            "published_at": "2026-08-25T01:25:00+00:00",
                            "published_at_precision": "datetime",
                            "content_type": "independent_report",
                            "category": "产品/AI",
                            "fingerprint": self.fingerprint,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config = replace(
            self.config,
            carryover=replace(self.config.carryover, path=queue_path),
        )

        result = dispatch_analysis(
            self.analysis_path,
            config,
            FakeClient(),
            state_path=self.state_path,
            now=self.now,
        )
        saved = json.loads(queue_path.read_text(encoding="utf-8"))

        self.assertEqual(result.carryover_completed, 1)
        self.assertEqual(saved["articles"][0]["queue_status"], "sent")

    def test_failed_send_leaves_due_carryover_queued(self) -> None:
        title = "示例科技发布AI新产品"
        queue_path = Path(self.temp_dir.name) / "pending_articles.json"
        queue = {
            "schema_version": 1,
            "queue_rules": "config/analysis_prompt.md",
            "articles": [
                {
                    "review_status": "verified",
                    "queue_status": "queued",
                    "send_after": "2026-08-28",
                    "competitor_id": "example-tech",
                    "competitor_name": "示例科技",
                    "region": "domestic",
                    "priority": 3,
                    "title": title,
                    "url": "https://www.news.cn/example",
                    "source": "新华网",
                    "source_url": "https://www.news.cn",
                    "published_at": "2026-08-25T01:25:00+00:00",
                    "published_at_precision": "datetime",
                    "content_type": "independent_report",
                    "category": "产品/AI",
                    "fingerprint": self.fingerprint,
                }
            ],
        }
        queue_path.write_text(
            json.dumps(queue, ensure_ascii=False),
            encoding="utf-8",
        )
        config = replace(
            self.config,
            carryover=replace(self.config.carryover, path=queue_path),
        )

        with self.assertRaises(DingTalkError):
            dispatch_analysis(
                self.analysis_path,
                config,
                FakeClient(fail=True),
                state_path=self.state_path,
                now=self.now,
            )
        saved = json.loads(queue_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["articles"][0]["queue_status"], "queued")

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
