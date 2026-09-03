from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable

from .monitoring import Competitor
from .news import (
    NewsArticle,
    VERIFIED_CONTENT_TYPES,
    VERIFIED_PUBLISHED_AT_PRECISIONS,
    title_fingerprint,
)


class CarryoverError(RuntimeError):
    """Raised when the deferred-article queue cannot be used safely."""


def _load_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CarryoverError(f"无法读取延期候选队列：{path}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise CarryoverError("延期候选队列版本无效。")
    articles = document.get("articles")
    if not isinstance(articles, list):
        raise CarryoverError("延期候选队列中的 articles 必须是列表。")
    return document


def _required_text(item: dict[str, Any], field: str, index: int) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CarryoverError(f"延期候选第 {index} 条缺少字段 {field}。")
    return value.strip()


def _article_from_item(item: dict[str, Any], index: int) -> NewsArticle:
    title = _required_text(item, "title", index)
    fingerprint = _required_text(item, "fingerprint", index)
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise CarryoverError(f"延期候选第 {index} 条的指纹无效。")
    if title_fingerprint(title) != fingerprint:
        raise CarryoverError(f"延期候选第 {index} 条的标题与指纹不一致。")
    url = _required_text(item, "url", index)
    if not url.startswith("https://"):
        raise CarryoverError(f"延期候选第 {index} 条的网址必须使用 HTTPS。")
    source_url = _required_text(item, "source_url", index)
    if not source_url.startswith("https://"):
        raise CarryoverError(f"延期候选第 {index} 条的来源网址必须使用 HTTPS。")
    try:
        published_at = datetime.fromisoformat(
            _required_text(item, "published_at", index)
        )
    except ValueError as exc:
        raise CarryoverError(f"延期候选第 {index} 条的发布时间无效。") from exc
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    precision = _required_text(item, "published_at_precision", index)
    if precision not in VERIFIED_PUBLISHED_AT_PRECISIONS:
        raise CarryoverError(f"延期候选第 {index} 条尚未核验真实发布时间。")
    content_type = _required_text(item, "content_type", index)
    if content_type not in VERIFIED_CONTENT_TYPES:
        raise CarryoverError(f"延期候选第 {index} 条尚未核验内容属性。")
    priority = item.get("priority")
    if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
        raise CarryoverError(f"延期候选第 {index} 条的优先级必须是正整数。")
    region = _required_text(item, "region", index)
    if region not in {"domestic", "international"}:
        raise CarryoverError(f"延期候选第 {index} 条的地区无效。")
    return NewsArticle(
        competitor_id=_required_text(item, "competitor_id", index),
        competitor_name=_required_text(item, "competitor_name", index),
        region=region,
        priority=priority,
        title=title,
        url=url,
        source=_required_text(item, "source", index),
        source_url=source_url,
        published_at=published_at,
        category=_required_text(item, "category", index),
        fingerprint=fingerprint,
        published_at_precision=precision,
        content_type=content_type,
    )


def load_due_articles(
    path: str | Path,
    *,
    on_date: date,
    competitors: Iterable[Competitor] | None = None,
) -> tuple[NewsArticle, ...]:
    queue_path = Path(path)
    document = _load_document(queue_path)
    competitor_scope = (
        {competitor.id: competitor for competitor in competitors}
        if competitors is not None
        else None
    )
    due: list[NewsArticle] = []
    seen_fingerprints: set[str] = set()
    for index, raw_item in enumerate(document["articles"], 1):
        if not isinstance(raw_item, dict):
            raise CarryoverError(f"延期候选第 {index} 条必须是 JSON 对象。")
        review_status = _required_text(raw_item, "review_status", index)
        queue_status = _required_text(raw_item, "queue_status", index)
        if review_status not in {"pending", "verified"}:
            raise CarryoverError(f"延期候选第 {index} 条的 review_status 无效。")
        if queue_status not in {"queued", "sent"}:
            raise CarryoverError(f"延期候选第 {index} 条的 queue_status 无效。")
        try:
            send_after = date.fromisoformat(
                _required_text(raw_item, "send_after", index)
            )
        except ValueError as exc:
            raise CarryoverError(f"延期候选第 {index} 条的 send_after 无效。") from exc
        if review_status != "verified" or queue_status != "queued" or send_after > on_date:
            continue
        article = _article_from_item(raw_item, index)
        if competitor_scope is not None:
            configured = competitor_scope.get(article.competitor_id)
            if configured is None or (
                article.competitor_name != configured.name
                or article.region != configured.region
                or article.priority != configured.priority
            ):
                raise CarryoverError(
                    f"延期候选第 {index} 条与当前竞品配置不一致。"
                )
        if article.fingerprint in seen_fingerprints:
            raise CarryoverError(f"延期候选第 {index} 条与前面的条目重复。")
        seen_fingerprints.add(article.fingerprint)
        due.append(article)
    return tuple(due)


def mark_pending_sent(
    path: str | Path,
    fingerprints: Iterable[str],
    *,
    sent_at: datetime,
) -> int:
    targets = set(fingerprints)
    if not targets:
        return 0
    queue_path = Path(path)
    document = _load_document(queue_path)
    changed = 0
    for raw_item in document["articles"]:
        if not isinstance(raw_item, dict):
            continue
        if (
            raw_item.get("fingerprint") in targets
            and raw_item.get("review_status") == "verified"
            and raw_item.get("queue_status") == "queued"
        ):
            raw_item["queue_status"] = "sent"
            raw_item["sent_at"] = sent_at.isoformat()
            changed += 1
    if not changed:
        return 0
    temporary_path = queue_path.with_name(f".{queue_path.name}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(queue_path)
    except OSError as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise CarryoverError("日报已发送，但无法更新延期候选队列。") from exc
    return changed
