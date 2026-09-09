from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path
import re
from typing import Iterable

from .analysis import AnalyzedArticle
from .state import DigestState, DigestStateError


def validate_supplement_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) or len(value) > 64:
        raise DigestStateError("补发标识必须是 1-64 位小写字母、数字或连字符。")
    return value


class SupplementState(DigestState):
    """Append-only delivery history for an explicitly authorized supplement."""

    def __init__(self, supplement_id: str, path: str | Path | None = None):
        super().__init__(path)
        self.supplement_id = validate_supplement_id(supplement_id)

    def run_status(self, digest_date: str) -> str | None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT status FROM supplement_runs WHERE digest_date=? AND supplement_id=?",
                (digest_date, self.supplement_id),
            ).fetchone()
        return str(row["status"]) if row else None

    def claim_run(self, digest_date: str, started_at: datetime) -> bool:
        with closing(self._connect()) as connection, connection:
            # Serialize against every unfinished delivery, including other supplements.
            connection.execute("BEGIN IMMEDIATE")
            busy = connection.execute(
                "SELECT 1 FROM digest_runs WHERE digest_date=? AND status='sending' "
                "UNION ALL SELECT 1 FROM supplement_runs WHERE digest_date=? AND status='sending'",
                (digest_date, digest_date),
            ).fetchone()
            if busy:
                return False
            cursor = connection.execute(
                "INSERT OR IGNORE INTO supplement_runs "
                "(digest_date,supplement_id,status,started_at) VALUES (?,?,'sending',?)",
                (digest_date, self.supplement_id, started_at.isoformat()),
            )
        return cursor.rowcount == 1

    def release_claim(self, digest_date: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM supplement_runs WHERE digest_date=? AND supplement_id=? AND status='sending'",
                (digest_date, self.supplement_id),
            )

    def complete_run(self, digest_date: str, sent_at: datetime, articles: Iterable[AnalyzedArticle]) -> None:
        items = tuple(articles)
        stamp = sent_at.isoformat()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE supplement_runs SET status='sent',sent_at=?,article_count=? "
                "WHERE digest_date=? AND supplement_id=? AND status='sending'",
                (stamp, len(items), digest_date, self.supplement_id),
            )
            if cursor.rowcount != 1:
                raise DigestStateError("补发缺少对应占位，不能记录成功。")
            connection.executemany(
                "INSERT INTO supplement_articles (fingerprint,title,digest_date,supplement_id,sent_at) "
                "VALUES (?,?,?,?,?)",
                ((item.article.fingerprint, item.article.title, digest_date, self.supplement_id, stamp) for item in items),
            )
