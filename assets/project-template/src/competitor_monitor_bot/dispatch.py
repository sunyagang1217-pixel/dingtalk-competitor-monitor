from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .analysis import build_analysis_markdown, load_analysis_document
from .carryover import CarryoverError, load_due_articles, mark_pending_sent
from .dingtalk import DingTalkError
from .monitoring import MonitoringConfig
from .state import DigestState


class MarkdownSender(Protocol):
    def send_markdown(
        self,
        title: str,
        text: str,
        *,
        at_all: bool = False,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DispatchResult:
    status: str
    digest_date: str
    article_count: int
    skipped_sent_articles: int
    errcode: Any = None
    errmsg: Any = None
    carryover_completed: int = 0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def dispatch_analysis(
    input_path: str | Path,
    config: MonitoringConfig,
    client: MarkdownSender,
    *,
    state_path: str | Path | None = None,
    now: datetime | None = None,
) -> DispatchResult:
    zone = config.schedule.zoneinfo()
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    digest_date = local_now.date().isoformat()
    state = DigestState(state_path)

    existing_status = state.run_status(digest_date)
    if existing_status:
        return DispatchResult(
            status="already_sent" if existing_status == "sent" else "already_running",
            digest_date=digest_date,
            article_count=0,
            skipped_sent_articles=0,
        )

    due_fingerprints = (
        {
            article.fingerprint
            for article in load_due_articles(
                config.carryover.path,
                on_date=local_now.date(),
                competitors=config.competitors,
            )
        }
        if config.carryover.enabled
        else set()
    )
    previously_sent_due = state.sent_fingerprints(due_fingerprints)
    analyzed = load_analysis_document(
        input_path,
        digest_format=config.digest.format,
        max_items=config.digest.max_items,
        lookback_days=config.digest.lookback_days,
        required_fingerprints=due_fingerprints - previously_sent_due,
        competitors=config.competitors,
    )
    sent_fingerprints = state.sent_fingerprints(
        item.article.fingerprint for item in analyzed
    )
    fresh = tuple(
        item for item in analyzed if item.article.fingerprint not in sent_fingerprints
    )
    skipped = len(analyzed) - len(fresh)

    if not state.claim_run(digest_date, local_now):
        status = state.run_status(digest_date)
        return DispatchResult(
            status="already_sent" if status == "sent" else "already_running",
            digest_date=digest_date,
            article_count=0,
            skipped_sent_articles=skipped,
        )

    title = f"{config.digest_title}｜{local_now:%Y-%m-%d}"
    markdown = build_analysis_markdown(fresh, config, now=local_now)
    try:
        response = client.send_markdown(title, markdown, at_all=False)
    except Exception:
        state.release_claim(digest_date)
        raise

    if response.get("errcode") != 0:
        state.release_claim(digest_date)
        raise DingTalkError(
            "钉钉未确认消息发送成功；未写入日报发送状态。"
        )

    state.complete_run(digest_date, local_now, fresh)
    carryover_completed = 0
    warnings: list[str] = []
    if config.carryover.enabled:
        try:
            carryover_completed = mark_pending_sent(
                config.carryover.path,
                (item.article.fingerprint for item in fresh),
                sent_at=local_now,
            )
        except CarryoverError as exc:
            warnings.append(str(exc))
    return DispatchResult(
        status="sent",
        digest_date=digest_date,
        article_count=len(fresh),
        skipped_sent_articles=skipped,
        errcode=response.get("errcode"),
        errmsg=response.get("errmsg"),
        carryover_completed=carryover_completed,
        warnings=tuple(warnings),
    )
