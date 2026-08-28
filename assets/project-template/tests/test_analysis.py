from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from competitor_monitor_bot.analysis import (
    AnalysisError,
    build_analysis_markdown,
    build_analysis_template,
    load_analysis_document,
)
from competitor_monitor_bot.monitoring import load_monitoring_config
from competitor_monitor_bot.news import NewsArticle, title_fingerprint


class AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_monitoring_config()
        title = "示例科技发布AI新产品"
        self.article = NewsArticle(
            competitor_id="example-tech",
            competitor_name="示例科技",
            region="domestic",
            priority=3,
            title=title,
            url="https://news.google.com/articles/example",
            source="新华网",
            source_url="https://www.news.cn",
            published_at=datetime(2026, 8, 25, 1, 25, tzinfo=timezone.utc),
            category="产品/AI",
            fingerprint=title_fingerprint(title),
        )

    def _write_analysis(self, mutate=None) -> Path:
        template = build_analysis_template(
            (self.article,),
            self.config,
            now=datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc),
        )
        template["articles"][0]["fact_summary"] = (
            "报道显示示例科技发布了一项新的人工智能产品与配套服务。"
        )
        template["articles"][0]["impact"] = (
            "值得关注其产品定位和服务模块是否改变同类企业的竞争策略。"
        )
        if mutate:
            mutate(template)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "analysis.json"
        path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
        return path

    def test_validates_and_renders_analysis(self) -> None:
        analyzed = load_analysis_document(
            self._write_analysis(),
            digest_format=self.config.digest.format,
            max_items=self.config.digest.max_items,
        )
        markdown = build_analysis_markdown(
            analyzed,
            self.config,
            now=datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc),
        )
        expected_label = (
            "**发生了什么：**"
            if self.config.digest.format == "analysis"
            else "**摘要：**"
        )
        self.assertIn(expected_label, markdown)
        self.assertIn("[新华网]", markdown)

    def test_rejects_modified_title_fingerprint_pair(self) -> None:
        def mutate(template):
            template["articles"][0]["title"] = "被修改的标题"

        with self.assertRaises(AnalysisError):
            load_analysis_document(
                self._write_analysis(mutate),
                digest_format=self.config.digest.format,
                max_items=self.config.digest.max_items,
            )

    def test_rejects_unfilled_analysis(self) -> None:
        def mutate(template):
            template["articles"][0]["impact"] = ""

        if self.config.digest.format == "analysis":
            with self.assertRaises(AnalysisError):
                load_analysis_document(
                    self._write_analysis(mutate),
                    digest_format=self.config.digest.format,
                    max_items=self.config.digest.max_items,
                )
        else:
            load_analysis_document(
                self._write_analysis(mutate),
                digest_format=self.config.digest.format,
                max_items=self.config.digest.max_items,
            )


if __name__ == "__main__":
    unittest.main()
