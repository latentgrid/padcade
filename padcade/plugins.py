"""Plugins: everything that draws on the pads. One folder each, with plugin.toml (what it is) and plugin.py (a class
Plugin with frame(now), press(x, y, velocity, now) and release(x, y, now); see docs/plugins.md). A plugin that raises
or takes too long is stopped and says why; the board carries on."""
import ctypes
import importlib.util
import threading
import time
import tomllib
import traceback
from pathlib import Path

from . import API

KINDS = ('page', 'scene', 'game')
BLACK = (0, 0, 0)
BLANK = [BLACK] * 64


class Overrun(BaseException):
    """Raised inside a plugin's call that ran past its time (not an Exception, so its own `except` can't keep it)."""


class Watch(threading.Thread):
    """Stops a call that doesn't come back: the plugin's thread gets an Overrun raised in it.
    ponytail: only Python code is interrupted; a call stuck inside C (a long sleep, a blocking read) is stopped as
    soon as it returns. Run plugins in a process of their own if that ever isn't enough."""
    def __init__(self):
        super().__init__(name='plugin-watch', daemon=True)
        self.changed = threading.Condition()
        self.armed = None  # (thread, deadline)
        self.next_check = None
        self.start()

    def run(self):
        with self.changed:
            while True:
                delay = None if self.next_check is None else max(0., self.next_check - time.monotonic())
                self.changed.wait(delay)
                now = time.monotonic()
                if self.armed and self.armed[1] <= now:
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(self.armed[0]), ctypes.py_object(Overrun))
                    self.armed = None
                # Keep a scheduled check when a fast call finishes. The next frame can reuse it.
                if self.next_check is not None and now >= self.next_check:
                    self.next_check = self.armed[1] if self.armed else None

    def watch(self, target):
        self.armed = target
        if target and (self.next_check is None or target[1] < self.next_check):
            self.next_check = target[1]
            self.changed.notify()

    def arm(self, seconds):
        """Watches the call that starts now; gives what it watched before (a page's call, while it asks a scene)."""
        with self.changed:
            outer = self.armed
            self.watch((threading.get_ident(), time.monotonic() + seconds))
            return outer

    def disarm(self, outer):
        with self.changed:
            self.watch(outer)
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(threading.get_ident()), None)  # (one on its way)


WATCH = None
SLOW = 10  # times the patience a plugin gets to be read and started (a Pi reading from its card as it boots)


def guarded(call, patience=None):
    """Runs call(): (what it returned, None), or (None, the error) if it raised or ran longer than `patience`."""
    global WATCH
    WATCH = WATCH or Watch()
    started = time.monotonic()
    try:
        outer = WATCH.arm(patience) if patience else None
        try:
            result = call()
        finally:
            if patience:
                WATCH.disarm(outer)
        if patience and time.monotonic() - started > patience:
            raise Overrun
        return result, None
    except KeyboardInterrupt:
        raise
    except BaseException as error:  # (SystemExit too: a plugin does not end the board)
        return None, error


def colour(text, default=(255, 255, 255)):
    """'#rrggbb' as (r, g, b)."""
    try:
        return tuple(bytes.fromhex(text.lstrip('#')))[:3] if len(text.lstrip('#')) == 6 else default
    except (ValueError, AttributeError):
        return default


KINDS_OF = {str: ('name', 'kind', 'colour', 'version', 'about', 'button', 'thing', 'things', 'whole'), list: ('needs',),
            dict: ('actions', 'settings', 'pad', 'defaults')}


class Plugin:
    """One plugin folder: its description, whether it is the owner's own, and why it is stopped if it is."""
    def __init__(self, folder, own=False, patience=1.):
        self.folder, self.id, self.own, self.patience = Path(folder), Path(folder).name, own, patience
        self.error, self.cls = None, None
        self.about = {'name': self.id, 'kind': 'page'}
        try:
            about = tomllib.loads((self.folder / 'plugin.toml').read_text())
            for kind, names in KINDS_OF.items():
                for name in names:
                    if name in about and not isinstance(about[name], kind):
                        raise ValueError(f'{name} must be {"text" if kind is str else "a list" if kind is list else "a table"}')
            tables = [about.get('defaults', {}).get('buttons', {}), *about.get('settings', {}).values(), *about.get('pad', {}).values()]
            if not all(isinstance(table, dict) for table in tables):
                raise ValueError('every setting, pad option and defaults.buttons must be a table')
            if about.get('kind', 'page') not in KINDS:
                raise ValueError(f'kind is "{about["kind"]}", it must be one of: {", ".join(KINDS)}')
            if type(about.get('api')) is not int:
                raise ValueError('api (the plugin interface version, a whole number) is missing')
            if about['api'] > API:
                raise ValueError(f'made for plugin interface {about["api"]}, this board has {API}: update the board')
            self.about |= about
        except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
            self.error = f'plugin.toml: {error}'

    name = property(lambda self: self.about['name'])
    kind = property(lambda self: self.about['kind'])
    colour = property(lambda self: colour(self.about.get('colour')))

    def load(self):
        """Its Python file, read afresh (switching a stopped plugin on again tries it again)."""
        self.error = self.cls = None

        def read():
            spec = importlib.util.spec_from_file_location(f'padcade_plugin_{self.id}', self.folder / 'plugin.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.Plugin
        self.cls, error = guarded(read, self.patience * SLOW)
        if error:
            self.stop('plugin.py', error)

    def stop(self, what, error):
        line = next((f'line {entry.lineno}' for entry in reversed(traceback.extract_tb(error.__traceback__))
                     if Path(entry.filename).parent == self.folder), None)
        self.error = what + (' took too long' if isinstance(error, Overrun) else f' raised {type(error).__name__}: {error}') \
            + (f' ({line})' if line else '')
        print(f'plugin {self.id} stopped: {self.error}', flush=True)

    def start(self, host, patience=1.):
        if self.cls is None and not self.error:
            self.load()
        return Running(self, host, patience)


class Running:
    """A plugin at work. Every call into it is guarded: an error or an overrun stops the plugin, and the call gives
    its default."""
    def __init__(self, plugin, host, patience):
        self.plugin, self.patience, self.instance = plugin, patience, None
        if plugin.cls:
            self.instance = self.call(lambda: plugin.cls(host), what='starting it', patience=patience * SLOW)

    stopped = property(lambda self: self.plugin.error is not None or self.plugin.cls is None)

    def call(self, method, *args, default=None, what=None, timed=True, patience=None):
        if self.stopped:
            return default
        name = method if isinstance(method, str) else None
        if name and not hasattr(type(self.instance), name):
            return default  # (it has no such method: its choice)
        result, error = guarded(lambda: getattr(self.instance, name)(*args) if name else method(*args),
                                (patience or self.patience) if timed else None)
        if error:
            self.plugin.stop(f'its {name}()' if name else what, error)
            return default
        return result

    def frame(self, now):
        """Its 64 colours, bottom row first; black when it has none to give."""
        picture = self.call('frame', now)
        try:  # (numbers out of range, infinite or not numbers at all are brought to 0-255: the LEDs' range)
            if len(picture) != 64:
                raise ValueError
            return [(r, g, b) if 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255 else
                    (min(255, max(0, r)), min(255, max(0, g)), min(255, max(0, b))) for r, g, b in picture]
        except (TypeError, ValueError):
            if not self.stopped:
                self.plugin.stop('its frame()', ValueError('it must return 64 colours, each (red, green, blue) from 0 to 255'))
            return BLANK

    def press(self, x, y, velocity, now):
        self.call('press', x, y, velocity, now)

    def release(self, x, y, now):
        self.call('release', x, y, now)


def discover(shipped, own, patience=1.):
    """Every plugin folder: the ones shipped with the board, then the owner's (a folder name taken by a shipped one is
    refused)."""
    found = {}
    for folder, mine in ((shipped, False), (own, True)):
        for path in sorted(Path(folder).iterdir()) if Path(folder).is_dir() else ():
            if (path / 'plugin.toml').exists() or (path / 'plugin.py').exists():
                plugin = Plugin(path, mine, patience)
                if plugin.id in found:
                    plugin.error = f'a plugin shipped with the board is already called {plugin.id}: rename the folder'
                    plugin.id += ' (yours)'
                found[plugin.id] = plugin
    return found
