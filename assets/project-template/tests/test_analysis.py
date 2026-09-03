from __future__ import annotations

from dataclasses import replace
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
            url="https://www.news.cn/example",
            source="新华网",
            source_url="https://www.news.cn",
            published_at=datetime(2026, 8, 25, 1, 25, tzinfo=timezone.utc),
            category="产品/AI",
            fingerprint=title_fingerprint(title),
            published_at_precision="datetime",
            content_type="independent_report",
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
        self.assertIn("独立报道", markdown)

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

    def test_rejects_candidate_without_original_source_verification(self) -> None:
        def mutate(template):
            template["articles"][0]["published_at_precision"] = "candidate"
            template["articles"][0]["content_type"] = "unverified"

        with self.assertRaisesRegex(AnalysisError, "打开原文"):
            load_analysis_document(self._write_analysis(mutate))

    def test_rejects_discovery_link_after_claimed_verification(self) -> None:
        def mutate(template):
            template["articles"][0]["url"] = (
                "https://news.google.com/articles/example"
            )

        with self.assertRaisesRegex(AnalysisError, "原始报道直链"):
            load_analysis_document(self._write_analysis(mutate))

    def test_rejects_original_publication_outside_lookback_window(self) -> None:
        def mutate(template):
            template["articles"][0]["published_at"] = "2026-08-19T01:25:00+00:00"

        with self.assertRaisesRegex(AnalysisError, "超出监控窗口"):
            load_analysis_document(
                self._write_analysis(mutate),
                lookback_days=7,
            )

    def test_date_precision_does_not_render_a_made_up_time(self) -> None:
        def mutate(template):
            template["articles"][0]["published_at_precision"] = "date"
            template["articles"][0]["published_at"] = "2026-08-25T00:00:00+08:00"

        analyzed = load_analysis_document(self._write_analysis(mutate))
        markdown = build_analysis_markdown(
            analyzed,
            self.config,
            now=datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc),
        )
        self.assertIn("｜08-25｜独立报道", markdown)
        self.assertNotIn("08-25 00:00", markdown)

    def test_accepts_official_legal_notice(self) -> None:
        def mutate(template):
            article = template["articles"][0]
            article["content_type"] = "official_notice"
            article["category"] = "法律/合规"
            article["fact_summary"] = (
                "法院公告列明涉事主体与教育培训合同纠纷，并注明了公告日期。"
            )

        analyzed = load_analysis_document(self._write_analysis(mutate))
        markdown = build_analysis_markdown(analyzed, self.config)
        self.assertIn("官方公告", markdown)

    def test_brand_content_must_be_labeled_in_fact_summary(self) -> None:
        def mutate(template):
            template["articles"][0]["content_type"] = "brand_content"
            template["articles"][0]["fact_summary"] = (
                "报道介绍示例科技新增了人工智能产品以及相关配套服务。"
            )

        with self.assertRaisesRegex(AnalysisError, "品牌内容"):
            load_analysis_document(self._write_analysis(mutate))

        def label_brand_content(template):
            template["articles"][0]["content_type"] = "brand_content"
            template["articles"][0]["fact_summary"] = (
                "品牌内容宣称示例科技发布了新的人工智能产品与配套服务。"
            )

        load_analysis_document(self._write_analysis(label_brand_content))

    def test_rejects_removing_a_due_carryover_article(self) -> None:
        with self.assertRaisesRegex(AnalysisError, "到期延期候选"):
            load_analysis_document(
                self._write_analysis(),
                required_fingerprints={"f" * 64},
            )

    def test_empty_digest_requires_a_successful_collection_source(self) -> None:
        template = build_analysis_template(
            (),
            self.config,
            now=datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc),
        )
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "analysis.json"
        path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")

        with self.assertRaisesRegex(AnalysisError, "至少一个配置来源采集成功"):
            load_analysis_document(path)

        template["collection"] = {"successful_sources": ["baidu"]}
        path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(load_analysis_document(path), ())

    def test_due_carryover_is_not_displaced_by_newer_candidates(self) -> None:
        limited_config = replace(
            self.config,
            digest=replace(
                self.config.digest,
                max_items=2,
                preferred_domestic_items=2,
                preferred_international_items=0,
            ),
        )

        def article(title: str, day: int) -> NewsArticle:
            return replace(
                self.article,
                title=title,
                published_at=datetime(2026, 8, day, tzinfo=timezone.utc),
                fingerprint=title_fingerprint(title),
            )

        due = article("示例科技法院公告", 21)
        recent_one = article("示例科技发布课程一", 27)
        recent_two = article("示例科技发布课程二", 26)
        template = build_analysis_template(
            (recent_one, recent_two, due),
            limited_config,
            now=datetime(2026, 8, 28, tzinfo=timezone.utc),
            required_fingerprints={due.fingerprint},
        )
        selected = {item["fingerprint"] for item in template["articles"]}
        self.assertEqual(len(selected), 2)
        self.assertIn(due.fingerprint, selected)


if __name__ == "__main__":
    unittest.main()
