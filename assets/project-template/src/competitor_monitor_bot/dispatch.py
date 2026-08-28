from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .analysis import build_analysis_markdown, load_analysis_document
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

    analyzed = load_analysis_document(
        input_path,
        digest_format=config.digest.format,
        max_items=config.digest.max_items,
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

    state.complete_run(digest_date, local_now, fresh)
    return DispatchResult(
        status="sent",
        digest_date=digest_date,
        article_count=len(fresh),
        skipped_sent_articles=skipped,
        errcode=response.get("errcode"),
        errmsg=response.get("errmsg"),
    )
