from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'brainwave_player_v8(1).py'))

class PackagingPathTests(unittest.TestCase):
    def test_relative_recording_path_uses_documents_not_install_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(MODULE['QStandardPaths'], 'writableLocation', return_value=directory):
                self.assertEqual(MODULE['recording_path']('session.csv'), Path(directory) / 'BrainwavePlayer/session.csv')

    def test_explicit_absolute_save_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            chosen = Path(directory) / 'chosen.csv'
            self.assertEqual(MODULE['recording_path'](str(chosen)), chosen)

if __name__ == '__main__':
    unittest.main()
