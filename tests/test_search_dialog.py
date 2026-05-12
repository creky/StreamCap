import unittest
from types import SimpleNamespace

from app.ui.components.dialogs.search_dialog import SearchDialog


class DummyLanguageManager:
    def __init__(self):
        self.language = {
            "search_dialog": {
                "search_keyword": "Enter search keyword",
            },
            "recordings_page": {
                "search": "Search",
                "filter_all": "All",
                "filter_recording": "Recording",
                "filter_living": "Living",
                "filter_error": "Error",
                "filter_offline": "Offline",
                "filter_stopped": "Stopped",
            },
            "base": {
                "cancel": "Cancel",
                "sure": "Search",
            },
        }

    def add_observer(self, _observer):
        return None


class SearchDialogTests(unittest.TestCase):
    def test_query_field_autofocuses_and_submits_on_enter(self):
        recordings_page = SimpleNamespace(
            current_filter="all",
            app=SimpleNamespace(language_manager=DummyLanguageManager()),
        )

        dialog = SearchDialog(recordings_page=recordings_page)

        self.assertTrue(dialog.query.autofocus)
        self.assertIsNotNone(dialog.query.on_submit)
        self.assertIs(dialog.query.on_submit.__self__, dialog)
        self.assertEqual(dialog.query.on_submit.__name__, "submit_query")


if __name__ == "__main__":
    unittest.main()
