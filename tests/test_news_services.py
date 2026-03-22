import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from services.article_extractor import build_fallback_text, clean_article_text
from services.gnews_service import GNewsService
from services.news_pipeline import NewsPipeline
from services.news_repository import NewsRepository
from services.translator import KazakhTranslator


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


class TranslatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_translate_to_kazakh_returns_original_without_client(self):
        translator = KazakhTranslator(client=None)
        translated = await translator.translate_to_kazakh("Sample text")
        self.assertEqual(translated, "Sample text")


class NewsPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_single_topic_marks_extra_articles_as_queued(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            bot = AsyncMock()
            formatter = AsyncMock()
            formatter.format_post = AsyncMock(return_value=(["message"], "kk"))
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
                    })()
                )
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=gnews_service,
                formatter=formatter,
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


if __name__ == "__main__":
    unittest.main()
