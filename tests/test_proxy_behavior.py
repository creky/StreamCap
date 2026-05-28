import asyncio
import inspect
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import flet as ft

from app.core.recording.stream_manager import LiveStreamRecorder
from app.ui.views.settings_view import SettingsPage


class DummyLanguageManager:
    def __init__(self):
        self.language = {
            "recording_manager": {
                "live_room": "Live Room",
                "notify": "Notify",
            },
            "stream_manager": {
                "record_stream_error": "record error",
                "no_ffmpeg_tip": "no ffmpeg",
                "live_recording_stopped_message": "stopped",
                "push_content_end": "push end",
                "status_notify": "status",
            },
            "settings_page": {},
            "video_quality": {},
            "base": {},
        }

    def add_observer(self, _observer):
        return None

    def load(self):
        return None

    def notify_observers(self):
        return None


class DummyPage:
    def __init__(self):
        self.on_keyboard_event = None
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


class ProxyBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_stream_uses_proxy_for_status_check_when_proxy_enabled(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)

        app = SimpleNamespace(
            settings=SimpleNamespace(
                user_config={
                    "enable_proxy": True,
                    "default_platform_with_proxy": "",
                    "default_live_source": "FLV",
                    "force_https_recording": False,
                    "filename_includes_title": False,
                    "folder_name_platform": False,
                    "folder_name_author": False,
                    "folder_name_time": False,
                    "folder_name_title": False,
                },
                cookies_config={},
                accounts_config={},
            ),
            subprocess_start_up_info=None,
            language_manager=DummyLanguageManager(),
            page=DummyPage(),
            proxy_manager=SimpleNamespace(
                is_subscription_active=lambda: False,
                get_status_check_proxy=lambda: "http://127.0.0.1:7890",
                get_proxy=lambda: "http://127.0.0.1:7890",
                mask_proxy_value=lambda value: value,
            ),
        )

        recording = SimpleNamespace(use_proxy=False, live_url=None, is_checking=True)
        recorder = LiveStreamRecorder(
            app,
            recording,
            {
                "platform": "Douyin",
                "platform_key": "douyin",
                "live_url": "https://live.douyin.com/123456",
                "output_dir": temp_dir.name,
                "segment_record": False,
                "segment_time": "1800",
                "save_format": "MP4",
                "quality": "OD",
            },
        )

        captured_proxies = []

        class DummyHandler:
            async def get_stream_info(self, live_url):
                return SimpleNamespace(
                    anchor_name="streamer",
                    live_url=live_url,
                    is_live=False,
                    title="offline",
                )

        def fake_get_platform_handler(*, proxy=None, **_kwargs):
            captured_proxies.append(proxy)
            return DummyHandler()

        with patch(
            "app.core.recording.stream_manager.platform_handlers.get_platform_handler",
            side_effect=fake_get_platform_handler,
        ):
            stream_info = await recorder.fetch_stream()

        self.assertEqual(stream_info.anchor_name, "streamer")
        self.assertEqual(captured_proxies, ["http://127.0.0.1:7890"])
        self.assertTrue(recording.use_proxy)

    async def test_enable_proxy_switch_schedules_immediate_save(self):
        class DummyConfigManager:
            def load_user_config(self):
                return {
                    "language": "Chinese",
                    "enable_proxy": False,
                    "proxy_address": "https://proxy-sub.example/list",
                    "loop_time_seconds": "180",
                }

            def load_language_config(self):
                return {"Chinese": "zh_CN"}

            def load_default_config(self):
                return {
                    "language": "Chinese",
                    "enable_proxy": False,
                    "proxy_address": "",
                    "loop_time_seconds": "180",
                }

            def load_cookies_config(self):
                return {}

            def load_accounts_config(self):
                return {}

        page = DummyPage()
        app = SimpleNamespace(
            page=page,
            content_area=SimpleNamespace(),
            config_manager=DummyConfigManager(),
            record_manager=SimpleNamespace(
                recordings=[],
                persist_recordings=lambda: None,
                initialize_dynamic_state=lambda: None,
            ),
            language_manager=DummyLanguageManager(),
            proxy_manager=SimpleNamespace(sync_from_settings=lambda: None),
        )
        settings_page = SettingsPage(app)

        captured_delays = []

        async def fake_start_task_timer(_task, delay=None):
            captured_delays.append(delay)

        settings_page.delay_handler.start_task_timer = fake_start_task_timer

        switch = ft.Switch(data="enable_proxy")
        event = SimpleNamespace(control=switch, data="true")

        await settings_page.on_change(event)
        await asyncio.gather(*page.scheduled_tasks)

        self.assertEqual(captured_delays, [0])


if __name__ == "__main__":
    unittest.main()
