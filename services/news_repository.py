from __future__ import annotations

import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

DB_PATH = Path("data/news_bot.sqlite3")


@dataclass(slots=True)
class NewsArticleRecord:
    topic: str
    url: str | None
    title: str
    description: str | None
    content: str | None
    published_at: str | None
    image: str | None
    source_name: str | None
    source_url: str | None
    dedupe_key: str
    status: str = "new"
    ru_text: str | None = None
    kk_text: str | None = None
    raw_payload: str | None = None


class NewsRepository:
    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS news_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    url TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    content TEXT,
                    published_at TEXT,
                    image TEXT,
                    source_name TEXT,
                    source_url TEXT,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    posted_at TEXT,
                    status TEXT NOT NULL,
                    ru_text TEXT,
                    kk_text TEXT,
                    raw_payload TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS api_usage (
                    usage_date TEXT PRIMARY KEY,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )

    @staticmethod
    def build_dedupe_key(url: str | None, title: str, published_at: str | None) -> str:
        if url:
            return url.strip()
        base = f"{title.strip()}::{published_at or ''}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    def has_article(self, dedupe_key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM news_posts WHERE dedupe_key = ? LIMIT 1",
                (dedupe_key,),
            ).fetchone()
        return row is not None

    def save_article(self, record: NewsArticleRecord) -> bool:
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO news_posts (
                        topic, url, title, description, content, published_at, image,
                        source_name, source_url, dedupe_key, status, ru_text, kk_text, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.topic,
                        record.url,
                        record.title,
                        record.description,
                        record.content,
                        record.published_at,
                        record.image,
                        record.source_name,
                        record.source_url,
                        record.dedupe_key,
                        record.status,
                        record.ru_text,
                        record.kk_text,
                        record.raw_payload,
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                logger.info("[NEWS DEDUPE] article already exists dedupe_key=%s", record.dedupe_key)
                return False

    def update_article_status(
        self,
        dedupe_key: str,
        status: str,
        *,
        ru_text: str | None = None,
        kk_text: str | None = None,
        posted_at: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE news_posts
                SET status = ?,
                    ru_text = COALESCE(?, ru_text),
                    kk_text = COALESCE(?, kk_text),
                    posted_at = COALESCE(?, posted_at),
                    updated_at = CURRENT_TIMESTAMP
                WHERE dedupe_key = ?
                """,
                (status, ru_text, kk_text, posted_at, dedupe_key),
            )

    def increment_daily_requests(self, usage_date: str | None = None, amount: int = 1) -> int:
        usage_date = usage_date or datetime.now(UTC).date().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO api_usage (usage_date, request_count)
                VALUES (?, ?)
                ON CONFLICT(usage_date) DO UPDATE SET
                    request_count = request_count + excluded.request_count,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (usage_date, amount),
            )
            row = connection.execute(
                "SELECT request_count FROM api_usage WHERE usage_date = ?",
                (usage_date,),
            ).fetchone()
        return int(row[0]) if row else 0

    def get_daily_requests(self, usage_date: str | None = None) -> int:
        usage_date = usage_date or datetime.now(UTC).date().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_count FROM api_usage WHERE usage_date = ?",
                (usage_date,),
            ).fetchone()
        return int(row[0]) if row else 0

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO bot_meta (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM bot_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def mark_last_fetch_time(self, value: datetime | None = None) -> None:
        timestamp = (value or datetime.now(UTC)).isoformat()
        self.set_meta("last_fetch_time", timestamp)

    def get_stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            total_saved = connection.execute("SELECT COUNT(*) FROM news_posts").fetchone()[0]
            total_posted = connection.execute(
                "SELECT COUNT(*) FROM news_posts WHERE status = 'posted'"
            ).fetchone()[0]
            total_failed = connection.execute(
                "SELECT COUNT(*) FROM news_posts WHERE status = 'failed'"
            ).fetchone()[0]
        return {
            "total_saved_articles": int(total_saved),
            "total_posted_articles": int(total_posted),
            "total_failed_articles": int(total_failed),
            "last_fetch_time": self.get_meta("last_fetch_time"),
        }
