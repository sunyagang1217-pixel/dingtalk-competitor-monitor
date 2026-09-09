from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path
import sqlite3
from typing import Any

from .monitoring import MonitoringConfig
from .state import default_state_path
from .supplement import validate_supplement_id


@dataclass(frozen=True)
class DeliveryCheck:
    status: str
    digest_date: str
    checked_at: str
    complete: bool
    message: str
    phase: str
    sent_at: str | None = None
    article_count: int | None = None
    supplement_id: str | None = None

    @property
    def exit_code(self) -> int:
        return 0 if self.complete or self.status == "not_scheduled" else 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_delivery_result(
    config: MonitoringConfig,
    *,
    state_path: str | Path | None = None,
    digest_date: date | None = None,
    now: datetime | None = None,
    supplement_id: str | None = None,
) -> DeliveryCheck:
    """Verify a committed delivery without credentials, network access or writes."""
    zone = config.schedule.zoneinfo()
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    target = digest_date if digest_date is not None else local_now.date()
    target_text = target.isoformat()
    if supplement_id is not None:
        validate_supplement_id(supplement_id)
    check_time = config.schedule.result_check_time
    phase = (
        "result_check"
        if check_time and local_now.time() >= time.fromisoformat(check_time)
        else "delivery"
    )

    def result(status: str, message: str, **details: Any) -> DeliveryCheck:
        return DeliveryCheck(
            status=status,
            digest_date=target_text,
            checked_at=local_now.isoformat(),
            complete=status == "sent",
            message=message,
            phase=phase,
            supplement_id=supplement_id,
            **details,
        )

    scheduled = target.isoweekday() in config.schedule.weekdays
    path = Path(state_path) if state_path is not None else default_state_path()
    try:
        if not scheduled and not path.exists():
            return result("not_scheduled", "该日期不在配置的发送星期内，无需发送。")
        # Do not use DigestState._connect(): diagnostics must never create a DB.
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            run_table = "supplement_runs" if supplement_id is not None else "digest_runs"
            article_table = "supplement_articles" if supplement_id is not None else "sent_articles"
            where = "digest_date = ?" + (" AND supplement_id = ?" if supplement_id is not None else "")
            parameters = (target_text, supplement_id) if supplement_id is not None else (target_text,)
            run = connection.execute(
                f"SELECT status, sent_at, article_count FROM {run_table} WHERE {where}",
                parameters,
            ).fetchone()
            articles = connection.execute(
                f"SELECT sent_at FROM {article_table} WHERE {where}",
                parameters,
            ).fetchall()
    except (sqlite3.Error, OSError, ValueError):
        return result("state_unavailable", "无法只读检查发送状态库，不能确认日报已发送。")

    if run is None:
        if articles:
            return result("inconsistent", "存在文章记录但缺少对应日报记录，需人工核查。")
        if not scheduled:
            return result("not_scheduled", "该日期不在配置的发送星期内，无需发送。")
        return result("missing", "未找到该日期的成功发送记录，日报尚未完成；不能据此判断没有新增。")
    if run["status"] == "sending":
        return result("in_progress", "存在发送占位但没有成功完成记录，可能仍在运行或已中断；不要自动重发。")
    if run["status"] != "sent":
        return result("inconsistent", "日报记录不是成功状态，需人工核查。")

    sent_at_text = run["sent_at"]
    try:
        sent_at = datetime.fromisoformat(sent_at_text)
    except (TypeError, ValueError):
        return result("inconsistent", "成功记录缺少有效发送时间，需人工核查。")
    if (
        sent_at.tzinfo is None
        or sent_at.astimezone(zone).date() != target
        or sent_at > local_now
    ):
        return result("inconsistent", "发送时间缺少时区、日期不符或晚于检查时间，需人工核查。")

    count = run["article_count"]
    if (
        type(count) is not int
        or count < 0
        or count != len(articles)
        or any(row["sent_at"] != sent_at_text for row in articles)
    ):
        return result("inconsistent", "日报条数或发送时间与文章去重记录不一致，需人工核查。")

    return result(
        "sent",
        "已核实该日期的成功发送记录，发送时间和文章去重记录一致。",
        sent_at=sent_at_text,
        article_count=count,
    )
