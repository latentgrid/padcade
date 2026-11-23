"""config.toml: what the owner sets, by hand or on the page."""
import tempfile
import tomllib
import unittest
from pathlib import Path

from padcade import config

SETTINGS = {
    'brightness': 80,
    'buttons': {'top1': 'open:pixel_art', 'top8': 'power'},
    'plugins': {
        'soundboard': {
            'enabled': True,
            'buttons': {'right1': 'volume_up'},
            'settings': {'volume': 3, 'ratio': .5},
            'banks': [{'name': 'Sounds "one"', 'pads': {'1,8': {'thing': 'Airhorn', 'colour': '#ff5a5a', 'mode': 'once'}}},
                      {'name': 'Jäm 🎹', 'pads': {}}],
        },
        'pixel_art': {'enabled': False, 'settings': {'order': ['lava', 'rain']}},
    },
}


class File(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'config.toml'

    def test_what_is_saved_is_read_back_and_is_toml_anyone_can_edit(self):
        config.save(self.path, SETTINGS)
        self.assertEqual(config.load(self.path), SETTINGS)
        self.assertEqual(tomllib.loads(self.path.read_text()), SETTINGS)
        self.assertIn('[plugins.soundboard.settings]', self.path.read_text())  # (tables, not one long line)

    def test_a_date_typed_by_hand_is_still_toml_when_the_page_saves(self):
        self.path.write_text('since = 2026-11-15\nat = 2026-11-15T10:00:00\n')
        loaded = config.load(self.path)
        config.save(self.path, loaded)
        self.assertEqual(config.load(self.path), loaded)

    def test_a_board_without_a_file_starts_empty(self):
        self.assertEqual(config.load(self.path), {})

    def test_a_file_with_a_mistake_is_set_aside_not_overwritten(self):
        self.path.write_text('brightness = \n')
        self.assertEqual(config.load(self.path), {})
        self.assertFalse(self.path.exists())
        self.assertEqual(self.path.with_suffix('.broken').read_text(), 'brightness = \n')
