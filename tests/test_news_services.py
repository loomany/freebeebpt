import os
from datetime import UTC, datetime
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from services.ai_news_processor import (
    AINewsResult,
    build_image_prompt_fallback,
    ensure_ai_result_category,
    extract_team_or_player_names,
    infer_news_category,
)
from services.article_extractor import build_fallback_text, clean_article_text
from services.dedup import build_article_hash
from services.fal_image_service import FalGenerationResult, FalImageService
from services.gnews_service import GNewsService
from services.news_pipeline import NewsPipeline
from services.news_ranker import NewsRanker
from services.news_repository import NewsArticleRecord, NewsRepository
from services.telegram_publisher import TelegramPublisher
from services.telegram_formatter import format_ai_news_message


class FalImageServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_normalize_status_reads_status_from_dict_payload(self):
        service = FalImageService()

        self.assertEqual(service._normalize_status({"status": "COMPLETED"}), "COMPLETED")

    def test_extract_image_url_reads_nested_data_images_payload(self):
        service = FalImageService()

        self.assertEqual(
            service._extract_image_url({"data": {"images": [{"url": "https://img.test/nested.png"}]}}),
            "https://img.test/nested.png",
        )

    async def test_generate_news_image_completes_and_fetches_result_after_completed_status(self):
        with patch.dict(os.environ, {"FAL_KEY": "test-key", "FAL_TIMEOUT_SECONDS": "6", "FAL_POLL_INTERVAL_SECONDS": "0.01"}, clear=False):
            service = FalImageService()
            service.max_retries = 0
            service.max_poll_attempts = 3
            service._submit = AsyncMock(return_value=(object(), "req-123"))
            service._poll_status = AsyncMock(side_effect=[{"status": "IN_PROGRESS"}, {"status": "COMPLETED"}])
            service._get_result = AsyncMock(return_value={"data": {"images": [{"url": "https://img.test/generated.png"}]}})

            result = await service.generate_news_image("poster prompt")

            self.assertEqual(result.status, "success")
            self.assertEqual(result.request_id, "req-123")
            self.assertEqual(result.image_url, "https://img.test/generated.png")
            self.assertEqual(service._poll_status.await_count, 2)
            service._get_result.assert_awaited_once()

    async def test_generate_news_image_fails_when_completed_result_has_no_image_url(self):
        with patch.dict(os.environ, {"FAL_KEY": "test-key", "FAL_TIMEOUT_SECONDS": "6", "FAL_POLL_INTERVAL_SECONDS": "0.01"}, clear=False):
            service = FalImageService()
            service.max_retries = 0
            service.max_poll_attempts = 3
            service._submit = AsyncMock(return_value=(object(), "req-empty"))
            service._poll_status = AsyncMock(return_value={"status": "COMPLETED"})
            service._get_result = AsyncMock(return_value={"images": [{}]})

            result = await service.generate_news_image("poster prompt")

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.request_id, "req-empty")
            self.assertEqual(result.error, "response does not contain data.images[0].url")
            self.assertEqual(service._poll_status.await_count, 1)
            service._get_result.assert_awaited_once()

    async def test_generate_news_image_returns_timeout_after_max_poll_attempts(self):
        with patch.dict(os.environ, {"FAL_KEY": "test-key", "FAL_TIMEOUT_SECONDS": "3", "FAL_POLL_INTERVAL_SECONDS": "0.01"}, clear=False):
            service = FalImageService()
            service.max_retries = 0
            service.max_poll_attempts = 2
            service._submit = AsyncMock(return_value=(object(), "req-timeout"))
            service._poll_status = AsyncMock(return_value={"status": "IN_PROGRESS"})
            service._get_result = AsyncMock(side_effect=RuntimeError("not ready"))

            result = await service.generate_news_image("poster prompt")

            self.assertEqual(result.status, "timeout")
            self.assertEqual(result.request_id, "req-timeout")
            self.assertIn("timed out", result.error)
            self.assertEqual(service._poll_status.await_count, 2)
            service._get_result.assert_awaited_once()

    async def test_generate_news_image_recovers_completed_result_after_poll_timeout(self):
        with patch.dict(os.environ, {"FAL_KEY": "test-key", "FAL_TIMEOUT_SECONDS": "3", "FAL_POLL_INTERVAL_SECONDS": "0.01"}, clear=False):
            service = FalImageService()
            service.max_retries = 0
            service.max_poll_attempts = 2
            service._submit = AsyncMock(return_value=(object(), "req-late-result"))
            service._poll_status = AsyncMock(return_value={"status": "IN_PROGRESS"})
            service._get_result = AsyncMock(return_value={"images": [{"url": "https://img.test/late.png"}]})

            result = await service.generate_news_image("poster prompt")

            self.assertEqual(result.status, "success")
            self.assertEqual(result.request_id, "req-late-result")
            self.assertEqual(result.image_url, "https://img.test/late.png")
            self.assertEqual(service._poll_status.await_count, 2)
            service._get_result.assert_awaited_once()


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

    def test_article_hash_ignores_tracking_query_params(self):
        first = build_article_hash(
            "https://example.com/story?utm_source=telegram&id=7",
            "ESPN",
            "Story",
        )
        second = build_article_hash(
            "https://example.com/story?id=7&utm_medium=social",
            "ESPN",
            "Story",
        )
        self.assertEqual(first, second)

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

    def test_duplicate_news_detected_for_similar_titles_from_different_sources(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            repository.save_sent_news(
                NewsArticleRecord(
                    topic="tennis",
                    article_hash="korda-one",
                    url="https://example.com/korda-1",
                    title="Sebastian Korda stuns Carlos Alcaraz in Miami Open upset",
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

            self.assertTrue(
                repository.is_duplicate_news(
                    "https://example.com/korda-2",
                    "Korda shocks Alcaraz in Miami Open third round",
                    "Sky Sports",
                )
            )

    def test_debug_payload_includes_fal_request_id_and_admin_message(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            repository.save_sent_news(
                NewsArticleRecord(
                    topic="football",
                    article_hash="fal-key",
                    url="https://example.com/fal",
                    title="Fal story",
                    description=None,
                    content=None,
                    published_at=None,
                    image=None,
                    source_name="ESPN",
                    source_url=None,
                )
            )
            repository.update_sent_status(
                "fal-key",
                "review_pending",
                fal_request_id="req-42",
                fal_status="success",
                generated_image_url="https://img.test/generated.png",
                sent_to_admin=True,
                admin_message_id=555,
                importance_score=83,
            )

            debug = repository.get_last_article_debug()

            self.assertEqual(debug["fal_request_id"], "req-42")
            self.assertEqual(debug["admin_message_id"], 555)
            self.assertEqual(debug["generated_image_url"], "https://img.test/generated.png")

    def test_callback_article_ref_resolves_back_to_article_hash(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            repository.save_sent_news(
                NewsArticleRecord(
                    topic="football",
                    article_hash="key-callback-ref",
                    url="https://example.com/callback",
                    title="Callback story",
                    description=None,
                    content=None,
                    published_at=None,
                    image=None,
                    source_name="ESPN",
                    source_url=None,
                )
            )

            article_ref = repository.get_callback_article_ref("key-callback-ref")

            self.assertIsNotNone(article_ref)
            self.assertTrue(article_ref.isdigit())
            self.assertEqual(repository.resolve_callback_article_hash(article_ref), "key-callback-ref")
            self.assertIsNone(repository.resolve_callback_article_hash("bad-ref"))

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

    async def test_fetch_topic_news_skips_batch_duplicates_before_repository_check(self):
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
                            "url": "https://example.com/story?id=1&utm_source=gnews",
                            "publishedAt": "2026-03-22T10:00:00Z",
                            "image": None,
                            "source": {"name": "ESPN", "url": "https://espn.com"},
                        },
                        {
                            "title": "Story",
                            "description": "Desc",
                            "content": "Content",
                            "url": "https://example.com/story?id=1&utm_medium=social",
                            "publishedAt": "2026-03-22T10:00:10Z",
                            "image": None,
                            "source": {"name": "ESPN", "url": "https://espn.com"},
                        },
                    ]
                }
            )

            result = await service.fetch_topic_news("football")

            self.assertEqual(result.fetched_articles, 2)
            self.assertEqual(len(result.new_articles), 1)
            self.assertEqual(repository.get_stats()["total_saved_articles"], 1)


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

    def test_ranker_allows_admin_preview_by_score_only(self):
        ranker = NewsRanker(min_score=75, admin_preview_min_score=10)
        result = AINewsResult(
            is_important=False,
            importance_score=28,
            importance_level="low",
            category="football",
            image_prompt_en="vertical sports poster",
            skip_reason="low practical value",
        )
        self.assertTrue(ranker.passed_for_admin_preview(result))
        self.assertFalse(ranker.passed_for_auto_publish(result))

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

    def test_ensure_ai_result_category_builds_person_prompt_when_ai_prompt_empty(self):
        payload = ensure_ai_result_category(
            {
                "is_important": True,
                "importance_score": 90,
                "importance_level": "top",
                "rewritten_title_kk": "Месси оралды",
                "summary_kk": "Қысқаша мазмұн",
                "image_prompt_en": "",
            },
            topic="football",
            title="Lionel Messi returns for Inter Miami",
            description="The forward is back for a key MLS match.",
            article_text="Lionel Messi could return to the lineup for Inter Miami this weekend.",
            source_name="ESPN",
            team_or_player_names=["Lionel Messi", "Inter Miami"],
        )

        self.assertIn("Lionel Messi", payload["image_prompt_en"])

    def test_build_image_prompt_fallback_uses_team_mode_without_person_name(self):
        prompt, mode = build_image_prompt_fallback(
            title="Inter Miami vs LA Galaxy showdown moved to prime time",
            description="A major MLS clash now gets a bigger stage.",
            article_text="The match between Inter Miami and LA Galaxy will headline the weekend schedule.",
            category="football",
            team_or_player_names=["Inter Miami", "LA Galaxy"],
        )

        self.assertEqual(mode, "team")
        self.assertIn("Inter Miami vs LA Galaxy", prompt)

    def test_build_image_prompt_fallback_uses_generic_mode_when_entities_missing(self):
        prompt, mode = build_image_prompt_fallback(
            title="Late injury update changes the outlook before a crucial playoff game",
            description="Coaches are adjusting plans ahead of tipoff.",
            article_text="A major basketball injury report has shifted expectations before tonight's playoff game.",
            category="basketball",
            team_or_player_names=[],
        )

        self.assertEqual(mode, "generic")
        self.assertTrue(prompt)
        self.assertIn("basketball editorial news scene", prompt)

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
    async def test_publish_news_post_fails_when_send_photo_errors_and_image_is_required(self):
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

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.chat_id, "-1003706297872")
        bot.send_message.assert_not_awaited()

    async def test_publish_news_post_fails_when_image_missing_and_image_is_required(self):
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value={"ok": True})
        publisher = TelegramPublisher(bot)

        result = await publisher.publish_news_post(
            chat_id="-1003706297872",
            messages=["hello world"],
            image_url=None,
            article_title="Story",
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "image_url is required for news post")
        bot.send_photo.assert_not_called()
        bot.send_message.assert_not_awaited()

    async def test_publish_news_post_can_fall_back_to_send_message_when_image_is_optional(self):
        bot = AsyncMock()
        bot.send_photo.side_effect = RuntimeError("photo rejected")
        bot.send_message = AsyncMock(return_value={"ok": True})
        with patch.dict(os.environ, {"REQUIRE_IMAGE_FOR_NEWS_POST": "false", "SEND_TEXT_IF_IMAGE_FAIL": "true"}):
            publisher = TelegramPublisher(bot)

            result = await publisher.publish_news_post(
                chat_id="-1003706297872",
                messages=["hello world"],
                image_url="https://example.com/image.png",
                article_title="Story",
            )

        self.assertEqual(result.status, "posted")
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

    async def test_process_manual_news_forces_preview_and_saves_source_type(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            bot = AsyncMock()
            formatter = AsyncMock()
            formatter.format_post = AsyncMock(return_value=(["message"], "kk"))
            ai_processor = AsyncMock()
            ai_processor.process_news_with_ai = AsyncMock(
                return_value=AINewsResult(
                    is_important=False,
                    importance_score=5,
                    importance_level="low",
                    category="basketball",
                    rewritten_title_kk="Қолмен енгізілген жаңалық",
                    summary_kk="Қысқа мазмұн",
                    key_points_kk=["Бірінші", "Екінші"],
                    image_prompt_en="",
                    skip_reason="not important, but manual",
                )
            )
            fal_image_service = AsyncMock()
            fal_image_service.generate_news_image = AsyncMock(
                return_value=FalGenerationResult(request_id="req-manual", status="completed", image_url="https://img/manual.png")
            )
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=AsyncMock(),
                formatter=formatter,
                ai_processor=ai_processor,
                ranker=NewsRanker(min_score=75, admin_preview_min_score=75),
                telegram_publisher=TelegramPublisher(bot),
                fal_image_service=fal_image_service,
                admin_id=1,
                news_channel_id="-100123",
                news_post_mode="admin",
            )
            pipeline.extract_enabled = False

            status = await pipeline.process_manual_news("Luka Doncic avoided suspension after a technical was rescinded.")

            self.assertEqual(status, "review_pending")
            fal_image_service.generate_news_image.assert_awaited_once()
            fallback_prompt = fal_image_service.generate_news_image.await_args.args[0]
            self.assertIn("vertical sports poster", fallback_prompt)
            state = repository.get_last_article_debug()
            self.assertEqual(state["status"], "review_pending")
            self.assertEqual(state["source_type"], "manual")
            payload = repository.get_article_delivery_payload(state["article_hash"])
            self.assertEqual(payload["source_type"], "manual")
            self.assertTrue(payload["sent_to_admin"])
            self.assertEqual(payload["generated_image_url"], "https://img/manual.png")

    async def test_process_manual_news_sends_text_preview_when_fal_fails(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            bot = AsyncMock()
            formatter = AsyncMock()
            formatter.format_post = AsyncMock(return_value=(["message"], "kk"))
            ai_processor = AsyncMock()
            ai_processor.process_news_with_ai = AsyncMock(
                return_value=AINewsResult(
                    is_important=False,
                    importance_score=1,
                    importance_level="low",
                    category="football",
                    rewritten_title_kk="T",
                    summary_kk="S",
                    image_prompt_en="poster",
                )
            )
            fal_image_service = AsyncMock()
            fal_image_service.generate_news_image = AsyncMock(
                return_value=FalGenerationResult(request_id="req-fail", status="failed", error="boom")
            )
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=AsyncMock(),
                formatter=formatter,
                ai_processor=ai_processor,
                ranker=NewsRanker(min_score=75),
                telegram_publisher=TelegramPublisher(bot),
                fal_image_service=fal_image_service,
                admin_id=1,
                news_channel_id="-100123",
                news_post_mode="admin",
            )
            pipeline.extract_enabled = False

            status = await pipeline.process_manual_news("Manual football update")

            self.assertEqual(status, "review_pending")
            bot.send_message.assert_awaited()
            payload = repository.get_article_delivery_payload(repository.get_last_article_debug()["article_hash"])
            self.assertIsNone(payload["generated_image_url"])
            self.assertEqual(payload["fal_status"], "failed")
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

    async def test_publish_article_marks_timeout_and_notifies_admin_when_fal_never_completes(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            repository.save_sent_news(
                NewsArticleRecord(
                    topic="football",
                    article_hash="key-timeout",
                    url="https://e/timeout",
                    title="Timeout story",
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
                    importance_score=83,
                    importance_level="high",
                    category="football",
                    rewritten_title_kk="T",
                    summary_kk="S",
                    image_prompt_en="vertical sports poster",
                )
            )
            fal_image_service = AsyncMock()
            fal_image_service.generate_news_image = AsyncMock(
                return_value=FalGenerationResult(request_id="req-timeout", status="timeout", error="timed out after 120s")
            )
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=AsyncMock(),
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
                {"article_hash": "key-timeout", "topic": "football", "title": "Timeout story", "source_name": "ESPN", "url": "https://e/timeout"}
            )

            self.assertEqual(status, "image_failed")
            bot.send_message.assert_awaited_once()
            bot.send_photo.assert_not_called()
            debug = repository.get_last_article_debug()
            self.assertEqual(debug["fal_request_id"], "req-timeout")
            self.assertEqual(debug["fal_status"], "timeout")
            self.assertEqual(debug["sent_to_admin"], 0)

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
                    image_prompt_en="vertical sports poster",
                )
            )
            fal_image_service = AsyncMock()
            fal_image_service.generate_news_image = AsyncMock(return_value="https://img.test/generated.png")
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
            bot.send_photo.assert_awaited_once_with(
                chat_id="-1003706297872",
                photo="https://img.test/generated.png",
                caption="message",
            )

    async def test_admin_preview_ignores_is_important_when_score_meets_preview_threshold(self):
        with TemporaryDirectory() as tmp_dir:
            repository = NewsRepository(Path(tmp_dir) / "test.sqlite3")
            repository.save_sent_news(
                NewsArticleRecord(
                    topic="football",
                    article_hash="key-admin-preview",
                    url="https://e/admin-preview",
                    title="Admin preview story",
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
                    is_important=False,
                    importance_score=28,
                    importance_level="low",
                    category="football",
                    rewritten_title_kk="T",
                    summary_kk="S",
                    image_prompt_en="vertical sports poster",
                    skip_reason="low practical value",
                )
            )
            fal_image_service = AsyncMock()
            fal_image_service.generate_news_image = AsyncMock(
                return_value=FalGenerationResult(request_id="req-preview", status="completed", image_url="https://img/preview.png")
            )
            pipeline = NewsPipeline(
                bot=bot,
                repository=repository,
                gnews_service=AsyncMock(),
                formatter=formatter,
                ai_processor=ai_processor,
                ranker=NewsRanker(min_score=75, admin_preview_min_score=10),
                telegram_publisher=TelegramPublisher(bot),
                fal_image_service=fal_image_service,
                admin_id=1,
                news_channel_id=None,
                news_post_mode="admin",
            )
            pipeline.extract_enabled = False

            status = await pipeline._publish_article(
                {"article_hash": "key-admin-preview", "topic": "football", "title": "Admin preview story", "source_name": "ESPN", "url": "https://e/admin-preview"}
            )

            self.assertEqual(status, "review_pending")
            formatter.format_post.assert_awaited_once()
            fal_image_service.generate_news_image.assert_awaited_once_with("vertical sports poster")
            bot.send_photo.assert_awaited_once()
            state = repository.get_article_delivery_payload("key-admin-preview")
            self.assertEqual(state["status"], "review_pending")
            self.assertEqual(state["importance_score"], 28)
            self.assertEqual(state["skip_reason"], "low practical value")
            self.assertTrue(state["sent_to_admin"])
            self.assertFalse(state["published_to_channel"])
            reply_markup = bot.send_photo.await_args.kwargs["reply_markup"]
            callback_data = [button.callback_data for row in reply_markup.inline_keyboard for button in row]
            self.assertEqual(callback_data, ["send_news:1", "skip_news:1"])
            self.assertTrue(all(len(value.encode("utf-8")) <= 64 for value in callback_data))


if __name__ == "__main__":
    unittest.main()
