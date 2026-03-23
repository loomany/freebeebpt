from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from services.dedup import build_article_hash

logger = logging.getLogger(__name__)


def _default_db_path() -> Path:
    env_path = os.getenv("NEWS_DB_PATH")
    if env_path:
        return Path(env_path)

    railway_volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if railway_volume_path:
        return Path(railway_volume_path) / "news_bot.sqlite3"

    return Path("data/news_bot.sqlite3")


DB_PATH = _default_db_path()


@dataclass(slots=True)
class NewsArticleRecord:
    topic: str
    article_hash: str
    url: str | None
    title: str
    description: str | None
    content: str | None
    published_at: str | None
    image: str | None
    source_name: str | None
    source_url: str | None
    status: str = "new"
    sent_to_channel: bool = False
    translated_text: str | None = None
    raw_payload: str | None = None
    importance_score: int | None = None
    importance_level: str | None = None
    rewritten_title_kk: str | None = None
    summary_kk: str | None = None
    betting_impact_kk: str | None = None
    team_impact_kk: str | None = None
    image_prompt_en: str | None = None
    generated_image_url: str | None = None
    send_reason: str | None = None
    skip_reason: str | None = None


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
                CREATE TABLE IF NOT EXISTS news_sent (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_hash TEXT NOT NULL UNIQUE,
                    source_name TEXT,
                    title TEXT NOT NULL,
                    url TEXT,
                    published_at TEXT,
                    sent_at TEXT,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    sent_to_channel INTEGER NOT NULL DEFAULT 0,
                    description TEXT,
                    content TEXT,
                    image TEXT,
                    source_url TEXT,
                    translated_text TEXT,
                    raw_payload TEXT,
                    importance_score INTEGER,
                    importance_level TEXT,
                    rewritten_title_kk TEXT,
                    summary_kk TEXT,
                    betting_impact_kk TEXT,
                    team_impact_kk TEXT,
                    image_prompt_en TEXT,
                    generated_image_url TEXT,
                    send_reason TEXT,
                    skip_reason TEXT,
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
            existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(news_sent)").fetchall()}
            migrations = {
                "sent_to_channel": "ALTER TABLE news_sent ADD COLUMN sent_to_channel INTEGER NOT NULL DEFAULT 0",
                "importance_score": "ALTER TABLE news_sent ADD COLUMN importance_score INTEGER",
                "importance_level": "ALTER TABLE news_sent ADD COLUMN importance_level TEXT",
                "rewritten_title_kk": "ALTER TABLE news_sent ADD COLUMN rewritten_title_kk TEXT",
                "summary_kk": "ALTER TABLE news_sent ADD COLUMN summary_kk TEXT",
                "betting_impact_kk": "ALTER TABLE news_sent ADD COLUMN betting_impact_kk TEXT",
                "team_impact_kk": "ALTER TABLE news_sent ADD COLUMN team_impact_kk TEXT",
                "image_prompt_en": "ALTER TABLE news_sent ADD COLUMN image_prompt_en TEXT",
                "generated_image_url": "ALTER TABLE news_sent ADD COLUMN generated_image_url TEXT",
                "send_reason": "ALTER TABLE news_sent ADD COLUMN send_reason TEXT",
                "skip_reason": "ALTER TABLE news_sent ADD COLUMN skip_reason TEXT",
            }
            for column, statement in migrations.items():
                if column not in existing_columns:
                    connection.execute(statement)

    def build_dedupe_key(self, url: str | None, title: str, published_at: str | None, source_name: str | None = None) -> str:
        return build_article_hash(url, source_name, title if published_at is None else f"{title} {published_at}")

    def _get_news_row_by_hash(self, article_hash: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT article_hash, url, status, sent_to_channel FROM news_sent WHERE article_hash = ? LIMIT 1",
                (article_hash,),
            ).fetchone()

    def is_duplicate_news(self, url: str | None, title: str, source_name: str | None = None) -> bool:
        article_hash = build_article_hash(url, source_name, title)
        return self._get_news_row_by_hash(article_hash) is not None

    def should_skip_publication(self, article_hash: str) -> bool:
        row = self._get_news_row_by_hash(article_hash)
        if row is None:
            return False
        return bool(row["sent_to_channel"]) or row["status"] == "posted"

    def get_article_state(self, article_hash: str) -> dict[str, Any] | None:
        row = self._get_news_row_by_hash(article_hash)
        if row is None:
            return None
        return {
            "article_hash": row["article_hash"],
            "url": row["url"],
            "status": row["status"],
            "sent_to_channel": bool(row["sent_to_channel"]),
        }

    def save_sent_news(self, record: NewsArticleRecord, *, sent_at: str | None = None) -> None:
        effective_sent_to_channel = record.sent_to_channel or record.status == "posted"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO news_sent (
                    article_hash, source_name, title, url, published_at, sent_at, topic, status,
                    sent_to_channel, description, content, image, source_url, translated_text, raw_payload,
                    importance_score, importance_level, rewritten_title_kk, summary_kk,
                    betting_impact_kk, team_impact_kk, image_prompt_en, generated_image_url,
                    send_reason, skip_reason, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(article_hash) DO UPDATE SET
                    sent_at = COALESCE(excluded.sent_at, news_sent.sent_at),
                    status = excluded.status,
                    sent_to_channel = CASE
                        WHEN news_sent.sent_to_channel = 1 OR excluded.sent_to_channel = 1 OR excluded.status = 'posted' THEN 1
                        ELSE 0
                    END,
                    translated_text = COALESCE(excluded.translated_text, news_sent.translated_text),
                    importance_score = COALESCE(excluded.importance_score, news_sent.importance_score),
                    importance_level = COALESCE(excluded.importance_level, news_sent.importance_level),
                    rewritten_title_kk = COALESCE(excluded.rewritten_title_kk, news_sent.rewritten_title_kk),
                    summary_kk = COALESCE(excluded.summary_kk, news_sent.summary_kk),
                    betting_impact_kk = COALESCE(excluded.betting_impact_kk, news_sent.betting_impact_kk),
                    team_impact_kk = COALESCE(excluded.team_impact_kk, news_sent.team_impact_kk),
                    image_prompt_en = COALESCE(excluded.image_prompt_en, news_sent.image_prompt_en),
                    generated_image_url = COALESCE(excluded.generated_image_url, news_sent.generated_image_url),
                    send_reason = COALESCE(excluded.send_reason, news_sent.send_reason),
                    skip_reason = COALESCE(excluded.skip_reason, news_sent.skip_reason),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    record.article_hash,
                    record.source_name,
                    record.title,
                    record.url,
                    record.published_at,
                    sent_at,
                    record.topic,
                    record.status,
                    int(effective_sent_to_channel),
                    record.description,
                    record.content,
                    record.image,
                    record.source_url,
                    record.translated_text,
                    record.raw_payload,
                    record.importance_score,
                    record.importance_level,
                    record.rewritten_title_kk,
                    record.summary_kk,
                    record.betting_impact_kk,
                    record.team_impact_kk,
                    record.image_prompt_en,
                    record.generated_image_url,
                    record.send_reason,
                    record.skip_reason,
                ),
            )

    def update_sent_status(
        self,
        article_hash: str,
        status: str,
        *,
        translated_text: str | None = None,
        sent_at: str | None = None,
        importance_score: int | None = None,
        importance_level: str | None = None,
        rewritten_title_kk: str | None = None,
        summary_kk: str | None = None,
        betting_impact_kk: str | None = None,
        team_impact_kk: str | None = None,
        image_prompt_en: str | None = None,
        generated_image_url: str | None = None,
        send_reason: str | None = None,
        skip_reason: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE news_sent
                SET status = ?,
                    sent_to_channel = CASE
                        WHEN ? = 'posted' THEN 1
                        ELSE sent_to_channel
                    END,
                    translated_text = COALESCE(?, translated_text),
                    sent_at = COALESCE(?, sent_at),
                    importance_score = COALESCE(?, importance_score),
                    importance_level = COALESCE(?, importance_level),
                    rewritten_title_kk = COALESCE(?, rewritten_title_kk),
                    summary_kk = COALESCE(?, summary_kk),
                    betting_impact_kk = COALESCE(?, betting_impact_kk),
                    team_impact_kk = COALESCE(?, team_impact_kk),
                    image_prompt_en = COALESCE(?, image_prompt_en),
                    generated_image_url = COALESCE(?, generated_image_url),
                    send_reason = COALESCE(?, send_reason),
                    skip_reason = COALESCE(?, skip_reason),
                    updated_at = CURRENT_TIMESTAMP
                WHERE article_hash = ?
                """,
                (
                    status,
                    status,
                    translated_text,
                    sent_at,
                    importance_score,
                    importance_level,
                    rewritten_title_kk,
                    summary_kk,
                    betting_impact_kk,
                    team_impact_kk,
                    image_prompt_en,
                    generated_image_url,
                    send_reason,
                    skip_reason,
                    article_hash,
                ),
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
            row = connection.execute("SELECT request_count FROM api_usage WHERE usage_date = ?", (usage_date,)).fetchone()
        return int(row[0]) if row else 0

    def get_daily_requests(self, usage_date: str | None = None) -> int:
        usage_date = usage_date or datetime.now(UTC).date().isoformat()
        with self._connect() as connection:
            row = connection.execute("SELECT request_count FROM api_usage WHERE usage_date = ?", (usage_date,)).fetchone()
        return int(row[0]) if row else 0

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO bot_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM bot_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def mark_last_fetch_time(self, value: datetime | None = None) -> None:
        self.set_meta("last_fetch_time", (value or datetime.now(UTC)).isoformat())

    def get_next_topic(self, topics: list[str]) -> str:
        last_topic = self.get_meta("last_topic")
        if last_topic in topics:
            next_index = (topics.index(last_topic) + 1) % len(topics)
        else:
            next_index = 0
        next_topic = topics[next_index]
        self.set_meta("last_topic", next_topic)
        return next_topic

    def get_stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            total_saved = connection.execute("SELECT COUNT(*) FROM news_sent").fetchone()[0]
            total_posted = connection.execute("SELECT COUNT(*) FROM news_sent WHERE status = 'posted'").fetchone()[0]
            total_failed = connection.execute("SELECT COUNT(*) FROM news_sent WHERE status = 'failed'").fetchone()[0]
        return {
            "total_saved_articles": int(total_saved),
            "total_posted_articles": int(total_posted),
            "total_failed_articles": int(total_failed),
            "last_fetch_time": self.get_meta("last_fetch_time"),
            "last_topic": self.get_meta("last_topic"),
            "db_path": str(self.db_path),
        }
