from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from competitor_monitor_bot.monitoring import (
    MonitoringConfigError,
    default_config_path,
    load_monitoring_config,
)


class MonitoringConfigTests(unittest.TestCase):
    def test_loads_requested_scope_and_schedule(self) -> None:
        config = load_monitoring_config()
        self.assertTrue(config.industry_name)
        self.assertTrue(config.digest_title)
        self.assertIn(config.digest.format, {"analysis", "brief"})
        self.assertTrue(config.schedule.timezone)
        self.assertTrue(config.schedule.cron)
        self.assertTrue(config.schedule.weekdays)
        self.assertGreaterEqual(len(config.competitors), 1)
        self.assertEqual(
            config.digest.preferred_domestic_items
            + config.digest.preferred_international_items,
            config.digest.max_items,
        )
        first = config.competitors[0]
        self.assertIn(first.name, first.aliases)
        self.assertTrue(first.query)
        self.assertEqual(
            tuple(source.id for source in config.discovery_sources if source.enabled),
            ("google_news", "baidu", "360", "wechat_articles"),
        )
        self.assertTrue(
            all(
                source.regions == ("domestic", "international")
                for source in config.discovery_sources
            )
        )
        self.assertTrue(config.verification.require_original)
        self.assertTrue(config.verification.allow_official_public_notices)
        self.assertTrue(config.carryover.reverify_before_send)
        self.assertEqual(config.carryover.path.name, "pending_articles.json")

    def _write_config(self, mutate) -> Path:
        document = json.loads(default_config_path().read_text(encoding="utf-8"))
        mutate(document)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        config_dir = Path(temp_dir.name) / "config"
        config_dir.mkdir()
        path = config_dir / "monitoring.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    def test_rejects_unsupported_discovery_source(self) -> None:
        def mutate(document):
            document["sources"]["discovery"][0]["id"] = "unknown"

        with self.assertRaisesRegex(MonitoringConfigError, "不支持"):
            load_monitoring_config(self._write_config(mutate))

    def test_rejects_carryover_path_outside_project(self) -> None:
        def mutate(document):
            document["carryover"]["path"] = "../../outside.json"

        with self.assertRaisesRegex(MonitoringConfigError, "不能离开项目目录"):
            load_monitoring_config(self._write_config(mutate))

    def test_rejects_disabling_mandatory_verification(self) -> None:
        def mutate(document):
            document["sources"]["verification"]["require_original"] = False

        with self.assertRaisesRegex(MonitoringConfigError, "必须全部启用"):
            load_monitoring_config(self._write_config(mutate))

    def test_rejects_carryover_without_reverification(self) -> None:
        def mutate(document):
            document["carryover"]["reverify_before_send"] = False

        with self.assertRaisesRegex(MonitoringConfigError, "重新核验"):
            load_monitoring_config(self._write_config(mutate))

    def test_rejects_competitor_priority_outside_one_to_five(self) -> None:
        def mutate(document):
            document["competitors"][0]["priority"] = 6

        with self.assertRaisesRegex(MonitoringConfigError, "1 至 5"):
            load_monitoring_config(self._write_config(mutate))

    def test_rejects_query_without_any_competitor_alias(self) -> None:
        def mutate(document):
            document["competitors"][0]["query"] = '"完全无关的公司"'

        with self.assertRaisesRegex(MonitoringConfigError, "至少一个别名"):
            load_monitoring_config(self._write_config(mutate))


if __name__ == "__main__":
    unittest.main()
