"""For the tests: a board in a temporary home, with plugins written on the spot."""
import shutil
import struct
import tempfile
import textwrap
import wave
from pathlib import Path

from padcade import config
from padcade.board import SHIPPED, Board
from padcade.plugins import Plugin

BLACK = (0, 0, 0)
PAGE = '''
class Plugin:
    def __init__(self, board):
        self.board, self.seen = board, []
    def frame(self, now):
        return [COLOUR] * 64
    def press(self, x, y, velocity, now):
        self.seen.append(['press', x, y])
    def release(self, x, y, now):
        self.seen.append(['release', x, y])
    def action(self, name, now):
        self.seen.append(['action', name])
    def released(self, name, now):
        self.seen.append(['released', name])
    def state(self):
        return {'seen': self.seen}
'''


class Voice:
    """A sound that lasts until it's stopped or ends (`code` set)."""
    def __init__(self, clip, gain=1.):
        self.clip, self.gain, self.code = clip, gain, None
        self.release_seconds = None
        self.level = 1.  # (how loud what is left of it is)

    def remaining_peak(self):
        return self.level

    def poll(self):
        return self.code

    def release(self, seconds):
        self.release_seconds = seconds

    def terminate(self):
        self.code = -15


class Sound:
    """The board's speaker, listened to."""
    def __init__(self):
        self.heard, self.levels, self.woken = [], [], 0

    def play(self, clip, gain=1.):
        self.heard.append(Voice(Path(clip).stem, gain))
        return self.heard[-1]

    def volume(self, level):
        self.levels.append(level)

    def wake(self):
        self.woken += 1


class Home:
    """The owner's folder, and plugin folders for a board to find."""
    def __init__(self, case):
        folder = tempfile.TemporaryDirectory()
        case.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'home'
        self.shipped = Path(folder.name) / 'shipped'
        self.shipped.mkdir()
        self.sound = Sound()

    def plugin(self, name, code=PAGE, own=False, colour=(200, 0, 0), **about):
        folder = (self.path / 'plugins' if own else self.shipped) / name
        folder.mkdir(parents=True, exist_ok=True)
        about = {'name': name.title(), 'kind': 'page', 'api': 1, 'colour': '#%02x%02x%02x' % colour} | about
        (folder / 'plugin.toml').write_text(config.dumps(about))
        (folder / 'plugin.py').write_text(f'COLOUR = {colour!r}\n' + textwrap.dedent(code))
        return folder

    def board(self, shipped=None, **options):
        return Board(self.path, shipped=shipped or self.shipped, sound=self.sound, **options)


def pads(board, now):
    frame = board.frame(now)
    return [frame[10 * y + x] for y in range(1, 9) for x in range(1, 9)]


def level(colour):
    return sum(colour)


def shipped(name, host=None):
    """One of the plugins shipped with the board, started on its own."""
    plugin = Plugin(SHIPPED / name)
    assert plugin.error is None, plugin.error
    return plugin.start(host)


def ship(home, *names):
    """Plugins the board really ships, put among the home's own test plugins."""
    for name in names:
        shutil.copytree(SHIPPED / name, home.shipped / name)


def note(path, frames=4410):
    """A short WAV in the format the mixer plays directly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((2, 2, 44100, 0, 'NONE', ''))
        wav.writeframes(struct.pack('<hh', 1000, -1000) * frames)
    return path
