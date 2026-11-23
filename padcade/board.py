"""The board's logic, no hardware: the surface is 10 by 10, (x, y) from the bottom left; the 8 by 8 pads in the middle
show the open page (a plugin), the round buttons round them do whatever config.toml puts on them. A frame is 100
colours, 0-255 RGB, numbered 10 * y + x."""
import copy
import io
import math
import os
import re
import secrets
import shutil
import tempfile
import tomllib
import zipfile
from contextlib import AbstractContextManager
from functools import cached_property
from pathlib import Path, PurePosixPath

from . import API, NAME, VERSION, config
from .sound_banks import unzip
from .gallery import Gallery
from .plugins import BLACK, BLANK, Plugin, discover, guarded

try:
    from .drivers.imu import Pickup
except ImportError:  # (a computer without Unix serial ports, running the simulator: nothing to pick up)
    class Pickup:
        reset, update = (lambda self: None), (lambda self, sample: False)

SHIPPED = Path(__file__).parent / 'plugins'
SHOW = 10.  # seconds the round buttons stay lit after a press
DEFAULTS = {'brightness': 80, 'sleep': 0, 'charge_limit': 80}  # percent; minutes untouched until the pads go dark (0: never); percent
BUTTON_SPOTS = {f'{side}{n}': xy for n in range(1, 9)
                for side, xy in (('top', (n, 9)), ('bottom', (n, 0)), ('left', (0, 9 - n)), ('right', (9, 9 - n)))}


def spot(name):
    """Where a round button named in config.toml is: 'top3' -> (3, 9)."""
    return BUTTON_SPOTS.get(name)


def triple(colour):
    """A colour a plugin gave, if it is one."""
    try:
        r, g, b = colour
        return (r + 0, g + 0, b + 0)
    except (TypeError, ValueError):
        return None


def dim(colour, v):
    r, g, b = colour
    return r * v, g * v, b * v


def clip_pads(table):
    """The Soundboard's clip assignments, including layouts saved before it had banks."""
    banks = table.get('banks') or [table]
    if not isinstance(banks, list):
        raise ValueError('Expected a list of Soundboard banks.')
    for bank in banks:
        if not isinstance(bank, dict) or not isinstance(pads := bank.get('pads', {}), dict):
            raise ValueError('Expected a Soundboard bank with pad assignments.')
        for key, pad in pads.items():
            if isinstance(pad, dict) and isinstance(clip := pad.get('thing'), str):
                yield pads, key, clip


class Silence:
    """The speaker of a board without one."""
    class Voice:
        poll, terminate = (lambda self: 0), (lambda self: None)

    def play(self, clip, gain=1.):
        return self.Voice()

    def volume(self, level):
        pass

    def wake(self):
        pass


class Host:
    """What a plugin is given when it starts: its settings and what the owner put on its pads, a folder for its own
    files, and the board's parts."""
    def __init__(self, board, plugin):
        self._board, self._plugin = board, plugin
        self.folder = plugin.folder  # (what it shipped with)
        level = board.config.get('tabletop_gravity')  # (what the motion sensor reads with the board flat on a table)
        if isinstance(level, list) and len(level) == 3 and all(type(v) in (int, float) for v in level):
            self.tabletop_gravity = tuple(level)

    @property
    def config(self):
        """Its table in config.toml, as it is now: `pads` or `banks`, `buttons`, `settings`."""
        return self._board.config['plugins'].get(self._plugin.id, {})

    @property
    def settings(self):
        """Its settings: what the owner chose, else the defaults of its description."""
        chosen = self.config.get('settings', {})
        return {name: chosen.get(name, about.get('default')) for name, about in self._plugin.about.get('settings', {}).items()}

    @property
    def data(self):
        """A folder that is its own and survives updates."""
        folder = self._board.home / 'data' / self._plugin.id
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def plugins(self, kind, *, include_disabled=False):
        """The working plugins of a kind, normally only those switched on, for a page that hosts them: each
        has name, colour and start(), which gives a new one with frame(now), press(...), release(...)."""
        return [Hosted(self._board, p) for p in self._board.plugins.values()
                if p.kind == kind and not p.error and (include_disabled or self._board.enabled(p.id))]

    gallery = property(lambda self: self._board.gallery)  # the owner's drawings (padcade/gallery.py)

    def play(self, clip, gain=1.):
        """Play a WAV at a fraction of full level. Its voice has poll() and terminate()."""
        gain = min(1., max(0., gain))
        voice = self._board.sound.play(clip, gain)
        recorder = self._board.running.get('soundboard')
        if recorder and self._plugin.id != 'soundboard':
            if hasattr(type(recorder.instance), 'external_voice'):
                return recorder.call('external_voice', clip, self.now, gain, voice, default=voice)
            if gain == 1.:
                recorder.call('external_clip', clip, self.now)
            else:
                recorder.call('external_clip', clip, self.now, gain)
        return voice

    now = property(lambda self: self._board.now)

    def append_bank(self, bank):
        """Save a new editable bank during a request's board-thread commit."""
        settings = copy.deepcopy(self._board.config)
        banks = settings['plugins'][self._plugin.id].setdefault('banks', [])
        banks.append(bank)
        self._board.configure(settings)
        return len(banks) - 1

    def clear_clip_pads(self, clip):
        """Save the layout before removing a clip, so a failed save leaves its sound playable."""
        settings = copy.deepcopy(self._board.config)
        table = settings['plugins'][self._plugin.id]
        matches = [(pads, key) for pads, key, name in clip_pads(table) if name == clip]
        for pads, key in matches:
            del pads[key]
        if matches:
            self._board.configure(settings)
        if hasattr(os, 'O_DIRECTORY'):
            # A power cut after unlinking the WAV must not revive the old config.toml.
            directory = os.open(self._board.home, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def prepare_sounds(self, clips):
        """Hint which WAV files are likely to play next on the physical speaker."""
        if prepare := getattr(self._board.sound, 'prepare', None):
            prepare(clips)

    def wake_speaker(self):
        self._board.sound.wake()

    volume = property(lambda self: self._board.volume)  # 1-5

    def set_volume(self, level):
        self._board.set_volume(level)

    def motion(self, now):
        """The motion sensor's latest reading while it is fresh, else None."""
        return self._board.motion(now)

    def changed(self):
        """Tells the management page that what the plugin shows there (its things, its state) is different now."""
        self._board.revision += 1


class Hosted:
    def __init__(self, board, plugin):
        self._board, self._plugin = board, plugin
        self.id, self.name, self.colour, self.about = plugin.id, plugin.name, plugin.colour, plugin.about

    def start(self):
        return self._plugin.start(Host(self._board, self._plugin), self._board.patience)


class Board:
    def __init__(self, home, shipped=SHIPPED, sound=None, patience=1.):
        self.home, self.shipped, self.sound, self.patience = Path(home), shipped, sound or Silence(), patience
        self.power, self.page, self.volume = True, None, 3
        self.shown_until = None  # (the round buttons lit until then)
        self.motion_sample = self.battery_sample = None  # (the drivers' threads put their readings here)
        self.motion_connected = False
        self.pickup = Pickup()
        self.now, self.wake_blocked_until = 0., 0.
        self.last_activity = self.welcome_at = None
        self.config_version = secrets.token_hex(16)
        self.revision = 0  # (goes up whenever the management page has something new to fetch)
        self.parts = {}  # name -> what the hardware says about it, for the page
        self.gallery = Gallery(self.home / 'data' / 'drawings.json')
        self.restore_plugin()
        self.plugins, self.running = discover(self.shipped, self.home / 'plugins', self.patience), {}
        self.disconnect_mqtt = None
        self.config, self.saved = {}, None  # (what is in force; what config.toml holds)
        if (self.home / 'config.toml').exists():
            (self.home / 'config.toml').chmod(0o600)  # (it may hold a token: put right if it was written or copied otherwise)
        layout = self.home / 'config.toml'
        settings = config.load(layout)
        self.migrating_owner = 'login' in settings or (not layout.exists() and (self.home / 'config.broken').exists())
        owner_file = self.home / 'owner.toml'
        if ('login' in settings and settings['login'] != config.UNPAIRED
                and (not owner_file.exists() or layout.stat().st_mtime_ns > owner_file.stat().st_mtime_ns)):
            config.save(owner_file, {'login': settings['login']})
        self.configure(settings, loaded=True)

    def finish_owner_migration(self):
        """The installer can roll back to a release reading login from config.toml until the first healthy frame."""
        if self.migrating_owner:
            config.save(self.home / 'config.toml', self.config)
            self.saved = copy.deepcopy(self.config)
            self.migrating_owner = False

    # ---- config.toml
    def configure(self, settings, loaded=False):
        """New settings, from the page or (`loaded`) the file: completed with the defaults of anything they leave
        out, saved if that or the page changed anything, and in force."""
        before = copy.deepcopy(settings)
        settings = copy.deepcopy(settings)
        settings.pop('login', None)
        settings.pop('peer_url', None)  # (the other board's address, from when the page had a switch to it)
        mqtt_id = settings.get('mqtt_id', self.config.get('mqtt_id'))
        if not isinstance(mqtt_id, str) or not re.fullmatch(r'[a-z0-9_]{1,40}', mqtt_id):
            # An installed board already paired with a broker keeps its existing automations.
            broker = settings.get('mqtt')
            legacy = loaded and 'buttons' in settings and isinstance(broker, dict) and broker.get('host')
            mqtt_id = 'launchpad' if legacy else 'padcade_' + secrets.token_hex(6)
        settings['mqtt_id'] = mqtt_id
        for name, default in DEFAULTS.items():  # (a value of the wrong kind, typed by hand: the default instead)
            if type(settings.get(name)) not in (int, float):
                settings[name] = default
        settings['brightness'] = min(100, max(5, settings['brightness']))
        if not isinstance(settings.get('buttons', {}), dict):
            del settings['buttons']
        if not isinstance(settings.get('plugins'), dict):
            settings['plugins'] = {}
        settings['plugins'] = {k: v if isinstance(v, dict) else {} for k, v in settings['plugins'].items()}
        for table in [settings] + list(settings['plugins'].values()):
            if 'buttons' in table:
                table['buttons'] = {k: v for k, v in table['buttons'].items() if isinstance(v, str)} if isinstance(table['buttons'], dict) else {}
        pages = [p for p in self.plugins.values() if p.kind == 'page']
        fresh = 'buttons' not in settings
        buttons = settings.setdefault('buttons', {'top7': 'show', 'top8': 'power'} if fresh else {})
        tables = settings.setdefault('plugins', {})
        for plugin in self.plugins.values():
            new = plugin.id not in tables
            table = tables.setdefault(plugin.id, {})
            table.setdefault('enabled', plugin.about.get('enabled', True) is not False)  # (a page may come switched off)
            if plugin.about.get('banks') and isinstance(table.get('pads'), dict) and not table.get('banks'):
                table['banks'] = [{'name': 'Bank 1', 'pads': table.pop('pads')}]  # (placed before the page had banks)
            if 'previous_buttons' in plugin.about and not table.get('updated_buttons'):
                if loaded and table.get('buttons') == plugin.about['previous_buttons']:
                    table['buttons'] = copy.deepcopy(plugin.about['defaults']['buttons'])
                table['updated_buttons'] = True
            defaults = plugin.about.get('defaults', {})
            if (loaded and not new and plugin.id == 'soundboard' and not plugin.own
                    and table.get('factory_banks', 1) in range(1, defaults.get('factory_banks', 1))):
                config.upgrade_soundboard(table, copy.deepcopy(defaults), self.home / 'data' / plugin.id)
            for name, default in defaults.items():
                if new or name == 'buttons':
                    table.setdefault(name, copy.deepcopy(default))
            if (plugin.id == 'soundboard' and not plugin.own and defaults.get('buttons', {}).get('left8') == 'performance'
                    and table.get('updated_buttons') not in (5, 6)):
                local = table['buttons']
                version = table.get('updated_buttons')
                if loaded and version not in (2, 3, 4) and 'bottom7' not in buttons and 'sustain' not in local.values():
                    local.setdefault('bottom7', 'sustain')
                if loaded and 'left8' not in buttons and 'bottom1' not in buttons:
                    old_pair = ('performance', 'takes') if version == 4 else ('record', 'performance')
                    if (local.get('left8'), local.get('bottom1')) == old_pair:
                        local['left8'] = 'performance'
                        del local['bottom1']
                    elif (version not in (3, 4) and local.get('left8') == 'record' and 'bottom1' not in local
                          and 'performance' not in local.values()):
                        local['left8'] = 'performance'
                table['updated_buttons'] = 5
            if plugin.id == 'soundboard' and not plugin.own and table.get('updated_buttons') == 5:
                # No track is "selected" any more: a button that muted the selected one has nothing left to do.
                table['buttons'] = {key: action for key, action in table['buttons'].items() if action != 'mute_selected'}
                table['updated_buttons'] = 6
            if plugin in pages and (new or fresh) and f'open:{plugin.id}' not in buttons.values():
                wanted = [plugin.about.get('button')] + [f'top{n}' for n in range(1, 9)]
                free = next((name for name in wanted if name and spot(name) and name not in buttons), None)
                if free:
                    buttons[free] = f'open:{plugin.id}'
        if loaded:
            self.saved = before
        persisted = settings | {'login': config.owner(self.home) or config.UNPAIRED} if self.migrating_owner else settings
        if persisted != self.saved:  # (saved first: what is in force is always what the file says)
            config.save(self.home / 'config.toml', persisted)
            self.saved = copy.deepcopy(persisted)
            self.config_version = secrets.token_hex(16)
        was, self.config = self.config, settings
        self.__dict__.pop('public_config', None)
        for plugin in self.plugins.values():
            if not self.enabled(plugin.id):
                if plugin.id in self.running:
                    self.discard(self.running.pop(plugin.id))
                plugin.cls = None  # (a plugin that is switched off is not even read; switched on, it is read afresh)
                continue
            if plugin.error and was.get('plugins', {}).get(plugin.id, {}).get('enabled') is False:
                plugin.error = None  # (stopped, switched off and on again: tried again)
            if plugin.cls is None and not plugin.error:
                plugin.load()
            if plugin.kind == 'page' and plugin.id not in self.running:
                self.running[plugin.id] = plugin.start(Host(self, plugin), self.patience)
        self.settle()
        self.revision += 1

    def restore_plugin(self):
        """An interrupted replacement keeps the last working plugin outside the discovery directory."""
        rollback = self.home / '.plugin-rollback'
        if rollback.is_dir():
            for previous in rollback.iterdir():
                target = self.home / 'plugins' / previous.name
                if target.exists():
                    shutil.rmtree(target)
                previous.rename(target)
            rollback.rmdir()

    def install(self, archive):
        """A plugin from a .zip (its folder, or just its files) into the owner's folder; says what is wrong with it."""
        try:
            with unzip(archive) as files:
                names = [PurePosixPath(n) for n in files.namelist() if not n.endswith('/')]
                about = [n for n in names if n.name == 'plugin.toml']
                if len(about) != 1 or sum(f.file_size for f in files.infolist()) > 20 * 2**20:
                    return {'error': 'A plugin .zip holds one folder with plugin.toml and plugin.py in it (at most 20 MB).'}
                root = about[0].parent
                name = root.name or tomllib.loads(files.read(str(about[0])).decode()).get('name', '')
                name = re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_')
                if not name:
                    return {'error': 'The plugin needs a name in plain letters: its folder\'s, or `name` in plugin.toml.'}
                if name in self.plugins and not self.plugins[name].own:
                    return {'error': f'A plugin shipped with the board is already called {name}.'}
                (self.home / 'plugins').mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=self.home) as staging:  # (the old copy goes only once the new one is whole)
                    for n in names:
                        inside = n.relative_to(root) if n.is_relative_to(root) else None
                        if inside is None or '..' in inside.parts or n.is_absolute():
                            continue  # (nothing is written outside the plugin's folder)
                        (Path(staging) / name / inside).parent.mkdir(parents=True, exist_ok=True)
                        (Path(staging) / name / inside).write_bytes(files.read(str(n)))
                    candidate = Path(staging) / name
                    if not (candidate / 'plugin.py').is_file():
                        return {'error': 'The plugin is missing plugin.py.'}
                    manifest = Plugin(candidate, own=True, patience=self.patience)
                    if manifest.error:
                        return {'error': manifest.error}
                    compile((candidate / 'plugin.py').read_bytes(), 'plugin.py', 'exec')

                    target = self.home / 'plugins' / name
                    previous = self.home / '.plugin-rollback' / name
                    old_config = copy.deepcopy(self.config)
                    if target.exists():
                        previous.parent.mkdir()
                        target.rename(previous)
                    try:
                        candidate.rename(target)
                        self.reload()
                        if self.plugins[name].error:
                            raise ValueError(self.plugins[name].error)
                    except Exception:
                        if previous.exists():
                            self.restore_plugin()
                        elif target.exists():
                            shutil.rmtree(target)
                        self.reload(old_config)
                        raise
                    if previous.exists():
                        shutil.rmtree(previous.parent)
        except (zipfile.BadZipFile, ValueError, SyntaxError, OSError) as error:
            return {'error': f'That plugin could not be installed ({error}).'}

    def remove(self, plugin, data=False):
        """One of the owner's plugins taken away (what it saved too, if asked)."""
        found = self.plugins.get(plugin)
        if not found or not found.own:
            return {'error': 'Only your own plugins can be removed.'}
        shutil.rmtree(found.folder, ignore_errors=True)
        if data:
            shutil.rmtree(self.home / 'data' / found.id, ignore_errors=True)
        settings = copy.deepcopy(self.config)
        settings['plugins'].pop(plugin, None)
        settings['buttons'] = {name: does for name, does in settings['buttons'].items() if does != f'open:{plugin}'}
        self.reload(settings)

    def reset(self, files=False):
        """Back to how a new board comes: every setting, pad and round button, no password, no token. With `files`,
        what the owner added goes too: the clips, the drawings and their own plugins."""
        for run in self.running.values():
            self.discard(run)
        self.running.clear()
        self.page = None
        try:
            if files:
                for folder in ('data', 'plugins', '.plugin-rollback'):
                    path = self.home / folder
                    if path.exists():
                        shutil.rmtree(path)
            for name in ('config.broken', 'config.tmp', 'owner.tmp', 'state.tmp'):
                (self.home / name).unlink(missing_ok=True)
            for leftover in self.home.glob('.settings-*.tmp'):
                leftover.unlink(missing_ok=True)
            if files:
                self.gallery = Gallery(self.home / 'data' / 'drawings.json')
            self.power, self.welcome_at, self.shown_until = True, None, None
            self.last_activity, self.wake_blocked_until = None, 0.
            self.pickup.reset()
            self.set_volume(3)
            self.reload({'plugins': {}})
            if self.disconnect_mqtt:
                self.disconnect_mqtt()
            # The broker writes its last state on disconnect.
            (self.home / 'state.json').unlink(missing_ok=True)
            self.set_password(None)
        except OSError:
            self.gallery = Gallery(self.home / 'data' / 'drawings.json')
            self.page = None
            self.reload()
            raise

    def reload(self, settings=None):
        """The plugin folders read again, every page started afresh."""
        for run in self.running.values():
            self.discard(run)
        self.plugins, self.running = discover(self.shipped, self.home / 'plugins', self.patience), {}
        self.configure(settings or self.config)

    # ---- a password for the management page, if the owner wants one (kept as a salted hash)
    def set_password(self, password, playing=False):
        """A new password; none at all (an empty one); or, with `playing`, the page opened for playing while the
        password goes on guarding the managing. That last one changes nothing when asked again."""
        if self.migrating_owner and not password:
            config.save(self.home / 'config.toml', self.config | {'login': config.UNPAIRED})
        verifier, locked = config.credentials(self.home)
        if playing and not password:
            if verifier and locked:
                config.save(self.home / 'owner.toml', {'login': verifier, 'admin_only': True})
        else:
            verifier = config.set_password(self.home, password, admin_only=bool(verifier) and not locked)  # (a changed password keeps what it guards)
        if self.migrating_owner:
            self.configure(self.config)
        self.revision += 1
        return verifier

    def discard(self, run):
        """A page that will not be used again: it closes, and stops whatever it keeps going (a thread, a socket)."""
        run.call('close')
        run.call('stop')

    def secrets(self, plugin):
        """The names of a plugin's settings that are secrets (a token, a password)."""
        return [name for name, about in self.plugins[plugin].about.get('settings', {}).items() if about.get('type') == 'secret']

    def management_settings_changed(self, settings):
        """Guests can arrange pads, play and adjust the ordinary instrument settings."""
        current = self.public_config
        layout = {'brightness', 'sleep', 'buttons', 'plugins'}
        if any(settings.get(k) != current.get(k) for k in (current.keys() | settings.keys()) - layout):
            return True
        tables = settings.get('plugins', {})
        if not isinstance(tables, dict) or tables.keys() != current['plugins'].keys():
            return True
        for name, before in current['plugins'].items():
            after = tables[name]
            allowed = {'banks', 'pads', 'buttons'}
            if name in ('soundboard', 'pixel_art') and not self.plugins[name].own:
                allowed.add('settings')
            if not isinstance(after, dict) or any(after.get(k) != before.get(k) for k in (after.keys() | before.keys()) - allowed):
                return True
        return False

    def from_page(self, settings):
        """Settings as the management page sends them, completed with what it is never shown: the page's password
        and every secret setting it left alone (one it emptied is removed)."""
        settings = {k: v for k, v in settings.items() if k != 'login'}
        broker, previous = settings.get('mqtt'), self.config.get('mqtt', {})
        if isinstance(broker, dict) and 'password' not in broker and previous.get('password') and all(
                broker.get(key, default) == previous.get(key, default) for key, default in (('host', ''), ('port', 1883), ('username', ''))):
            broker['password'] = previous['password']
        elif isinstance(broker, dict) and not broker.get('password'):
            broker.pop('password', None)
        tables = settings.setdefault('plugins', {})
        if not isinstance(tables, dict):
            raise ValueError('Expected plugin settings.')
        # Keep missing plugins and retired options on disk across saves from their redacted browser view.
        for plugin, old in self.config['plugins'].items():
            if plugin not in self.plugins:
                tables[plugin] = old
                continue
            fields = self.plugins[plugin].about.get('settings', {})
            table = tables.setdefault(plugin, {})
            if not isinstance(table, dict):
                raise ValueError('Expected a plugin settings table.')
            if plugin == 'soundboard' and not self.plugins[plugin].own and table != old:
                # Only check newly placed sounds. Saved layouts may include samples still being generated.
                added = {clip for _, _, clip in clip_pads(table)} - {clip for _, _, clip in clip_pads(old)}
                clips = self.home / 'data' / plugin / 'clips'
                for clip in added:
                    if Path(clip).name != clip or not (clips / (clip + '.wav')).is_file():
                        raise ValueError(f'The clip "{clip}" is no longer in the library.')
            kept = old.get('settings', {})
            if 'settings' not in table and not kept:
                continue
            chosen = table.setdefault('settings', {})
            if not isinstance(chosen, dict):
                raise ValueError('Expected plugin options.')
            table['settings'] = chosen = {k:v for k,v in chosen.items() if k in fields}
            chosen.update({k:v for k,v in kept.items() if k not in fields})
            for name in self.secrets(plugin):
                destination = fields[name].get('bound_to')
                default = fields.get(destination, {}).get('default')
                if name not in chosen and name in kept:
                    if not destination or chosen.get(destination, default) == kept.get(destination, default):
                        chosen[name] = kept[name]
                elif not chosen.get(name):
                    chosen.pop(name, None)
        self.configure(settings)

    def enabled(self, plugin):
        return self.config['plugins'].get(plugin, {}).get('enabled', False)

    def pages(self):
        """The pages that can be opened, in the order of their buttons: {id: name}."""
        def rank(plugin):
            name = next((n for n, f in self.config['buttons'].items() if f == f'open:{plugin}'), None)
            x, y = spot(name) or (99, 0)
            return (y != 9, x)
        working = [p for p, run in self.running.items() if not run.stopped]
        return {p: self.plugins[p].name for p in sorted(working, key=rank)}

    def settle(self):
        """The open page is one that works (another one when it has stopped, none when there is none)."""
        if self.page not in self.running or self.running[self.page].stopped:
            self.page = next(iter(self.pages()), None)
            self.revision += 1
            if self.page:
                self.running[self.page].call('open')

    def function(self, x, y):
        """What the round button there does: ('power' | 'show' | 'open' | 'action', what)."""
        for name, does in self.config['buttons'].items():
            if spot(name) == (x, y):
                return ('open', does[5:]) if does.startswith('open:') else (does, None)
        for name, action in self.buttons().items():
            if spot(name) == (x, y):
                return ('action', action)
        return (None, None)

    def buttons(self, hosted=True):
        """The open page's round buttons, {name: action}: its own and (`hosted`) those of a plugin it hosts just now (a
        game being played), where the page has none itself."""
        tables = self.config['plugins']
        own = tables.get(self.page, {}).get('buttons', {})
        guest = self.running[self.page].call('hosted') if hosted and self.page else None
        return (tables.get(guest, {}).get('buttons', {}) if isinstance(guest, str) else {}) | own

    # ---- what the hardware tells it
    def motion(self, now):
        """The latest motion sample while it is fresh, or None."""
        sample = self.motion_sample
        return sample if sample is not None and 0 <= now - sample.at <= .25 else None

    def battery(self, now):
        sample = self.battery_sample
        return sample if sample is not None and 0 <= now - sample.at <= 45 else None

    def level(self, now):
        """The board lies flat on a table just now: what the motion sensor reads is kept as level (in config.toml,
        `tabletop_gravity`), so the tilt games start level however the board is held when they are opened."""
        sample = self.motion(now)
        if sample is None:
            return {'error': 'The motion sensor gives no reading just now.'}
        length = math.sqrt(sum(v * v for v in sample.game_orientation))
        x, y, z, w = (v / length for v in sample.game_orientation)
        self.configure(self.config | {'tabletop_gravity': [2 * (w * y - x * z), -2 * (w * x + y * z), 2 * (x * x + y * y) - 1]})

    def audio_deadline(self, now=None):
        """Service a musical wakeup between frames, or just read the next deadline after a frame."""
        studio = self.running.get('soundboard')
        if self.power and studio and not studio.plugin.own:
            if now is not None:
                self.now = now
            return studio.call('audio_deadline', now, self.page == 'soundboard')

    def tick(self, now):
        """Time passing: the pads go dark when left alone, a pickup wakes them."""
        self.now = now
        connected = self.motion_sample is not None  # The UART reader clears the sample when reports stop.
        if connected != self.motion_connected:
            self.motion_connected = connected
            self.revision += 1
        if self.last_activity is None:
            self.last_activity = now
        if self.power:
            for name, run in self.running.items():
                if name != self.page:
                    run.call('background', now)
        sleep = self.config['sleep']
        if self.power and sleep and now - self.last_activity > sleep * 60:
            self.set_power(False)
            self.wake_blocked_until = 0.
        if now < self.wake_blocked_until:
            self.pickup.reset()
            return
        if not self.pickup.update(self.motion(now)):
            return
        idle = now - self.last_activity
        self.set_power(True)
        self.welcome_at = now if idle >= 3600 else None
        self.last_activity = now
        self.shown_until = now + SHOW

    # ---- what can be done to it
    def set_power(self, power):
        """The pads and buttons lit or all dark; the board itself keeps running."""
        if self.power != power:
            self.pickup.reset()
            self.welcome_at = None
            self.wake_blocked_until = self.now + 2 if not power else 0.
            if not power:
                for run in self.running.values():
                    run.call('power_off', self.now)
            if self.page:
                self.running[self.page].call('open' if power else 'close')
            self.revision += 1
        self.power = power

    def open(self, page):
        if page in self.pages() and page != self.page:
            self.running[self.page].call('close')
            self.page = page
            self.running[page].call('open')
            self.revision += 1

    def set_volume(self, level):
        self.volume = min(5, max(1, int(level)))
        self.sound.volume(self.volume)
        self.revision += 1

    def act(self, action, now, held=False):
        """One of the open page's actions: from the management page, or (`held`) from its round button, which is
        let go later."""
        self.now = now
        if self.page:
            self.running[self.page].call('action', action, now)
            if not held:
                self.running[self.page].call('released', action, now)
            self.settle()
            self.revision += 1

    def press(self, x, y, velocity, now):
        """A pad or round button pressed."""
        self.now = self.last_activity = now
        self.welcome_at = None
        if not self.power:
            self.shown_until = now + SHOW
            return self.set_power(True)  # (any press wakes it, and does nothing else)
        if 1 <= x <= 8 and 1 <= y <= 8:
            if self.page:
                self.running[self.page].press(x - 1, y - 1, velocity, now)
                self.settle()
            return
        shown = self.shown_until is not None and self.shown_until > now
        self.shown_until = now + SHOW
        does, what = self.function(x, y)
        if does == 'power':
            self.set_power(False)
        elif does == 'show' and shown:
            self.shown_until = now  # (shown already: hidden)
        elif does == 'open':
            self.open(what)
        elif does == 'action':
            self.act(what, now, held=True)

    def release(self, x, y, now):
        """A pad let go, or a round button with one of the page's actions on it."""
        self.now = now
        if not self.power or not self.page:
            return
        if 1 <= x <= 8 and 1 <= y <= 8:
            self.running[self.page].release(x - 1, y - 1, now)
        else:
            does, what = self.function(x, y)
            if does == 'action':
                self.running[self.page].call('released', what, now)
        self.settle()

    def request(self, plugin, name, params, body, on_board=lambda finish: finish()):
        """Something the management page asks of a page plugin (its uploads, its drawings); runs in the web server's
        thread, so a slow one (converting a clip) doesn't hold the pads up."""
        run = self.running.get(plugin)
        if not run or run.stopped or not hasattr(run.instance, 'request'):
            return None
        def handle():
            result = run.instance.request(name, params, body)
            if isinstance(result, AbstractContextManager):
                # Unpack on the web worker; only the final layout change runs in the board loop.
                with result as finish:
                    return on_board(lambda: finish() if self.running.get(plugin) is run and not run.stopped else
                                    {'error': 'The page changed while the files were being prepared. Try again.'})
            return result
        result, error = guarded(handle)
        self.revision += 1
        if error:  # (a request it cannot read is the page's mistake: the plugin is not stopped for it)
            return {'error': f'{run.plugin.name} could not do that ({type(error).__name__}: {error}).'}
        return result

    # ---- what it shows
    def power_colour(self, now):
        sample = self.battery(now)
        if sample is None:
            return (90, 90, 90)
        if sample.plugged:
            return (0, 255, 60) if sample.percent >= 80 else (0, 180, 255)
        if sample.percent <= 15:  # (pulsing: the battery driver shuts the Pi down at 8)
            return (155 + 100 * math.sin(now * math.pi), 0, 0)
        if sample.percent <= 25:
            return (255, 0, 0)
        if sample.percent <= 50:
            return (255, 100, 0)
        if sample.percent <= 75:
            return (100, 255, 60)
        return (0, 110, 25)

    def frame(self, now):
        frame = [BLACK] * 100
        if not self.power:
            return frame
        if self.shown_until is None:
            self.shown_until = now + SHOW
        brightness = self.config['brightness'] / 100
        page = self.running.get(self.page)
        picture = page.frame(now) if page else BLANK
        if page and page.stopped:
            self.settle()
            page = self.running.get(self.page)
        for i, colour in enumerate(picture):
            frame[10 * (i // 8 + 1) + i % 8 + 1] = dim(colour, brightness)
        lit = .25 + .75 * brightness  # (the buttons stay findable when it's dim)
        seen = lit * min(1., max(0., self.shown_until - now))  # (fading out over the last second)
        stay = lit if page and (self.plugins[self.page].about.get('buttons_stay_lit') or page.call('stay_lit', now)) else seen
        own = self.buttons(hosted=False)
        if stay:
            lights = page.call('lights', now) if page else None
            lights = lights if isinstance(lights, dict) else {}
            for name, action in own.items():
                if xy := spot(name):
                    x, y = xy
                    frame[10 * y + x] = dim(triple(lights.get(action)) or (70, 70, 70), stay)
        if page:  # (a game's own buttons: lit all the while it is played, the ones it lights)
            guest = {name: action for name, action in self.buttons().items() if name not in own and spot(name)}
            lights = (lights if stay else page.call('lights', now)) if guest else None
            for name, action in guest.items():
                x, y = spot(name)
                frame[10 * y + x] = dim(triple(lights.get(action) if isinstance(lights, dict) else None) or BLACK, lit)
        for name, does in self.config['buttons'].items():
            xy = spot(name)
            if not xy:
                continue
            x, y = xy
            if does == 'power':
                colour = dim(self.power_colour(now), lit)
            elif does == 'show':
                colour = dim((255, 110, 0), lit)
            elif does.startswith('open:') and (target := self.running.get(does[5:])) and not target.stopped:
                colour = dim(target.plugin.colour, seen * (1 if target is page else .12))
            else:
                colour = BLACK
            frame[10 * y + x] = colour
        if self.welcome_at is not None:
            progress = (now - self.welcome_at) / .6
            if 0 <= progress < 1:
                radius = 5.7 * (1 - (1 - progress)**3)
                colour = dim(self.power_colour(now), brightness)
                for y in range(1, 9):
                    for x in range(1, 9):
                        distance = math.hypot(x - 4.5, y - 4.5)
                        reveal = min(1., max(0., radius - distance + 1))
                        glow = max(0., 1 - abs(distance - radius)) * (1 - progress)
                        index = 10 * y + x
                        frame[index] = tuple(c * reveal * (1 - glow) + g * glow for c, g in zip(frame[index], colour))
            else:
                self.welcome_at = None
        return frame

    @cached_property
    def public_config(self):
        shown = copy.deepcopy({k: v for k, v in self.config.items() if k != 'login'})
        if isinstance(shown.get('mqtt'), dict):
            shown['mqtt'].pop('password', None)
        for plugin, table in list(shown['plugins'].items()):
            if plugin not in self.plugins:
                del shown['plugins'][plugin]
            elif 'settings' in table:
                fields = self.plugins[plugin].about.get('settings', {})
                table['settings'] = {key: value for key, value in table['settings'].items()
                                     if key in fields and fields[key].get('type') != 'secret'}
        return shown

    def state(self, catalog_page=None, *, config_version=None, catalog_version=None, share_config=False):
        """Omit other item catalogs when refreshing the same page during a performance."""
        full_catalog = not catalog_page or catalog_page != self.page
        battery = self.battery(self.now)
        broker = self.config.get('mqtt')
        if not isinstance(broker, dict):
            broker = {}
        if not hasattr(self, 'active_mqtt'):
            mqtt_status = 'Saved. MQTT runs on the Pi.' if broker.get('host') else 'No broker configured.'
        elif self.active_mqtt != (broker if broker.get('host') else None):
            mqtt_status = 'Settings saved. Restart the board to apply them.'
        elif broker.get('host'):
            mqtt_status = self.mqtt_connection
        else:
            mqtt_status = 'No broker configured.'

        def about(plugin):
            run = self.running.get(plugin.id)
            live = run and not run.stopped

            def ask(what, kind):  # (what the plugin says about itself, if it is the right kind of thing)
                said = run.call(what)
                return said if isinstance(said, kind) else kind()
            static = {k: plugin.about[k] for k in ('name', 'kind', 'colour', 'version', 'about', 'needs', 'thing', 'things',
                                                   'whole', 'banks', 'holds', 'settings', 'pad', 'pad_actions') if k in plugin.about}
            panel = plugin.folder / 'panel.js'  # (its own part of the management page, if it brings one: said with its age, so a new one is fetched)
            return static | {
                'own': plugin.own, 'enabled': self.enabled(plugin.id), 'error': plugin.error, 'panel': panel.stat().st_mtime_ns if panel.is_file() else None,
                'secrets': [name for name in self.secrets(plugin.id) if self.config['plugins'].get(plugin.id, {}).get('settings', {}).get(name)],
                'actions': plugin.about.get('actions', {}) | (ask('actions', dict) if live else {}),
                'items': ask('things', list) if live and (full_catalog or plugin.id == self.page) else [], 'state': ask('state', dict) if live else {},
            }
        covered = [{'button': name, 'does': does, 'plugin': plugin, 'action': table['buttons'][name]}
                   for name, does in self.config['buttons'].items() for plugin, table in self.config['plugins'].items()
                   if plugin in self.plugins and self.enabled(plugin) and name in table.get('buttons', {})]
        state = {
            'name': NAME, 'version': VERSION, 'api': API, 'revision': self.revision, 'config_version': self.config_version, 'covered': covered,
            'page': self.page, 'pages': list(self.pages()), 'power': self.power, 'volume': self.volume,
            'battery': {'percent': battery.percent, 'plugged': battery.plugged} if battery else None,
            'parts': self.parts | {'Motion sensor': 'found' if self.motion_connected else 'not found',
                                   'Battery board': f'found, {battery.percent} %' if battery else 'not found'},
            'locked': config.credentials(self.home)[1], 'password': config.owner(self.home) is not None,
            'broker_password': bool(broker.get('password')),
            'mqtt_status': mqtt_status,
            'plugins': {plugin.id: about(plugin) for plugin in self.plugins.values()},
        }
        if self.config_version != config_version:
            state['config'] = self.public_config if share_config else copy.deepcopy(self.public_config)
        if catalog_version and catalog_page == self.page:
            current = state['plugins'].get(catalog_page)
            if current and current['state'].get('catalog_version') == catalog_version:
                del current['items']
        return state
