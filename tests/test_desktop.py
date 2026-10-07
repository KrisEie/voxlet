"""UI smoke check with isolated data, no microphone or global shortcut registration."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
import app
from portable_config import load_paths
from ui import VoiceDialog


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_empty_install_and_bokmal_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(app, 'ROOT', root), patch.object(app, 'PATHS', load_paths(root)), \
                    patch.object(app.Hotkeys, 'register', return_value=[]), \
                    patch.object(app.Studio, 'fill_devices'), patch.object(app.Studio, 'setup_tray'), \
                    patch('ui.startup_enabled', return_value=False):
                window = app.Studio()
                try:
                    self.assertEqual(window.engines.voices, [])
                    self.assertEqual(window.voice.currentData(), 'piper:en')
                    self.assertEqual(window.nor_voice.currentData(), 'piper:no')
                    self.assertFalse(window.delete_voice_button.isEnabled())
                    dialog = VoiceDialog(window, app.Recorder)
                    try:
                        self.assertEqual(dialog.prompt_language.currentData(), 'nb_long')
                        self.assertFalse(dialog.recording)
                    finally:
                        dialog.reject()
                    self.assertEqual(window.tabs.count(), 4)
                finally:
                    window.shutdown()
                    window.deleteLater()
                    self.qt.processEvents()


if __name__ == '__main__':
    unittest.main()
