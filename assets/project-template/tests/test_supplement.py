from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from competitor_monitor_bot.analysis import load_analysis_document
from competitor_monitor_bot.dispatch import dispatch_analysis
from competitor_monitor_bot.dingtalk import DingTalkError
from competitor_monitor_bot.monitoring import Competitor, load_monitoring_config
from competitor_monitor_bot.news import title_fingerprint
from competitor_monitor_bot.result_check import check_delivery_result
from competitor_monitor_bot.state import DigestState, DigestStateError
from competitor_monitor_bot.supplement import SupplementState, validate_supplement_id


class Client:
    def __init__(self, response=None):
        self.response = response or {"errcode": 0, "errmsg": "ok"}
        self.calls = []

    def send_markdown(self, title, text, *, at_all=False):
        self.calls.append((title, text, at_all))
        return self.response


class SupplementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "state.sqlite3"
        self.input = Path(temporary.name) / "analysis.json"
        self.now = datetime(2026, 9, 9, 4, 0, tzinfo=timezone.utc)
        self.date = "2026-09-09"
        loaded = load_monitoring_config()
        company = Competitor("example", "示例品牌", "domestic", 3, ("示例品牌",), '"示例品牌"')
        self.config = replace(loaded, competitors=(company,), schedule=replace(loaded.schedule, timezone="Asia/Shanghai", weekdays=(1,2,3,4,5)))
        if hasattr(loaded, "carryover"):
            self.config = replace(self.config, carryover=replace(loaded.carryover, enabled=False))
        title = "示例品牌负责人任免动态"
        self.fingerprint = title_fingerprint(title)
        document = {
            "schema_version": 1, "generated_at": self.now.isoformat(),
            "articles": [{
                "competitor_id": company.id, "competitor_name": company.name,
                "region": "domestic", "priority": 3, "title": title,
                "url": "https://example.com/personnel", "source": "示例媒体",
                "source_url": "https://example.com", "published_at": "2026-09-07T12:00:00+00:00",
                "category": "人事/组织", "fingerprint": self.fingerprint,
                "published_at_precision": "datetime", "content_type": "independent_report",
                "fact_summary": "示例媒体报道该品牌近期存在负责人任免动向，企业尚未公开回应。",
                "impact": "该人事动向可能影响品牌后续经营安排，值得持续关注官方回应。"
            }]
        }
        self.input.write_text(json.dumps(document, ensure_ascii=False))
        self.state = DigestState(self.path)

    def primary(self):
        self.state.record_confirmed_send(self.date, self.now, ())
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("SELECT * FROM digest_runs").fetchall()

    def send(self, client, supplement_id="keyword-scope"):
        return dispatch_analysis(self.input, self.config, client, state_path=self.path, now=self.now, supplement_id=supplement_id)

    def test_success_preserves_primary_history_and_checks_each_delivery(self):
        before = self.primary()
        client = Client()
        result = self.send(client)
        self.assertEqual(result.status, "sent")
        self.assertEqual(result.supplement_id, "keyword-scope")
        self.assertEqual(result.article_count, 1)
        self.assertIn("补充日报", client.calls[0][0])
        self.assertFalse(client.calls[0][2])
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM digest_runs").fetchall(), before)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sent_articles").fetchone()[0], 0)
        for identifier in (None, "keyword-scope"):
            checked = check_delivery_result(self.config, state_path=self.path, now=self.now, supplement_id=identifier)
            self.assertTrue(checked.complete)
        self.assertEqual(self.state.sent_fingerprints([self.fingerprint]), {self.fingerprint})

    def test_same_batch_is_idempotent(self):
        self.primary()
        client = Client()
        self.send(client)
        self.assertEqual(self.send(client).status, "already_sent")
        self.assertEqual(len(client.calls), 1)

    def test_new_batch_does_not_repeat_already_sent_articles(self):
        self.primary()
        client = Client()
        self.send(client)
        self.assertEqual(self.send(client, "second-batch").status, "no_new_articles")
        self.assertEqual(len(client.calls), 1)
        self.assertIsNone(SupplementState("second-batch", self.path).run_status(self.date))

    def test_rejected_supplement_does_not_change_primary(self):
        before = self.primary()
        with self.assertRaises(DingTalkError):
            self.send(Client({"errcode": 310000, "errmsg": "test rejection"}))
        self.assertIsNone(SupplementState("keyword-scope", self.path).run_status(self.date))
        self.assertEqual(self.state.sent_fingerprints([self.fingerprint]), set())
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM digest_runs").fetchall(), before)

    def test_supplement_requires_a_successful_primary(self):
        client = Client()
        with self.assertRaises(DigestStateError):
            self.send(client)
        self.assertEqual(client.calls, [])

    def test_active_other_supplement_blocks_concurrent_send(self):
        self.primary()
        self.assertTrue(SupplementState("other", self.path).claim_run(self.date, self.now))
        client = Client()
        self.assertEqual(self.send(client).status, "already_running")
        self.assertEqual(client.calls, [])

    def test_primary_success_does_not_imply_supplement_success(self):
        self.primary()
        checked = check_delivery_result(self.config, state_path=self.path, now=self.now, supplement_id="not-sent")
        self.assertEqual(checked.status, "missing")
        self.assertFalse(checked.complete)

    def test_deduplicates_again_after_acquiring_supplement_claim(self):
        self.primary()
        real_claim = SupplementState.claim_run
        analyzed = load_analysis_document(self.input)
        def claim(state, date, started_at):
            other = SupplementState("finished-between-checks", self.path)
            self.assertTrue(real_claim(other, date, started_at))
            other.complete_run(date, started_at, analyzed)
            return real_claim(state, date, started_at)
        client = Client()
        with patch.object(SupplementState, "claim_run", claim):
            result = self.send(client)
        self.assertEqual(result.status, "no_new_articles")
        self.assertEqual(client.calls, [])

    def test_rejects_invalid_batch_ids(self):
        for identifier in ("", "../other", "UPPER", "a"*65):
            with self.subTest(identifier=identifier), self.assertRaises(DigestStateError):
                validate_supplement_id(identifier)


if __name__ == "__main__":
    unittest.main()
