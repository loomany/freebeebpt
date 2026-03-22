import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from services.ai_news_processor import AINewsResult
from services.article_extractor import build_fallback_text, clean_article_text
from services.gnews_service import GNewsService
from services.news_pipeline import NewsPipeline
from services.news_ranker import NewsRanker
from services.news_repository import NewsRepository
from services.telegram_formatter import format_ai_news_message


class NewsRepositoryTests(unittest.TestCase):
    def test_dedupe_key_falls_back_to_hash_without_url(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            key = repository.build_dedupe_key(None, "Title", "2026-03-22T00:00:00Z")
            self.assertEqual(len(key), 64)

    def test_daily_request_counter_is_stored(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            self.assertEqual(repository.get_daily_requests("2026-03-22"), 0)
            self.assertEqual(repository.increment_daily_requests("2026-03-22"), 1)
            self.assertEqual(repository.increment_daily_requests("2026-03-22", 2), 3)


class ArticleHelpersTests(unittest.TestCase):
    def test_clean_article_text_removes_noise_and_tail(self):
        cleaned = clean_article_text("Hello\n\nAdvertisement\nWorld [+123 chars]")
        self.assertEqual(cleaned, "Hello\n\nWorld")

    def test_build_fallback_text_prefers_clean_content(self):
        article = {"title": "Title", "description": "Desc", "content": "Content [+99 chars]"}
        self.assertEqual(build_fallback_text(article, None), "Content")


class GNewsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_topic_news_stops_at_limit(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            repository.increment_daily_requests("2026-03-22", 96)
            service = GNewsService(repository=repository, api_key="test")

            result = await service.fetch_topic_news("football")

            self.assertTrue(result.skipped_due_to_limit)
            self.assertEqual(result.fetched_articles, 0)

    async def test_fetch_topic_news_saves_only_new_articles(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            service = GNewsService(repository=repository, api_key="test")
            service.fetcher._request_json = AsyncMock(
                return_value={
                    "articles": [
                        {
                            "title": "Story",
                            "description": "Desc",
                            "content": "Content",
                            "url": "https://example.com/1",
                            "publishedAt": "2026-03-22T10:00:00Z",
                            "image": None,
                            "source": {"name": "ESPN", "url": "https://espn.com"},
                        },
                        {
                            "title": "Story",
                            "description": "Desc",
                            "content": "Content",
                            "url": "https://example.com/1",
                            "publishedAt": "2026-03-22T10:00:00Z",
                            "image": None,
                            "source": {"name": "ESPN", "url": "https://espn.com"},
                        },
                    ]
                }
            )

            result = await service.fetch_topic_news("football")

            self.assertEqual(result.fetched_articles, 2)
            self.assertEqual(len(result.new_articles), 1)


class FormatterAndRankerTests(unittest.TestCase):
    def test_format_ai_news_message_hides_empty_impact_sections(self):
        result = AINewsResult(
            is_important=True,
            importance_score=90,
            importance_level="top",
            category="football",
            rewritten_title_kk="Маңызды жаңалық",
            summary_kk="Қысқаша мазмұн",
            key_points_kk=["Бір", "Екі"],
            send_reason="strong impact",
        )

        message = format_ai_news_message("football", result)[0]

        self.assertIn("📰 Маңызды жаңалық", message)
        self.assertNotIn("📊 Ставкаға әсері:", message)
        self.assertNotIn("👥 Командаға әсері:", message)

    def test_ranker_requires_score_and_level(self):
        ranker = NewsRanker(min_score=75)
        result = AINewsResult(
            is_important=True,
            importance_score=74,
            importance_level="high",
            category="football",
        )
        self.assertFalse(ranker.should_send(result))


class NewsPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_single_topic_marks_extra_articles_as_queued(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            bot = AsyncMock()
            formatter = AsyncMock()
            formatter.format_post = AsyncMock(return_value=(["message"], "kk"))
            ai_processor = AsyncMock()
            ai_processor.process_news_with_ai = AsyncMock(
                return_value=AINewsResult(
                    is_important=True,
                    importance_score=88,
                    importance_level="top",
                    category="football",
                    rewritten_title_kk="T",
                    summary_kk="S",
                )
            )
            gnews_service = AsyncMock()
            gnews_service.fetch_topic_news = AsyncMock(
                return_value=type(
                    "Result",
                    (),
                    {
                        "topic": "football",
                        "fetched_articles": 4,
                        "new_articles": [
                            {"article_hash": f"key-{i}", "topic": "football", "title": f"T{i}", "source_name": "ESPN", "url": f"https://e/{i}"}
                            for i in range(4)
                        ],
                        "request_count_today": 1,
                    },
                )()
            )
            for i in range(4):
                repository.save_sent_news(
                    type("Record", (), {
                        "topic": "football", "article_hash": f"key-{i}", "url": f"https://e/{i}", "title": f"T{i}", "description": None,
                        "content": None, "published_at": None, "image": None, "source_name": "ESPN",
                        "source_url": None, "status": "new", "translated_text": None, "raw_payload": None,
                        "importance_score": None, "importance_level": None, "rewritten_title_kk": None,
                        "summary_kk": None, "betting_impact_kk": None, "team_impact_kk": None,
                    })()
                )
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=gnews_service,
                formatter=formatter,
                ai_processor=ai_processor,
                ranker=NewsRanker(min_score=75),
                admin_id=1,
                news_channel_id=None,
                news_post_mode="admin",
            )
            pipeline.extract_enabled = False
            pipeline.send_photo_enabled = False

            summary = await pipeline.run_single_topic_cycle("football", trigger="test")

            self.assertIn("posted=3", summary)
            self.assertIn("queued=1", summary)
            self.assertEqual(bot.send_message.await_count, 3)

    async def test_run_single_topic_skips_low_value_news(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            bot = AsyncMock()
            formatter = AsyncMock()
            ai_processor = AsyncMock()
            ai_processor.process_news_with_ai = AsyncMock(
                return_value=AINewsResult(
                    is_important=False,
                    importance_score=20,
                    importance_level="low",
                    category="football",
                    skip_reason="low practical value",
                )
            )
            gnews_service = AsyncMock()
            gnews_service.fetch_topic_news = AsyncMock(
                return_value=type(
                    "Result",
                    (),
                    {
                        "topic": "football",
                        "fetched_articles": 1,
                        "new_articles": [{"article_hash": "key-1", "topic": "football", "title": "T1", "source_name": "ESPN", "url": "https://e/1"}],
                        "request_count_today": 1,
                    },
                )()
            )
            repository.save_sent_news(
                type("Record", (), {
                    "topic": "football", "article_hash": "key-1", "url": "https://e/1", "title": "T1", "description": None,
                    "content": None, "published_at": None, "image": None, "source_name": "ESPN",
                    "source_url": None, "status": "new", "translated_text": None, "raw_payload": None,
                    "importance_score": None, "importance_level": None, "rewritten_title_kk": None,
                    "summary_kk": None, "betting_impact_kk": None, "team_impact_kk": None,
                })()
            )
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=gnews_service,
                formatter=formatter,
                ai_processor=ai_processor,
                ranker=NewsRanker(min_score=75),
                admin_id=1,
                news_channel_id=None,
                news_post_mode="admin",
            )
            pipeline.extract_enabled = False
            pipeline.send_photo_enabled = False

            summary = await pipeline.run_single_topic_cycle("football", trigger="test")

            self.assertIn("skipped=1", summary)
            self.assertEqual(bot.send_message.await_count, 0)
            formatter.format_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
