from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Iterable

from .analysis import AnalyzedArticle


class DigestStateError(RuntimeError):
    """Raised when the digest delivery state cannot be updated safely."""


def default_state_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "state.sqlite3"


class DigestState:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_state_path()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS digest_runs (
                digest_date TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ('sending', 'sent')),
                started_at TEXT NOT NULL,
                sent_at TEXT,
                article_count INTEGER
            );

            CREATE TABLE IF NOT EXISTS sent_articles (
                fingerprint TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                digest_date TEXT NOT NULL,
                sent_at TEXT NOT NULL
            );
            """
        )
        connection.commit()
        return connection

    def run_status(self, digest_date: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM digest_runs WHERE digest_date = ?",
                (digest_date,),
            ).fetchone()
        return str(row["status"]) if row else None

    def sent_fingerprints(self, fingerprints: Iterable[str]) -> set[str]:
        unique = tuple(dict.fromkeys(fingerprints))
        if not unique:
            return set()
        placeholders = ",".join("?" for _ in unique)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT fingerprint FROM sent_articles "
                f"WHERE fingerprint IN ({placeholders})",
                unique,
            ).fetchall()
        return {str(row["fingerprint"]) for row in rows}

    def claim_run(self, digest_date: str, started_at: datetime) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO digest_runs (
                    digest_date, status, started_at, sent_at, article_count
                ) VALUES (?, 'sending', ?, NULL, NULL)
                """,
                (digest_date, started_at.isoformat()),
            )
        return cursor.rowcount == 1

    def release_claim(self, digest_date: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM digest_runs "
                "WHERE digest_date = ? AND status = 'sending'",
                (digest_date,),
            )

    def complete_run(
        self,
        digest_date: str,
        sent_at: datetime,
        articles: Iterable[AnalyzedArticle],
    ) -> None:
        article_items = tuple(articles)
        sent_at_value = sent_at.isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE digest_runs
                SET status = 'sent', sent_at = ?, article_count = ?
                WHERE digest_date = ? AND status = 'sending'
                """,
                (sent_at_value, len(article_items), digest_date),
            )
            if cursor.rowcount != 1:
                raise DigestStateError(
                    f"Digest run {digest_date} was not claimed before completion."
                )
            connection.executemany(
                """
                INSERT OR IGNORE INTO sent_articles (
                    fingerprint, title, digest_date, sent_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        item.article.fingerprint,
                        item.article.title,
                        digest_date,
                        sent_at_value,
                    )
                    for item in article_items
                ),
            )

    def record_confirmed_send(
        self,
        digest_date: str,
        sent_at: datetime,
        articles: Iterable[AnalyzedArticle],
    ) -> None:
        article_items = tuple(articles)
        sent_at_value = sent_at.isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO digest_runs (
                    digest_date, status, started_at, sent_at, article_count
                ) VALUES (?, 'sent', ?, ?, ?)
                ON CONFLICT(digest_date) DO UPDATE SET
                    status = 'sent',
                    sent_at = excluded.sent_at,
                    article_count = excluded.article_count
                """,
                (
                    digest_date,
                    sent_at_value,
                    sent_at_value,
                    len(article_items),
                ),
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO sent_articles (
                    fingerprint, title, digest_date, sent_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (
                        item.article.fingerprint,
                        item.article.title,
                        digest_date,
                        sent_at_value,
                    )
                    for item in article_items
                ),
            )
