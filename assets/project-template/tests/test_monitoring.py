from __future__ import annotations

import unittest

from competitor_monitor_bot.monitoring import load_monitoring_config


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


if __name__ == "__main__":
    unittest.main()
