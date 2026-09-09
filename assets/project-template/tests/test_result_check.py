from __future__ import annotations

from contextlib import closing, redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timezone
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from competitor_monitor_bot.cli import main
from competitor_monitor_bot.dispatch import DispatchResult
from competitor_monitor_bot.monitoring import load_monitoring_config
from competitor_monitor_bot.result_check import check_delivery_result
from competitor_monitor_bot.state import DigestState


class ResultCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "state.sqlite3"
        loaded = load_monitoring_config()
        self.config = replace(loaded, schedule=replace(loaded.schedule, timezone="Asia/Shanghai", weekdays=(1, 2, 3, 4, 5), result_check_time="10:50"))
        self.now = datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)
        self.sent_at = "2026-09-09T10:35:00+08:00"

    def initialize(self):
        connection = DigestState(self.path)._connect()
        connection.close()

    def record(self, *, count=1, sent_at=None):
        self.initialize()
        stamp = sent_at if sent_at is not None else self.sent_at
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO digest_runs VALUES (?, 'sent', ?, ?, ?)",
                ("2026-09-09", stamp, stamp, count),
            )
            for index in range(count):
                connection.execute(
                    "INSERT INTO sent_articles VALUES (?, ?, ?, ?)",
                    (str(index), "测试文章", "2026-09-09", stamp),
                )

    def check(self, **kwargs):
        return check_delivery_result(self.config, state_path=self.path, now=self.now, **kwargs)

    def run_cli(self, args):
        with (
            patch("competitor_monitor_bot.cli.load_monitoring_config", return_value=self.config),
            patch(
                "competitor_monitor_bot.cli.check_delivery_result",
                side_effect=lambda config, **kwargs: check_delivery_result(config, now=self.now, **kwargs),
            ),
        ):
            return main(args)

    def test_missing_file_is_not_created(self):
        result = self.check()
        self.assertEqual(result.status, "state_unavailable")
        self.assertEqual(result.exit_code, 1)
        self.assertFalse(self.path.exists())

    def test_skipped_run_is_failure(self):
        self.initialize()
        result = self.check()
        self.assertEqual(result.status, "missing")
        self.assertFalse(result.complete)
        self.assertEqual(result.exit_code, 1)

    def test_yesterdays_success_does_not_satisfy_today(self):
        DigestState(self.path).record_confirmed_send(
            "2026-09-08", datetime(2026, 9, 8, 2, 35, tzinfo=timezone.utc), ()
        )
        self.assertEqual(self.check().status, "missing")

    def test_sending_claim_is_not_success_and_is_preserved(self):
        DigestState(self.path).claim_run("2026-09-09", self.now)
        before = self.path.read_bytes()
        self.assertEqual(self.check().status, "in_progress")
        self.assertEqual(self.path.read_bytes(), before)

    def test_successful_delivery_is_read_only(self):
        self.record(count=2)
        before = self.path.read_bytes()
        result = self.check()
        self.assertTrue(result.complete)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.article_count, 2)
        self.assertEqual(result.sent_at, self.sent_at)
        self.assertEqual(self.path.read_bytes(), before)

    def test_successful_empty_digest_is_valid(self):
        self.record(count=0)
        result = self.check()
        self.assertTrue(result.complete)
        self.assertEqual(result.article_count, 0)

    def test_article_count_mismatch_fails(self):
        self.record()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DELETE FROM sent_articles")
        self.assertEqual(self.check().status, "inconsistent")

    def test_article_timestamp_mismatch_fails(self):
        self.record()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE sent_articles SET sent_at = '2026-09-08T10:35:00+08:00'")
        self.assertEqual(self.check().status, "inconsistent")

    def test_invalid_success_timestamp_fails(self):
        self.record()
        for stamp in (None, "invalid", "2026-09-09T10:35:00", "2026-09-08T10:35:00+08:00", "2026-09-09T12:00:00+08:00"):
            with self.subTest(stamp=stamp):
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    connection.execute("UPDATE digest_runs SET sent_at = ?", (stamp,))
                self.assertEqual(self.check().status, "inconsistent")

    def test_orphan_articles_are_not_success(self):
        self.record()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DELETE FROM digest_runs")
        self.assertEqual(self.check().status, "inconsistent")

    def test_corrupt_database_does_not_expose_contents(self):
        self.path.write_bytes(b"sensitive-diagnostic-fixture")
        result = self.check()
        self.assertEqual(result.status, "state_unavailable")
        self.assertNotIn("sensitive-diagnostic-fixture", json.dumps(result.to_dict()))

    def test_uses_configured_timezone_for_today(self):
        self.record(sent_at="2026-09-09T00:10:00+08:00")
        result = check_delivery_result(
            self.config, state_path=self.path,
            now=datetime(2026, 9, 8, 16, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(result.digest_date, "2026-09-09")
        self.assertTrue(result.complete)

    def test_weekend_is_skipped_without_claiming_delivery(self):
        result = self.check(digest_date=date(2026, 9, 12))
        self.assertEqual(result.status, "not_scheduled")
        self.assertFalse(result.complete)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(self.path.exists())

    def test_result_check_phase_begins_at_configured_time(self):
        self.initialize()
        for minute, phase in ((49, "delivery"), (50, "result_check")):
            with self.subTest(minute=minute):
                result = check_delivery_result(
                    self.config, state_path=self.path,
                    now=datetime(2026, 9, 9, 2, minute, tzinfo=timezone.utc),
                )
                self.assertEqual(result.phase, phase)

    def test_rejects_invalid_check_time(self):
        from competitor_monitor_bot.monitoring import MonitoringConfigError, default_config_path

        raw = json.loads(default_config_path().read_text())
        raw["schedule"]["result_check_time"] = "25:99"
        config_path = Path(self.temp_dir.name) / "monitoring.json"
        config_path.write_text(json.dumps(raw))
        with self.assertRaises(MonitoringConfigError):
            load_monitoring_config(config_path)

    def test_explicit_delivery_on_unscheduled_day_is_verified(self):
        self.record(count=0)
        configuration = replace(self.config, schedule=replace(self.config.schedule, weekdays=(1,)))
        result = check_delivery_result(configuration, state_path=self.path, now=self.now)
        self.assertTrue(result.complete)
        self.assertEqual(result.status, "sent")

    def test_cli_checks_without_credentials_or_network(self):
        self.record(count=0)
        with patch("competitor_monitor_bot.cli.load_credentials", side_effect=AssertionError("must not load credentials")), patch("competitor_monitor_bot.cli.DingTalkClient", side_effect=AssertionError("must not send")):
            for target, expected in (("2026-09-09", 0), ("2026-09-10", 1)):
                with self.subTest(target=target), redirect_stdout(StringIO()) as output:
                    code = self.run_cli(["check-result", "--state", str(self.path), "--date", target])
                    self.assertEqual(code, expected)
                    self.assertEqual(json.loads(output.getvalue())["complete"], expected == 0)

    def test_send_cli_returns_failure_for_unfinished_run(self):
        DigestState(self.path).claim_run("2026-09-09", self.now)
        dispatch = DispatchResult("already_running", "2026-09-09", 0, 0)
        with patch("competitor_monitor_bot.cli.load_credentials"), patch("competitor_monitor_bot.cli.DingTalkClient"), patch("competitor_monitor_bot.cli.dispatch_analysis", return_value=dispatch), redirect_stdout(StringIO()) as output:
            code = self.run_cli(["send-analysis", "--input", "unused.json", "--state", str(self.path), "--confirm", "SEND_TO_DINGTALK"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["delivery_check"]["status"], "in_progress")

    def test_claimed_send_success_without_record_is_failure(self):
        self.initialize()
        dispatch = DispatchResult("sent", "2026-09-09", 0, 0, 0, "ok")
        with patch("competitor_monitor_bot.cli.load_credentials"), patch("competitor_monitor_bot.cli.DingTalkClient"), patch("competitor_monitor_bot.cli.dispatch_analysis", return_value=dispatch), redirect_stdout(StringIO()) as output:
            code = self.run_cli(["send-analysis", "--input", "unused.json", "--state", str(self.path), "--confirm", "SEND_TO_DINGTALK"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output.getvalue())["delivery_check"]["complete"])

    def test_already_sent_cli_rechecks_persisted_record(self):
        self.record(count=2)
        dispatch = DispatchResult("already_sent", "2026-09-09", 0, 0)
        with (
            patch("competitor_monitor_bot.cli.load_credentials"),
            patch("competitor_monitor_bot.cli.DingTalkClient"),
            patch("competitor_monitor_bot.cli.dispatch_analysis", return_value=dispatch),
            redirect_stdout(StringIO()) as output,
        ):
            code = self.run_cli(["send-analysis", "--input", "unused.json", "--state", str(self.path), "--confirm", "SEND_TO_DINGTALK"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["delivery_check"]["article_count"], 2)


if __name__ == "__main__":
    unittest.main()
