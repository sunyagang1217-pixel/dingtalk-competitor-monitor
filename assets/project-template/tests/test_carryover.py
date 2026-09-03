from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from competitor_monitor_bot.carryover import (
    CarryoverError,
    load_due_articles,
    mark_pending_sent,
)
from competitor_monitor_bot.monitoring import Competitor
from competitor_monitor_bot.news import title_fingerprint


class CarryoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "pending_articles.json"

    def _item(self, title: str, **updates):
        item = {
            "review_status": "verified",
            "queue_status": "queued",
            "send_after": "2026-09-03",
            "competitor_id": "example-tech",
            "competitor_name": "示例科技",
            "region": "domestic",
            "priority": 5,
            "title": title,
            "url": "https://example.gov.cn/notices/example",
            "source": "示例法院",
            "source_url": "https://example.gov.cn",
            "published_at": "2026-09-01T00:00:00+08:00",
            "published_at_precision": "date",
            "content_type": "official_notice",
            "category": "法律/合规",
            "fingerprint": title_fingerprint(title),
        }
        item.update(updates)
        return item

    def _write(self, articles) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "queue_rules": "config/analysis_prompt.md",
                    "articles": articles,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def test_loads_only_verified_queued_articles_whose_date_has_arrived(self) -> None:
        due = self._item("示例科技法院公告")
        future = self._item(
            "示例科技未来公告",
            send_after="2026-09-04",
        )
        pending = self._item(
            "示例科技待核验公告",
            review_status="pending",
        )
        sent = self._item(
            "示例科技已发送公告",
            queue_status="sent",
        )
        self._write([due, future, pending, sent])

        articles = load_due_articles(self.path, on_date=date(2026, 9, 3))

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].title, due["title"])
        self.assertEqual(articles[0].content_type, "official_notice")

    def test_rejects_unverified_source_url_for_due_article(self) -> None:
        self._write(
            [
                self._item(
                    "示例科技法院公告",
                    source_url="http://example.gov.cn",
                )
            ]
        )

        with self.assertRaisesRegex(CarryoverError, "来源网址必须使用 HTTPS"):
            load_due_articles(self.path, on_date=date(2026, 9, 3))

    def test_rejects_due_article_outside_current_competitor_scope(self) -> None:
        self._write([self._item("示例科技法院公告")])
        configured = Competitor(
            id="another-competitor",
            name="其他竞品",
            region="domestic",
            priority=5,
            aliases=("其他竞品",),
            query='"其他竞品"',
        )

        with self.assertRaisesRegex(CarryoverError, "当前竞品配置不一致"):
            load_due_articles(
                self.path,
                on_date=date(2026, 9, 3),
                competitors=(configured,),
            )

    def test_marks_matching_queue_item_sent_atomically(self) -> None:
        due = self._item("示例科技法院公告")
        future = self._item("示例科技未来公告", send_after="2026-09-04")
        self._write([due, future])

        changed = mark_pending_sent(
            self.path,
            [due["fingerprint"]],
            sent_at=datetime(2026, 9, 3, 10, 30, tzinfo=timezone.utc),
        )
        saved = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(changed, 1)
        self.assertEqual(saved["articles"][0]["queue_status"], "sent")
        self.assertEqual(
            saved["articles"][0]["sent_at"],
            "2026-09-03T10:30:00+00:00",
        )
        self.assertEqual(saved["articles"][1]["queue_status"], "queued")


if __name__ == "__main__":
    unittest.main()
