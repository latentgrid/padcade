"""The board as one Home Assistant MQTT device."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from padcade.drivers.mqtt_device import apply_command, restore_settings, save_settings, settings
from tests.kit import Home


class DeviceControls(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        for n, page in enumerate(('art', 'games', 'sound', 'room')):
            self.home.plugin(page, button=f'top{n + 1}')
        self.board = self.home.board()

    def test_remote_settings_and_local_buttons_share_one_state(self):
        board = self.board
        for name, value in (('profile', 'Room'), ('brightness', '45.0'), ('display', 'OFF'), ('volume', '4')):
            apply_command(board, name, value)
        self.assertEqual(settings(board), dict(profile='Room', brightness=45, display='OFF', volume=4))
        self.assertEqual(self.home.sound.levels, [4])
        for name, value in (('profile', 'Missing'), ('brightness', 'nan'), ('display', 'bad'), ('volume', '9')):
            apply_command(board, name, value)
        self.assertEqual(settings(board), dict(profile='Room', brightness=45, display='OFF', volume=4))
        board.press(3, 9, 127, 1.)  # Wake.
        board.press(3, 9, 127, 2.)  # The third page's button.
        self.assertEqual(settings(board), dict(profile='Sound', brightness=45, display='ON', volume=4))

    def test_last_board_settings_survive_restart(self):
        before = self.board
        apply_command(before, 'display', 'OFF')
        apply_command(before, 'profile', 'room')
        apply_command(before, 'volume', '5')
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            save_settings(path, settings(before))
            after = self.home.board()
            restore_settings(after, path)
            self.assertEqual(settings(after), settings(before))
            path.write_text('broken')
            restore_settings(after, path)
            self.assertEqual(settings(after), settings(before))
