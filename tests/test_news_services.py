from datetime import UTC, datetime
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from services.ai_news_processor import AINewsResult, ensure_ai_result_category, extract_team_or_player_names, infer_news_category
from services.article_extractor import build_fallback_text, clean_article_text
from services.gnews_service import GNewsService
from services.news_pipeline import NewsPipeline
from services.news_ranker import NewsRanker
from services.news_repository import NewsArticleRecord, NewsRepository
from services.telegram_publisher import TelegramPublisher
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

    def test_duplicate_news_detected_by_normalized_title_after_restart(self):
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test.sqlite3"
            repository = NewsRepository(db_path)
            repository.save_sent_news(
                NewsArticleRecord(
                    topic="football",
                    article_hash="title-only-key",
                    url="https://example.com/old-link",
                    title="Breaking: Messi Returns!",
                    description=None,
                    content=None,
                    published_at="2026-03-22T10:00:00Z",
                    image=None,
                    source_name="ESPN",
                    source_url=None,
                    status="posted",
                    sent_to_channel=True,
                ),
                sent_at="2026-03-22T10:05:00Z",
            )

            restarted_repository = NewsRepository(db_path)

            self.assertTrue(
                restarted_repository.is_duplicate_news(
                    "https://example.com/new-link",
                    "Breaking Messi returns",
                    "Sky Sports",
                )
            )

    def test_sent_to_channel_persists_across_repository_restart(self):
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test.sqlite3"
            repository = NewsRepository(db_path)
            repository.save_sent_news(
                NewsArticleRecord(
                    topic="football",
                    article_hash="persisted-key",
                    url="https://example.com/persisted",
                    title="Persisted story",
                    description=None,
                    content=None,
                    published_at="2026-03-22T10:00:00Z",
                    image=None,
                    source_name="ESPN",
                    source_url=None,
                    status="posted",
                    sent_to_channel=True,
                ),
                sent_at="2026-03-22T10:05:00Z",
            )

            restarted_repository = NewsRepository(db_path)
            state = restarted_repository.get_article_state("persisted-key")

            self.assertIsNotNone(state)
            self.assertTrue(state["sent_to_channel"])
            self.assertEqual(state["status"], "posted")
            self.assertTrue(restarted_repository.should_skip_publication("persisted-key"))


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
            repository.increment_daily_requests(datetime.now(UTC).date().isoformat(), 96)
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
                            "title": "Story!!!",
                            "description": "Desc",
                            "content": "Content",
                            "url": "https://example.com/2?utm_source=dup",
                            "publishedAt": "2026-03-22T10:00:00Z",
                            "image": None,
                            "source": {"name": "Sky Sports", "url": "https://skysports.com"},
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
            image_prompt_en="vertical sports poster",
            send_reason="strong impact",
        )

        message = format_ai_news_message("football", result)[0]

        self.assertIn("📰 Маңызды жаңалық", message)
        self.assertNotIn("📊 Беттингке әсері:", message)
        self.assertNotIn("👥 Командаға әсері:", message)


    def test_format_ai_news_message_shows_betting_label_when_present(self):
        result = AINewsResult(
            is_important=True,
            importance_score=90,
            importance_level="top",
            category="tennis",
            rewritten_title_kk="Теннис жаңалығы",
            summary_kk="Қысқаша мазмұн",
            betting_impact_kk="Нарық күтулері өзгеруі мүмкін.",
            image_prompt_en="vertical sports poster",
        )

        message = format_ai_news_message("tennis", result)[0]

        self.assertIn("📊 Беттингке әсері:", message)
        self.assertNotIn("📊 Ставкаға әсері:", message)
        self.assertNotIn("👥 Командаға әсері:", message)

    def test_ranker_requires_score_and_level(self):
        ranker = NewsRanker(min_score=75)
        result = AINewsResult(
            is_important=True,
            importance_score=74,
            importance_level="high",
            category="football",
            image_prompt_en="vertical sports poster",
        )
        self.assertFalse(ranker.should_send(result))

    def test_format_ai_news_message_uses_ai_category_for_emoji(self):
        result = AINewsResult(
            is_important=True,
            importance_score=90,
            importance_level="top",
            category="basketball",
            rewritten_title_kk="NBA жаңалығы",
            summary_kk="Қысқаша мазмұн",
            image_prompt_en="vertical sports poster",
        )

        message = format_ai_news_message("football", result)[0]

        self.assertTrue(message.startswith("🏀 Basketball"))

    def test_infer_news_category_uses_keywords_when_ai_category_missing(self):
        payload = ensure_ai_result_category(
            {
                "is_important": True,
                "importance_score": 88,
                "importance_level": "top",
                "rewritten_title_kk": "NBA жаңалығы",
                "summary_kk": "Қысқаша мазмұн",
                "image_prompt_en": "vertical sports poster",
            },
            topic="",
            title="NBA playoffs update",
            description="Lakers win again",
            article_text="The NBA postseason continues tonight.",
            source_name="ESPN",
            team_or_player_names=["Lakers"],
        )

        self.assertEqual(payload["category"], "basketball")

    def test_infer_news_category_has_no_default_football(self):
        self.assertIsNone(infer_news_category("", "", "Breaking update", "General sports bulletin"))

    def test_extract_team_or_player_names_prefers_real_entities(self):
        names = extract_team_or_player_names(
            "Novak Djokovic beats Carlos Alcaraz in Miami Open thriller",
            "ATP stars Novak Djokovic and Carlos Alcaraz advance",
        )

        self.assertIn("Novak Djokovic", names)
        self.assertIn("Carlos Alcaraz", names)


class TelegramPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_news_post_falls_back_to_send_message_after_send_photo_error(self):
        bot = AsyncMock()
        bot.send_photo.side_effect = RuntimeError("photo rejected")
        bot.send_message = AsyncMock(return_value={"ok": True})
        publisher = TelegramPublisher(bot)

        result = await publisher.publish_news_post(
            chat_id="-1003706297872",
            messages=["hello world"],
            image_url="https://example.com/image.png",
            article_title="Story",
        )

        self.assertEqual(result.status, "posted")
        self.assertEqual(result.chat_id, "-1003706297872")
        bot.send_message.assert_awaited_once_with(
            chat_id="-1003706297872",
            text="hello world",
            disable_web_page_preview=True,
        )

    async def test_verify_channel_access_reports_missing_admin_rights(self):
        bot = AsyncMock()
        bot.get_chat = AsyncMock(return_value=type("Chat", (), {"title": "News", "type": "channel"})())
        bot.get_me = AsyncMock(return_value=type("Me", (), {"id": 777})())
        bot.get_chat_member = AsyncMock(return_value=type("Member", (), {"status": "member", "can_post_messages": None})())
        publisher = TelegramPublisher(bot)

        ok, message = await publisher.verify_channel_access("-1003706297872")

        self.assertFalse(ok)
        self.assertIn("bot must be administrator", message)


class NewsPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_single_topic_marks_extra_articles_as_queued(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            bot = AsyncMock()
            formatter = AsyncMock()
            formatter.format_post = AsyncMock(return_value=(["message"], "kk"))
            ai_processor = AsyncMock()
            fal_image_service = AsyncMock()
            fal_image_service.generate_news_image = AsyncMock(return_value="https://img.test/generated.png")
            ai_processor.process_news_with_ai = AsyncMock(
                return_value=AINewsResult(
                    is_important=True,
                    importance_score=88,
                    importance_level="top",
                    category="football",
                    rewritten_title_kk="T",
                    summary_kk="S",
                    image_prompt_en="vertical sports poster",
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
                    NewsArticleRecord(
                        topic="football",
                        article_hash=f"key-{i}",
                        url=f"https://e/{i}",
                        title=f"T{i}",
                        description=None,
                        content=None,
                        published_at=None,
                        image=None,
                        source_name="ESPN",
                        source_url=None,
                    )
                )
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=gnews_service,
                formatter=formatter,
                ai_processor=ai_processor,
                ranker=NewsRanker(min_score=75),
                telegram_publisher=TelegramPublisher(bot),
                fal_image_service=fal_image_service,
                admin_id=1,
                news_channel_id=None,
                news_post_mode="admin",
            )
            pipeline.extract_enabled = False

            summary = await pipeline.run_single_topic_cycle("football", trigger="test")

            self.assertIn("posted=3", summary)
            self.assertIn("queued=1", summary)
            self.assertEqual(bot.send_photo.await_count, 3)

    async def test_build_ai_result_extracts_team_or_player_names_for_prompt(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            bot = AsyncMock()
            formatter = AsyncMock()
            ai_processor = AsyncMock()
            ai_processor.process_news_with_ai = AsyncMock(return_value=None)
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=AsyncMock(),
                formatter=formatter,
                ai_processor=ai_processor,
                ranker=NewsRanker(min_score=75),
                telegram_publisher=TelegramPublisher(bot),
                fal_image_service=AsyncMock(),
                admin_id=1,
                news_channel_id=None,
                news_post_mode="admin",
            )

            await pipeline._build_ai_result(
                {
                    "topic": "football",
                    "title": "Lionel Messi returns for Inter Miami",
                    "description": "The Argentine star is back in training ahead of the match.",
                    "content": "Inter Miami expect Lionel Messi to start after recovery.",
                    "final_text": "Lionel Messi could return to the lineup for Inter Miami this weekend.",
                    "source_name": "ESPN",
                    "published_at": "2026-03-23T10:00:00Z",
                    "url": "https://example.com/story",
                }
            )

            call = ai_processor.process_news_with_ai.await_args
            self.assertIn("Lionel Messi", call.kwargs["team_or_player_names"])
            self.assertIn("Inter Miami", call.kwargs["team_or_player_names"])

    async def test_publish_skips_article_already_marked_as_posted_in_db(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            bot = AsyncMock()
            formatter = AsyncMock()
            formatter.format_post = AsyncMock(return_value=(["message"], "kk"))
            ai_processor = AsyncMock()
            ai_processor.process_news_with_ai = AsyncMock(
                return_value=AINewsResult(
                    is_important=True,
                    importance_score=90,
                    importance_level="top",
                    category="football",
                    rewritten_title_kk="T",
                    summary_kk="S",
                    image_prompt_en="vertical sports poster",
                )
            )
            fal_image_service = AsyncMock()
            gnews_service = AsyncMock()
            repository.save_sent_news(
                NewsArticleRecord(
                    topic="football",
                    article_hash="key-posted",
                    url="https://e/posted",
                    title="Posted",
                    description=None,
                    content=None,
                    published_at=None,
                    image=None,
                    source_name="ESPN",
                    source_url=None,
                    status="posted",
                    sent_to_channel=True,
                )
            )
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=gnews_service,
                formatter=formatter,
                ai_processor=ai_processor,
                ranker=NewsRanker(min_score=75),
                telegram_publisher=TelegramPublisher(bot),
                fal_image_service=fal_image_service,
                admin_id=1,
                news_channel_id=None,
                news_post_mode="admin",
            )
            pipeline.extract_enabled = False

            status = await pipeline._publish_article(
                {"article_hash": "key-posted", "topic": "football", "title": "Posted", "source_name": "ESPN", "url": "https://e/posted"}
            )

            self.assertEqual(status, "skipped")
            formatter.format_post.assert_not_called()
            ai_processor.process_news_with_ai.assert_not_called()
            bot.send_message.assert_not_called()
            bot.send_photo.assert_not_called()

    async def test_run_single_topic_skips_low_value_news(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            bot = AsyncMock()
            formatter = AsyncMock()
            ai_processor = AsyncMock()
            fal_image_service = AsyncMock()
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
                NewsArticleRecord(
                    topic="football",
                    article_hash="key-1",
                    url="https://e/1",
                    title="T1",
                    description=None,
                    content=None,
                    published_at=None,
                    image=None,
                    source_name="ESPN",
                    source_url=None,
                )
            )
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=gnews_service,
                formatter=formatter,
                ai_processor=ai_processor,
                ranker=NewsRanker(min_score=75),
                telegram_publisher=TelegramPublisher(bot),
                fal_image_service=fal_image_service,
                admin_id=1,
                news_channel_id=None,
                news_post_mode="admin",
            )
            pipeline.extract_enabled = False

            summary = await pipeline.run_single_topic_cycle("football", trigger="test")

            self.assertIn("skipped=1", summary)
            self.assertEqual(bot.send_message.await_count, 0)
            formatter.format_post.assert_not_called()

    async def test_publish_article_uses_channel_chat_id_in_channel_mode(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            repository.save_sent_news(
                NewsArticleRecord(
                    topic="football",
                    article_hash="key-channel",
                    url="https://e/channel",
                    title="T1",
                    description=None,
                    content=None,
                    published_at=None,
                    image=None,
                    source_name="ESPN",
                    source_url=None,
                )
            )
            bot = AsyncMock()
            formatter = AsyncMock()
            formatter.format_post = AsyncMock(return_value=(["message"], "kk"))
            ai_processor = AsyncMock()
            ai_processor.process_news_with_ai = AsyncMock(
                return_value=AINewsResult(
                    is_important=True,
                    importance_score=95,
                    importance_level="top",
                    category="football",
                    rewritten_title_kk="T",
                    summary_kk="S",
                )
            )
            fal_image_service = AsyncMock()
            publisher = TelegramPublisher(bot)
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=AsyncMock(),
                formatter=formatter,
                ai_processor=ai_processor,
                ranker=NewsRanker(min_score=75),
                telegram_publisher=publisher,
                fal_image_service=fal_image_service,
                admin_id=12345,
                news_channel_id="-1003706297872",
                news_post_mode="channel",
            )
            pipeline.extract_enabled = False

            status = await pipeline._publish_article(
                {"article_hash": "key-channel", "topic": "football", "title": "Channel story", "source_name": "ESPN", "url": "https://e/channel"}
            )

            self.assertEqual(status, "posted")
            bot.send_message.assert_awaited_once_with(
                chat_id="-1003706297872",
                text="message",
                disable_web_page_preview=True,
            )


if __name__ == "__main__":
    unittest.main()
