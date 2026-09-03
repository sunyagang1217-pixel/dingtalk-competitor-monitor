from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import Sequence

from .analysis import (
    AnalysisError,
    build_analysis_markdown,
    build_analysis_template,
    load_analysis_document,
)
from .carryover import CarryoverError, load_due_articles, mark_pending_sent
from .config import ConfigError, load_credentials
from .digest import build_digest_markdown
from .dispatch import dispatch_analysis
from .dingtalk import DingTalkClient, DingTalkError, build_markdown_payload
from .monitoring import MonitoringConfigError, load_monitoring_config
from .news import CollectionResult, NewsCollectionError, collect_news
from .state import DigestState, DigestStateError


LIVE_SEND_CONFIRMATION = "SEND_TO_DINGTALK"
RECORD_SEND_CONFIRMATION = "RECORD_CONFIRMED_SEND"


def _require_collection_success(collection: CollectionResult) -> None:
    if collection.has_successful_source:
        return
    details = "；".join(collection.errors[:4])
    suffix = f"原因：{details}" if details else "未返回可用来源结果。"
    raise NewsCollectionError(f"所有配置采集来源均失败，未生成日报素材。{suffix}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="competitor-monitor-bot",
        description="采集、校验并发送行业竞品分析日报。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "check-config",
        help="校验本地凭据，但不显示凭据内容。",
    )

    preview = subparsers.add_parser(
        "preview",
        help="只预览钉钉消息，不读取凭据、不发送。",
    )
    preview.add_argument("--title", required=True)
    preview.add_argument("--text", required=True)
    preview.add_argument("--at-all", action="store_true")

    collect_preview = subparsers.add_parser(
        "collect-preview",
        help="采集公开新闻并预览 Markdown，不发送。",
    )
    collect_preview.add_argument(
        "--config",
        help="可选监控配置路径，默认使用 config/monitoring.json。",
    )

    collect_json = subparsers.add_parser(
        "collect-json",
        help="采集公开新闻并生成待分析 JSON。",
    )
    collect_json.add_argument(
        "--config",
        help="可选监控配置路径，默认使用 config/monitoring.json。",
    )
    collect_json.add_argument(
        "--output",
        help="可选输出路径；不填写时输出到终端。",
    )
    collect_json.add_argument(
        "--state",
        help="可选去重数据库路径，默认使用 data/state.sqlite3。",
    )
    collect_json.add_argument(
        "--include-sent",
        action="store_true",
        help="包含已经记录为已发送的报道。",
    )

    analysis_preview = subparsers.add_parser(
        "analysis-preview",
        help="校验分析 JSON 并预览钉钉 Markdown。",
    )
    analysis_preview.add_argument("--input", required=True)
    analysis_preview.add_argument(
        "--config",
        help="可选监控配置路径，默认使用 config/monitoring.json。",
    )

    send = subparsers.add_parser(
        "send",
        help="向已配置的钉钉群发送一条经过确认的消息。",
    )
    send.add_argument("--title", required=True)
    send.add_argument("--text", required=True)
    send.add_argument("--at-all", action="store_true")
    send.add_argument(
        "--confirm",
        required=True,
        help=f"必须精确填写 {LIVE_SEND_CONFIRMATION}。",
    )

    send_analysis = subparsers.add_parser(
        "send-analysis",
        help="发送一份已校验日报，并执行日期与文章去重。",
    )
    send_analysis.add_argument("--input", required=True)
    send_analysis.add_argument(
        "--config",
        help="可选监控配置路径，默认使用 config/monitoring.json。",
    )
    send_analysis.add_argument(
        "--state",
        help="可选去重数据库路径，默认使用 data/state.sqlite3。",
    )
    send_analysis.add_argument(
        "--confirm",
        required=True,
        help=f"必须精确填写 {LIVE_SEND_CONFIRMATION}。",
    )

    record_sent = subparsers.add_parser(
        "record-analysis-sent",
        help="只记录一份已经确认送达的日报，不再次发送。",
    )
    record_sent.add_argument("--input", required=True)
    record_sent.add_argument("--digest-date", required=True)
    record_sent.add_argument(
        "--config",
        help="可选监控配置路径，默认使用 config/monitoring.json。",
    )
    record_sent.add_argument(
        "--state",
        help="可选去重数据库路径，默认使用 data/state.sqlite3。",
    )
    record_sent.add_argument(
        "--confirm",
        required=True,
        help=f"必须精确填写 {RECORD_SEND_CONFIRMATION}。",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    try:
        if args.command == "check-config":
            credentials = load_credentials()
            print(
                json.dumps(
                    {
                        "configured": True,
                        "source": credentials.source,
                        "values_redacted": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "preview":
            payload = build_markdown_payload(
                args.title,
                args.text,
                at_all=args.at_all,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command == "collect-preview":
            monitoring = load_monitoring_config(args.config)
            collection = collect_news(monitoring)
            _require_collection_success(collection)
            print(build_digest_markdown(collection.articles, monitoring))
            if collection.errors:
                print(
                    f"警告：有 {len(collection.errors)} 个来源采集失败。",
                    file=sys.stderr,
                )
            return 0

        if args.command == "collect-json":
            monitoring = load_monitoring_config(args.config)
            collection = collect_news(monitoring)
            _require_collection_success(collection)
            local_now = datetime.now(monitoring.schedule.zoneinfo())
            due_articles = (
                load_due_articles(
                    monitoring.carryover.path,
                    on_date=local_now.date(),
                    competitors=monitoring.competitors,
                )
                if monitoring.carryover.enabled
                else ()
            )
            articles_by_fingerprint = {
                article.fingerprint: article for article in collection.articles
            }
            for article in due_articles:
                articles_by_fingerprint[article.fingerprint] = article
            articles = tuple(articles_by_fingerprint.values())
            excluded_sent = 0
            reconciled_carryover = 0
            if not args.include_sent:
                state = DigestState(args.state)
                sent_fingerprints = state.sent_fingerprints(
                    article.fingerprint for article in articles
                )
                articles = tuple(
                    article
                    for article in articles
                    if article.fingerprint not in sent_fingerprints
                )
                excluded_sent = len(articles_by_fingerprint) - len(articles)
                if monitoring.carryover.enabled:
                    reconciled_carryover = mark_pending_sent(
                        monitoring.carryover.path,
                        sent_fingerprints,
                        sent_at=local_now,
                    )
            due_fingerprints = {
                article.fingerprint for article in due_articles
            } - (sent_fingerprints if not args.include_sent else set())
            template = build_analysis_template(
                articles,
                monitoring,
                now=local_now,
                required_fingerprints=due_fingerprints,
            )
            template["collection"] = {
                "enabled_sources": [
                    source.id
                    for source in monitoring.discovery_sources
                    if source.enabled
                ],
                "successful_sources": list(collection.successful_sources),
                "source_errors": list(collection.errors),
            }
            template["carryover"] = {
                "enabled": monitoring.carryover.enabled,
                "due_articles": len(due_fingerprints),
                "reverification_required": bool(due_fingerprints),
                "reconciled_sent_articles": reconciled_carryover,
            }
            serialized = json.dumps(template, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(serialized, encoding="utf-8")
                print(
                    json.dumps(
                        {
                            "output": str(output_path),
                            "articles": len(template["articles"]),
                            "excluded_sent": excluded_sent,
                            "source_errors": len(collection.errors),
                            "source_failures": list(collection.errors),
                            "successful_sources": list(collection.successful_sources),
                            "carryover_due": len(due_fingerprints),
                            "carryover_reconciled": reconciled_carryover,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(serialized, end="")
            return 0

        if args.command == "analysis-preview":
            monitoring = load_monitoring_config(args.config)
            local_now = datetime.now(monitoring.schedule.zoneinfo())
            required_fingerprints = (
                {
                    article.fingerprint
                    for article in load_due_articles(
                        monitoring.carryover.path,
                        on_date=local_now.date(),
                        competitors=monitoring.competitors,
                    )
                }
                if monitoring.carryover.enabled
                else set()
            )
            analyzed = load_analysis_document(
                args.input,
                digest_format=monitoring.digest.format,
                max_items=monitoring.digest.max_items,
                lookback_days=monitoring.digest.lookback_days,
                required_fingerprints=required_fingerprints,
                competitors=monitoring.competitors,
            )
            print(build_analysis_markdown(analyzed, monitoring, now=local_now))
            return 0

        if args.command == "record-analysis-sent":
            if args.confirm != RECORD_SEND_CONFIRMATION:
                print(
                    "已拒绝更新状态：确认口令不匹配。",
                    file=sys.stderr,
                )
                return 2
            try:
                digest_date = date.fromisoformat(args.digest_date).isoformat()
            except ValueError:
                print("错误：日报日期必须使用 YYYY-MM-DD 格式。", file=sys.stderr)
                return 1
            monitoring = load_monitoring_config(args.config)
            analyzed = load_analysis_document(
                args.input,
                digest_format=monitoring.digest.format,
                max_items=monitoring.digest.max_items,
                lookback_days=monitoring.digest.lookback_days,
                competitors=monitoring.competitors,
            )
            state = DigestState(args.state)
            state.record_confirmed_send(
                digest_date,
                datetime.now().astimezone(),
                analyzed,
            )
            carryover_completed = 0
            carryover_warnings: list[str] = []
            if monitoring.carryover.enabled:
                try:
                    carryover_completed = mark_pending_sent(
                        monitoring.carryover.path,
                        (item.article.fingerprint for item in analyzed),
                        sent_at=datetime.now(monitoring.schedule.zoneinfo()),
                    )
                except CarryoverError as exc:
                    carryover_warnings.append(str(exc))
            print(
                json.dumps(
                    {
                        "recorded": True,
                        "digest_date": digest_date,
                        "articles": len(analyzed),
                        "carryover_completed": carryover_completed,
                        "warnings": carryover_warnings,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if args.command == "send-analysis":
            if args.confirm != LIVE_SEND_CONFIRMATION:
                print(
                    "已拒绝真实发送：确认口令不匹配。",
                    file=sys.stderr,
                )
                return 2
            monitoring = load_monitoring_config(args.config)
            credentials = load_credentials()
            client = DingTalkClient(credentials)
            result = dispatch_analysis(
                args.input,
                monitoring,
                client,
                state_path=args.state,
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.confirm != LIVE_SEND_CONFIRMATION:
            print(
                "已拒绝真实发送：确认口令不匹配。",
                file=sys.stderr,
            )
            return 2

        credentials = load_credentials()
        client = DingTalkClient(credentials)
        result = client.send_markdown(
            args.title,
            args.text,
            at_all=args.at_all,
        )
        print(
            json.dumps(
                {
                    "sent": True,
                    "errcode": result.get("errcode"),
                    "errmsg": result.get("errmsg"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (
        ConfigError,
        AnalysisError,
        DingTalkError,
        MonitoringConfigError,
        NewsCollectionError,
        CarryoverError,
        DigestStateError,
        ValueError,
    ) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
