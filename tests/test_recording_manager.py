import asyncio
import inspect
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.recording.record_manager import GlobalRecordingState, RecordingManager
from app.models.recording.recording_model import Recording


class DummyLanguageManager:
    def __init__(self):
        self.language = {
            "recording_manager": {
                "live_room": "Live Room",
                "is_live": "LIVE",
                "monitor_stopped": "Monitor Stopped",
                "notify": "Notify",
                "live_recording_started_message": "started",
                "push_content": "push",
                "status_notify": "status",
            },
            "video_quality": {"OD": "Original"},
        }

    def add_observer(self, _observer):
        return None


class DummyConfigManager:
    def __init__(self):
        self.saved_configs = []

    def load_recordings_config(self):
        return []

    async def save_recordings_config(self, config):
        self.saved_configs.append(config)


class DummyPage:
    def __init__(self):
        self.pubsub = SimpleNamespace(send_others_on_topic=lambda *_args, **_kwargs: None)
        self.scheduled_tasks = []

    def run_task(self, *_args, **_kwargs):
        func = _args[0]
        args = _args[1:]
        result = func(*args, **_kwargs)
        if inspect.isawaitable(result):
            task = asyncio.create_task(result)
            self.scheduled_tasks.append(task)
            return task
        return result


class DummyRecordCardManager:
    async def update_card(self, _recording):
        return None


class DummySnackBar:
    async def show_snack_bar(self, *_args, **_kwargs):
        return None


class RecordingManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        GlobalRecordingState.recordings = []
        self.config_manager = DummyConfigManager()
        self.page = DummyPage()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings = SimpleNamespace(
            user_config={
                "platform_max_concurrent_requests": 3,
                "loop_time_seconds": "180",
                "language": "zh_CN",
                "recording_space_threshold": 0,
                "remove_emojis": False,
            },
        )
        self.settings.get_video_save_path = lambda: self.temp_dir.name
        self.app = SimpleNamespace(
            settings=self.settings,
            language_manager=DummyLanguageManager(),
            config_manager=self.config_manager,
            page=self.page,
            record_card_manager=DummyRecordCardManager(),
            snack_bar=DummySnackBar(),
            recording_enabled=True,
            proxy_manager=SimpleNamespace(
                is_subscription_active=lambda: False,
                get_status_check_proxy=lambda: None,
                mask_proxy_value=lambda value: value,
            ),
        )

    def tearDown(self):
        GlobalRecordingState.recordings = []
        self.temp_dir.cleanup()

    @staticmethod
    def _make_recording(rec_id: str, url: str) -> Recording:
        return Recording(
            rec_id=rec_id,
            url=url,
            streamer_name=f"streamer-{rec_id}",
            record_format="MP4",
            quality="OD",
            segment_record=True,
            segment_time="1800",
            monitor_status=True,
            scheduled_recording=False,
            scheduled_start_time="",
            monitor_hours="5",
            recording_dir="",
            enabled_message_push=False,
            only_notify_no_record=False,
            flv_use_direct_download=False,
        )

    async def test_add_recordings_persists_once_for_multiple_items(self):
        manager = RecordingManager(self.app)
        recordings = [
            self._make_recording("rec-1", "https://example.com/1"),
            self._make_recording("rec-2", "https://example.com/2"),
        ]

        await manager.add_recordings(recordings)

        self.assertEqual(len(manager.recordings), 2)
        self.assertEqual(len(self.config_manager.saved_configs), 1)
        self.assertEqual(
            [item["rec_id"] for item in self.config_manager.saved_configs[0]],
            ["rec-1", "rec-2"],
        )

    async def test_wait_for_runtime_tasks_waits_for_registered_task_completion(self):
        manager = RecordingManager(self.app)

        async def background_work():
            await asyncio.sleep(0.01)

        task = asyncio.create_task(background_work())
        manager.register_runtime_task(task)

        completed = await manager.wait_for_runtime_tasks(timeout=1)

        self.assertTrue(completed)
        self.assertTrue(task.done())
        self.assertNotIn(task, manager.active_runtime_tasks)

    async def test_update_recording_card_clears_cached_live_url_when_url_changes(self):
        manager = RecordingManager(self.app)
        recording = self._make_recording("rec-1", "https://v.douyin.com/original")
        recording.live_url = "https://live.douyin.com/123456"
        manager.recordings.append(recording)

        await manager.update_recording_card(
            recording,
            {
                "rec_id": recording.rec_id,
                "url": "https://v.douyin.com/changed",
                "streamer_name": recording.streamer_name,
            },
        )
        await asyncio.gather(*self.page.scheduled_tasks)

        self.assertIsNone(recording.live_url)
        self.assertEqual(self.config_manager.saved_configs[-1][0]["live_url"], None)

    async def test_check_if_live_prefers_cached_live_url_and_persists_latest_live_url(self):
        manager = RecordingManager(self.app)
        recording = self._make_recording("rec-1", "https://v.douyin.com/original")
        recording.live_url = "https://live.douyin.com/cached"
        manager.recordings.append(recording)
        captured_live_urls = []

        class DummyRecorder:
            def __init__(self, _app, _recording, recording_info):
                captured_live_urls.append(recording_info["live_url"])

            async def fetch_stream(self):
                return SimpleNamespace(
                    anchor_name="streamer-rec-1",
                    live_url="https://live.douyin.com/latest",
                    is_live=False,
                    title="offline",
                )

        async def skip_check_free_space(*_args, **_kwargs):
            return None

        manager.check_free_space = skip_check_free_space

        with patch("app.core.recording.record_manager.LiveStreamRecorder", DummyRecorder):
            await manager.check_if_live(recording)
            await asyncio.gather(*self.page.scheduled_tasks)

        self.assertEqual(captured_live_urls, ["https://live.douyin.com/cached"])
        self.assertEqual(recording.live_url, "https://live.douyin.com/latest")
        self.assertEqual(self.config_manager.saved_configs[-1][0]["live_url"], "https://live.douyin.com/latest")


if __name__ == "__main__":
    unittest.main()
