from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "scripts" / "scaffold_project.py"
SPEC = importlib.util.spec_from_file_location("scaffold_project", MODULE_PATH)
assert SPEC and SPEC.loader
scaffold_project = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scaffold_project)


class ScaffoldProjectTests(unittest.TestCase):
    def _specification(self) -> dict:
        return {
            "project": {
                "slug": "example-training-monitor",
                "display_name": "示例培训竞品监控机器人",
            },
            "industry": {
                "name": "职业培训",
                "digest_title": "职业培训竞品日报",
            },
            "regions": ["domestic", "international"],
            "schedule": {
                "timezone": "Asia/Shanghai",
                "time": "10:30",
                "weekdays": [1, 2, 3, 4, 5],
            },
            "digest": {
                "format": "analysis",
                "max_items": 6,
                "lookback_days": 7,
                "preferred_domestic_items": 4,
                "preferred_international_items": 2,
            },
            "sources": {
                "enabled": [
                    "google_news",
                    "baidu",
                    "360",
                    "wechat_articles",
                ]
            },
            "carryover": {"enabled": True},
            "competitors": [
                {
                    "id": "example-cn",
                    "name": "示例教育",
                    "region": "domestic",
                    "priority": 5,
                    "aliases": ["示例教育", "示例课堂"],
                    "query": '"示例教育" OR "示例课堂" 职业培训',
                },
                {
                    "id": "example-global",
                    "name": "Example Learning",
                    "region": "international",
                    "priority": 4,
                    "aliases": ["Example Learning", "ExampleLearning"],
                    "query": '"Example Learning" OR ExampleLearning',
                },
            ],
        }

    def test_generates_four_peer_sources_and_carryover_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            output = Path(temporary_dir) / "generated-project"
            validated = scaffold_project.validate_spec(self._specification())
            scaffold_project.scaffold(validated, output)

            monitoring = json.loads(
                (output / "config" / "monitoring.json").read_text(
                    encoding="utf-8"
                )
            )
            sources = monitoring["sources"]["discovery"]
            self.assertEqual(
                [source["id"] for source in sources if source["enabled"]],
                ["google_news", "baidu", "360", "wechat_articles"],
            )
            self.assertTrue(
                all(
                    source["regions"] == ["domestic", "international"]
                    for source in sources
                )
            )
            self.assertIn(
                "所有提及竞品的公众号文章",
                next(
                    source["scope"]
                    for source in sources
                    if source["id"] == "wechat_articles"
                ),
            )
            self.assertTrue(monitoring["carryover"]["enabled"])
            self.assertEqual(
                monitoring["carryover"]["path"],
                "data/pending_articles.json",
            )

            queue = json.loads(
                (output / "data" / "pending_articles.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(queue["schema_version"], 1)
            self.assertEqual(queue["articles"], [])

            readme = (output / "README.md").read_text(encoding="utf-8")
            prompt = (output / "config" / "analysis_prompt.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Google News、百度搜索、360搜索、微信公众号搜索", readme)
            self.assertIn("均为同级发现渠道", prompt)
            self.assertNotRegex(readme + prompt, r"__[A-Z0-9_]+__")

    def test_allows_an_explicit_subset_but_keeps_sources_configurable(self) -> None:
        specification = self._specification()
        specification["sources"]["enabled"] = ["baidu", "wechat_articles"]
        validated = scaffold_project.validate_spec(specification)

        with tempfile.TemporaryDirectory() as temporary_dir:
            output = Path(temporary_dir) / "generated-project"
            scaffold_project.scaffold(validated, output)
            monitoring = json.loads(
                (output / "config" / "monitoring.json").read_text(
                    encoding="utf-8"
                )
            )

        enabled = {
            source["id"]: source["enabled"]
            for source in monitoring["sources"]["discovery"]
        }
        self.assertEqual(
            enabled,
            {
                "google_news": False,
                "baidu": True,
                "360": False,
                "wechat_articles": True,
            },
        )

    def test_rejects_credentials_in_specification(self) -> None:
        specification = self._specification()
        specification["webhook"] = (
            "https://oapi.dingtalk.com/robot/send?access_token=not-a-real-token"
        )

        with self.assertRaisesRegex(scaffold_project.SpecError, "凭据"):
            scaffold_project.validate_spec(specification)

    def test_rejects_unknown_or_empty_sources(self) -> None:
        unknown = self._specification()
        unknown["sources"]["enabled"] = ["unknown"]
        with self.assertRaisesRegex(scaffold_project.SpecError, "不支持的来源"):
            scaffold_project.validate_spec(unknown)

        empty = self._specification()
        empty["sources"]["enabled"] = []
        with self.assertRaisesRegex(scaffold_project.SpecError, "非空列表"):
            scaffold_project.validate_spec(empty)


if __name__ == "__main__":
    unittest.main()
