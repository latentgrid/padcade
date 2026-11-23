"""The Soundboard: a clip on every pad the owner likes, on as many banks of 64 pads as they need. config.toml says
which clip is on which pad (its colour, how it plays); the clips themselves are WAV files in the plugin's data
folder, uploaded on the management page and converted there to what the mixer plays."""
import hashlib
import json
import math
import re
import secrets
import shutil
import tempfile
import threading
import tomllib
import wave
from array import array
from copy import deepcopy
from pathlib import Path

from padcade import synth
from padcade.sound_banks import convert, prepare_bank
from padcade import song_import
from padcade.song_samples import SampleStore
from padcade.song_studio import MAX_BPM, MIN_BPM, Studio, release_note, release_seconds
from padcade.kit import BLACK, WHITE, mix
from padcade.plugins import colour as rgb

VOICES = 24  # clips sounding at once: 16% of one core on a Pi 3A+ when all of them sound
PALETTE = ('#ff5a5a', '#ff9838', '#ffd23f', '#5fe07a', '#3fd0ff', '#5b7bff', '#a070ff', '#ff5fc0')
SPEAKER = ('...#....', '..##.#..', '####..#.', '####..#.', '..##.#..', '...#....')


def tint(name):
    """A clip's own colour until the owner picks one: always the same for the same name."""
    return PALETTE[sum(name.encode()) % len(PALETTE)]


TRACKS = tuple(map(rgb, PALETTE))
AMBER = (255, 160, 35)
READY = (82, 68, 108)
TIMING_LIGHT = {'As played':.12, 'Gentle':.34, 'Fine':.62, 'Tight':1}
HOLD = .45
BANKS_PER_PAGE = 21  # Three horizontal pairs per row, seven rows plus navigation.


class RecordedVoice:
    """A voice started by another page, which may silence it before its WAV ends."""
    def __init__(self, voice, cut):
        self.voice, self.cut = voice, cut

    def __getattr__(self, name):
        return getattr(self.voice, name)

    def terminate(self):
        if self.voice.poll() is None:
            self.cut()
        self.voice.terminate()


class Plugin:
    board_requests = ('studio', 'project', 'take-project', 'delete')
    read_requests = ('clip', 'project', 'take-project')

    def __init__(self, board):
        self.board, self.clips = board, board.data / 'clips'
        with (board.folder / 'plugin.toml').open('rb') as source:
            factory = tomllib.load(source)['defaults']['banks']
        self.group_order = ['My clips', *(bank['name'] for bank in factory)]
        self.clip_groups = {'Hat': 'Drums', 'Tom': 'Drums'}  # Earlier factory sounds remain in existing libraries.
        for note in synth.NOTES:
            for voice, octave in (('Bass', 2), ('Bass', 3), ('Pluck', 4), ('Pluck', 5)):
                self.clip_groups[f'{voice} {note}{octave}'] = 'Synth'
        for bank in factory:
            for pad in bank['pads'].values():
                self.clip_groups.setdefault(pad['thing'], bank['name'])
        # Prior Synth/Keys layouts still name these instruments in recordings and custom pad maps.
        for octave in range(2, 6):
            for note in synth.CHROMATIC:
                self.clip_groups.setdefault(f'Bright pluck {note}{octave}', 'Synth')
        for note in ('C2', 'D2', 'E2', 'F2', 'G2', 'A2', 'B2', 'C3'):
            for voice in ('Round bass ', 'Plucked bass ', 'Finger bass '):
                self.clip_groups[voice + note] = 'Synth'
        for name in synth.RECIPES:
            if name.startswith(('Round bass ', 'Soft keys ', 'Warm pad ', 'Chord ')):
                self.clip_groups.setdefault(name, 'Synth')
        # Older songs reference this factory WAV by name. Keep it playable and hide only that recording.
        old_clip = self.clips / 'Coin.wav'
        try:
            with old_clip.open('rb') as source:
                self.hide_factory_coin = hashlib.sha256(source.read(128 * 1024)).hexdigest() == (
                    '3506d8b05c0d23044bacde5c472b4ef7e346429f15ad8eef659710342a987353')
        except FileNotFoundError:
            self.hide_factory_coin = False
        self.bank, self.applied = 0, False
        self.drawn_pads = self.catalog_banks = self.catalog_version = None
        self.voices = []  # [bank, pad, clip, voice, mode, gain] of what is sounding, oldest first
        self.clip_lock = threading.RLock()
        self.samples = SampleStore(self.clips)
        self.studio = Studio(board.data, lambda name, gain: self.start(None, {'thing': name}, gain), lambda: self.hush(list(self.voices)),
                             self.metronome, self.samples.flush)
        self.picker = self.shift_at = None
        self.shift_used = False
        self.tempo_hold = None
        self.pressed_tracks, self.pressed_pads = {}, {}
        self.arm_since = self.track_modifier = None
        self.take_page = self.bank_page = 0
        self.last_status = None
        self.has_private_samples = any(self.clips.glob('Song sample *.wav'))
        self.sample_revision, self.sweeper = -1, None
        self.seed_lock, self.stopped = threading.Lock(), False
        self.resume = False  # the loop was playing when another page was opened
        self.prepare_after_seed = False
        pending = self.clips.parent / '.first-sounds'
        names = None
        if self.clips.is_dir() and not pending.is_dir():
            chromatic = synth.CHROMATIC_FACTORY_CLIPS | synth.HIGH_CHORD_CLIPS
            grid = synth.SYNTH_GRID_CLIPS | chromatic
            restored = ((synth.JAM_FACTORY_CLIPS - {'Classic kick'}) | synth.RESTORED_EFFECT_CLIPS
                        | synth.LEGACY_PLUCK_CLIPS | synth.LEGACY_INSTRUMENT_CLIPS | grid)
            studio_bass = synth.STUDIO_BASS_CLIPS | restored
            tuned = synth.TUNED_KICK_CLIPS | synth.FINGER_BASS_CLIPS | studio_bass
            electronic = synth.ELECTRONIC_DRUM_CLIPS | synth.NEW_BASS_CLIPS | tuned
            rhythm = synth.DRUM_FACTORY_CLIPS | synth.SCALE_CHORD_CLIPS | electronic
            chords = synth.CHORD_FACTORY_CLIPS | rhythm
            new_instruments = synth.PIANO_FACTORY_CLIPS | synth.DIATONIC_FACTORY_CLIPS | chords
            for marker, names in (('.add-pack-sounds', synth.NEW_FACTORY_CLIPS | restored),
                                  ('.expand-pack-sounds', synth.EXPANDED_FACTORY_CLIPS | new_instruments),
                                  ('.add-keys-sounds', new_instruments), ('.add-chromatic-sounds', new_instruments),
                                  ('.add-piano-sounds', new_instruments), ('.add-chord-sounds', chords),
                                  ('.add-rhythm-sounds', rhythm), ('.add-electronic-sounds', electronic),
                                  ('.add-tuned-sounds', tuned), ('.add-studio-bass-sounds', studio_bass),
                                  ('.add-jam-sounds', restored), ('.add-synth-grid-sounds', grid),
                                  ('.add-chromatic-bank-sounds', chromatic), ('.add-high-chord-sounds', synth.HIGH_CHORD_CLIPS)):
                pending = self.clips.parent / marker
                if pending.is_dir():
                    break
        if not self.clips.exists() or pending.is_dir():
            pending.mkdir(exist_ok=True)  # A restart resumes until all defaults have been published.
            self.clips.mkdir(exist_ok=True)
            threading.Thread(target=self.first_sounds, args=(pending, names), daemon=True).start()

    def first_sounds(self, pending, names):
        try:
            source = self.board.folder / 'sounds'
            needed = dict.fromkeys(synth.factory_names(source) if names is None else sorted(names))
            def next_clips():
                while needed and not self.stopped:
                    banks = self.banks()
                    pads = banks[min(self.bank, len(banks) - 1)].get('pads', {})
                    playable = (p['thing'] for p in pads.values() if isinstance(p, dict) and isinstance(p.get('thing'), str))
                    name = next((n for n in playable if n in needed), next(iter(needed)))
                    del needed[name]
                    if not (self.clips / (name + '.wav')).exists():
                        yield name

            for name, rate, pcm in synth.background_factory_clips(source, next_clips()):
                clip = self.clips / f'{name}.wav'
                with self.seed_lock:
                    if self.stopped:
                        return
                    if clip.exists():
                        continue
                    with tempfile.NamedTemporaryFile(dir=pending, delete=False) as complete:
                        with wave.open(complete, 'wb') as wav:
                            wav.setparams((2, 2, rate, 0, 'NONE', ''))
                            wav.writeframes(pcm)
                    try:
                        clip.hardlink_to(complete.name)
                    except FileExistsError:
                        pass  # An upload that arrived during generation wins.
                    finally:
                        Path(complete.name).unlink(missing_ok=True)
            with self.seed_lock:
                if not self.stopped:
                    shutil.rmtree(pending)
                    self.prepare_after_seed = True
                    self.board.changed()
        except FileNotFoundError:
            pass  # Its home went away while preparing the sounds.

    # ---- what the description promises
    def banks(self):
        banks = self.board.config.get('banks') or [{'name': 'Bank 1', 'pads': {}}]
        self.bank = min(self.bank, len(banks) - 1)  # (the open bank may just have been removed)
        return banks

    def bank_order(self):
        # Present the related instruments together while bank:N bindings keep their original indices.
        banks = self.banks()
        family = [i for i, bank in enumerate(banks) if bank.get('family') == 'Synth']
        order = list(range(len(banks)))
        if family:
            order = [i for i in order if i not in family]
            order[family[0]:family[0]] = family
        return order

    def pads(self):
        return self.banks()[self.bank].get('pads', {})

    def sounding(self):
        self.voices = [v for v in self.voices if v[3].poll() is None or v[4] == 'loop' and v[3].poll() == 0]
        return self.voices

    def metronome(self, counting):
        clip = self.clips.parent / ('count-in.wav' if counting else 'metronome.wav')
        if not clip.exists():
            pitch = 2000 if counting else 1200
            samples = synth.tone(.045, lambda t: pitch, decay=80)
            part = clip.with_suffix('.part')
            with wave.open(str(part), 'wb') as wav:
                wav.setparams((2, 2, synth.RATE, 0, 'NONE', ''))
                wav.writeframes(array('h', (int(s*14000) for s in samples for _ in range(2))).tobytes())
            part.replace(clip)
        self.play(clip)

    def start(self, pad, to, gain=1.):
        name = to['thing']
        if isinstance(name, str) and Path(name).name == name:
            clip = self.clips / f'{name}.wav'
            if self.captured(name) and not clip.is_file():
                try:
                    self.samples.flush(name)
                except OSError:
                    return
            if clip.is_file():
                return self.play(clip, pad, to.get('mode', 'once'), gain)

    def play(self, clip, pad=None, mode='once', gain=1.):
        if not self.applied:
            self.board.set_volume(self.board.settings['volume'])
            self.applied = True
        try:
            voice = self.board.play(clip, gain)
            self.voices.append([self.bank, pad, clip, voice, mode, gain])
        except OSError:
            return  # (no speaker: nothing to hear)
        while len(self.voices) > VOICES:
            # The quietest of the others gives way. A speaker that cannot say how loud a voice still is (the
            # simulator's browser) loses its oldest.
            self.hush([min(self.voices[:-1], key=lambda v: getattr(v[3], 'remaining_peak', float)())])
        return voice

    def hush(self, voices):
        for voice in voices:
            self.studio.cut_voice(voice[3], max(self.studio.now, self.board.now))
            voice[3].terminate()
            self.voices.remove(voice)

    def finish_tempo_hold(self):
        if self.tempo_hold:
            self.tempo_hold = None
            self.studio.save()  # One disk write for the whole gesture.

    def audio_deadline(self, now, foreground):
        with self.clip_lock:
            if now is not None:
                self.studio.tick(now, foreground=foreground)
            return self.studio.next_deadline(foreground)

    def background(self, now, foreground=False):
        for key, since in list(self.pressed_tracks.items()):
            if now - since >= HOLD:
                del self.pressed_tracks[key]
                self.command('record_' + key, now)
        with self.clip_lock:
            self.studio.tick(now, foreground=foreground)
            if (self.has_private_samples and self.sample_revision != self.studio.saved_revision
                    and self.studio.storage_safe and self.studio.performance is None and self.studio.record_at is None
                    and not (self.sweeper and self.sweeper.is_alive()) and not self.stopped):
                self.sample_revision = self.studio.saved_revision
                self.sweeper = threading.Thread(target=self.sweep_samples, args=(self.sample_revision,), daemon=True)
                self.sweeper.start()
        if self.tempo_hold:
            action, slot, repeat_at = self.tempo_hold
            if slot != self.studio.slot or not self.available(action):
                self.finish_tempo_hold()
            elif now >= repeat_at:
                self.command(action, now, persist=False)
                self.tempo_hold = (action, slot, now + .1)
        if self.arm_since is not None and now - self.arm_since >= HOLD:
            self.arm_since = None
            self.command('takes', now)
        status = (self.studio.slot, self.studio.playing, self.studio.record_at, self.studio.count_in(now),
                  self.studio.recording(now), self.studio.save_error, self.studio.take_playing, self.studio.performance is not None)
        if status != self.last_status:
            self.last_status = status
            self.board.changed()

    def frame(self, now):
        if self.prepare_after_seed:
            self.prepare_bank()
        self.background(now, foreground=True)
        for voice in self.sounding():
            if voice[3].poll() == 0:  # (a loop that ended: again)
                # ponytail: the loop restarts a frame late (a gap of up to 20 ms); a looping voice in the mixer would close it
                voice[3] = self.board.play(voice[2], voice[5])
                self.studio.performance_hit(voice[2].stem, voice[5], now, voice[3])
        pads = self.pads()
        if self.picker or self.studio.mode != 'play':
            choices = self.take_choices()
            order = self.bank_order() if self.picker == 'banks' else None
            return [self.lamp(self.choice(x, y, choices, order), now) for y in range(8) for x in range(8)]
        if not pads:  # (nothing to play yet: a speaker, breathing)
            picture = [BLACK] * 64
            glow = .25 + .15 * math.sin(now * 2)
            for r, row in enumerate(SPEAKER):
                for x, c in enumerate(row):
                    if c == '#':
                        picture[8 * (6 - r) + x] = mix(BLACK, rgb('#5fe07a'), glow)
            return picture
        if pads is not self.drawn_pads:
            # Board.configure replaces the pad map on every edit or bank import.
            self.resting = [BLACK] * 64
            self.lit_pads, self.action_pads = {}, {}
            for pad, to in pads.items():
                x, y = (int(n) - 1 for n in pad.split(','))
                index = 8*y + x
                if 'action' in to:
                    self.action_pads[index] = to['action']
                elif 'thing' in to:
                    colour = rgb(to.get('colour') or tint(to['thing']))
                    self.resting[index] = mix(BLACK, colour, .3)
                    self.lit_pads[pad] = index, mix(colour, WHITE, .6)
            self.drawn_pads = pads
        picture = self.resting.copy()
        for bank, pad, *_ in self.voices:
            if bank == self.bank and pad in self.lit_pads:
                index, colour = self.lit_pads[pad]
                picture[index] = colour
        for index, action in self.action_pads.items():
            picture[index] = self.lamp(action, now)
        return picture

    def press(self, x, y, velocity, now):
        pad = f'{x + 1},{y + 1}'
        if self.picker or self.studio.mode != 'play':
            action = self.choice(x, y)
            if action in ('tempo_up', 'tempo_down'):
                if action == self.pressed_pads.get(pad):
                    return
                self.finish_tempo_hold()
                if self.command(action, now, persist=False):
                    self.pressed_pads[pad] = action
                    self.tempo_hold = (action, self.studio.slot, now + HOLD)
            else:
                self.command(action, now)
            return
        to = self.pads().get(pad)
        if to and 'action' in to:
            self.pressed_pads[pad] = to['action']
            self.action(to['action'], now)
            return
        name = to.get('thing') if to else None
        if not isinstance(name, str):
            return
        looping = [v for v in self.sounding() if v[:2] == [self.bank, pad] and v[4] == 'loop']
        if looping:
            self.hush(looping)  # (pressed again: enough)
        else:
            with self.clip_lock:
                dynamics = to.get('dynamics', 'auto')
                responds = dynamics == 'velocity' or dynamics == 'auto' and self.clip_groups.get(name) in ('Drums', 'Synth', 'Synth high', 'All chords', 'Keys', 'Jam classics')
                gain = velocity / 127 if responds and self.board.settings['pressure_sensitive'] else 1.
                voice = self.start(pad, to, gain)
                if voice and self.studio.hit(name, now, pad if to.get('mode') == 'hold' else None, gain, voice=voice):
                    self.board.changed()

    def release(self, x, y, now):
        pad = f'{x+1},{y+1}'
        with self.clip_lock:
            self.studio.release_hit(pad, now)
        action = self.pressed_pads.pop(pad, None)
        if action:
            self.released(action, now)
        for playing in self.sounding():
            if playing[1] == pad and playing[4] == 'hold':
                if self.studio.pedal_since is not None and playing[2].stem in synth.PEDAL_CLIPS:
                    playing[4] = 'sustained'
                else:
                    release_note(playing[2].stem, playing[3])
                playing[1] = None  # Stop still owns the tail, the released pad no longer does.

    def take_choices(self):
        if self.studio.mode not in ('takes', 'delete-take'):
            return {}
        items = self.studio.take_items()
        paged = len(items) > 64
        size = 56 if paged else 64
        self.take_page = min(self.take_page, max(0, (len(items) - 1) // size))
        start = self.take_page * size
        choices = {(i % 8, 7 - i // 8): ('take:' + take['id'], take['title'])
                   for i, take in enumerate(items[start:start + size])}
        if start:
            choices[0, 0] = ('takes_prev', 'Previous')
        if start + size < len(items):
            choices[7, 0] = ('takes_next', 'Next')
        return choices

    def choice(self, x, y, take_choices=None, bank_order=None):
        if self.studio.mode in ('takes', 'delete-take'):
            choices = self.take_choices() if take_choices is None else take_choices
            return choices.get((x, y), ('', ''))[0]
        row = 7-y
        if self.picker == 'banks':
            if x == 7 and row < 2:
                return 'tempo_up' if row == 0 else 'tempo_down'
            if row == 7:
                if len(self.banks()) > BANKS_PER_PAGE:
                    if x < 2:
                        return 'banks_prev'
                    if 4 <= x < 6:
                        return 'banks_next'
                return ''
            index = self.bank_page * BANKS_PER_PAGE + row * 3 + x // 2
            order = self.bank_order() if bank_order is None else bank_order
            return f'bank:{order[index]}' if x < 6 and index < len(order) else ''
        if self.picker == 'volume':
            # Five ascending steps; the higher the level, the longer its row.
            return f'level:{y}' if 1 <= y <= 5 and 1 <= x <= y+1 else ''
        if self.studio.mode == 'delete-track':
            return f'track:{row}'
        return f'slot:{4*(row//2)+x//2}'

    def available(self, name):
        if not isinstance(name, str):
            return False
        mode = self.studio.mode
        if name in ('takes_next', 'takes_prev'):
            return any(action == name for action, title in self.take_choices().values())
        if self.picker and name == 'performance' and self.studio.performance is not None:
            return True
        if self.picker:
            if name == 'click':
                return self.studio.available(name)
            if name in ('record', 'play'):
                return self.studio.available(name) and (name == 'play' or self.studio.record_at is not None)
            if name in ('tempo_up', 'tempo_down'):
                if self.picker != 'banks' or not self.studio.can_set_tempo():
                    return False
                bpm = self.studio.song['bpm']
                return bpm < MAX_BPM if name == 'tempo_up' else bpm > MIN_BPM
            if name == self.picker:
                return True
            if self.picker == 'volume':
                return name in ('level:1', 'level:2', 'level:3', 'level:4', 'level:5')
            if name == 'banks_prev':
                return self.bank_page > 0
            if name == 'banks_next':
                return (self.bank_page+1)*BANKS_PER_PAGE < len(self.banks())
            if not name.startswith('bank:'):
                return False
        if name.startswith('bank:'):
            arg = name[5:]
            return mode == 'play' and arg.isascii() and arg.isdecimal() and len(arg) <= 6 and int(arg) < len(self.banks())
        if name == 'sustain':
            return mode == 'play' and (self.studio.pedal_since is not None or any(
                pad.get('mode') == 'hold' and pad.get('thing') in synth.PEDAL_CLIPS for pad in self.pads().values()))
        if name in ('volume_up', 'volume_down', 'next_bank', 'stop', 'banks', 'volume'):
            return mode == 'play'
        return self.studio.available(name)

    def command(self, name, now, *, persist=True):
        if name in ('tempo_up', 'tempo_down'):
            with self.clip_lock:
                self.studio.tick(now)
        if not self.available(name):
            return False
        if name in ('tempo_up', 'tempo_down'):
            step = 1 if name == 'tempo_up' else -1
            self.studio.edit('bpm', self.studio.song['bpm'] + step, persist=persist)
            self.shift_used = True
        elif name in ('volume_up', 'volume_down'):
            self.board.set_volume(self.board.volume + (1 if name == 'volume_up' else -1))
            self.applied = True
        elif name == 'stop':
            with self.clip_lock:
                self.studio.stop(now)
        elif name in ('banks', 'volume'):
            self.finish_tempo_hold()
            self.picker = None if self.picker == name else name
            self.bank_page = self.bank_order().index(self.bank) // BANKS_PER_PAGE
        elif name in ('takes_next', 'takes_prev'):
            self.take_page += 1 if name == 'takes_next' else -1
        elif name in ('banks_prev', 'banks_next'):
            self.bank_page += 1 if name == 'banks_next' else -1
        elif name.startswith('level:'):
            self.board.set_volume(int(name[6:]))
            self.applied, self.picker = True, None
        elif name == 'next_bank':
            order = self.bank_order()
            self.bank = order[(order.index(self.bank) + 1) % len(order)]
            self.prepare_bank()
        elif name.startswith('bank:'):
            self.finish_tempo_hold()
            self.bank = int(name[5:])
            self.prepare_bank()
            self.shift_used = True
            if self.shift_at is None:
                self.picker = None
        else:
            with self.clip_lock:
                if not self.studio.action(name, now):
                    self.board.changed()
                    return False
            if name == 'takes':
                self.take_page = 0
        self.board.changed()
        return True

    def action(self, name, now):
        if self.track_modifier:
            modifier, slot, _ = self.track_modifier
            if name == modifier:
                return
            if name.startswith('track:'):
                self.track_modifier = (modifier, slot, None)  # An ineligible target still consumes the chord.
                if self.can_name_track() and self.studio.slot == slot:
                    self.command(modifier + '_' + name, now)
                return
            self.track_modifier = None
        if name in ('quantise', 'undo') and self.can_name_track():
            self.pressed_tracks.clear()
            self.track_modifier = (name, self.studio.slot, now)
            self.board.changed()
            return
        if name == 'performance':
            if self.arm_since is None and (self.available('performance') or self.available('takes')):
                self.arm_since = now
            return
        if name == 'sustain':
            if self.studio.pedal_since is None and self.available(name):
                self.studio.pedal_since = now
                self.board.changed()
            return
        if name.startswith('track:') and self.studio.mode == 'play':
            # A tap mutes or unmutes what is recorded there, a hold records: which it is shows on release.
            if self.available('tap_' + name) or self.available('record_' + name):
                self.pressed_tracks.setdefault(name, now)
            return
        if name == 'banks':
            self.shift_at, self.shift_used = now, False
        self.command(name, now)

    def released(self, name, now):
        if self.track_modifier and self.track_modifier[0] == name:
            _, slot, since = self.track_modifier
            self.track_modifier = None
            # (Undo tapped by itself takes back a queued song or brings back what was deleted; Quantise by itself does nothing)
            if since is not None and now - since < HOLD and self.can_name_track() and self.studio.slot == slot:
                self.command(name, now)
            self.board.changed()
            return
        if name == 'performance' and self.arm_since is not None:
            held = now - self.arm_since >= HOLD
            self.arm_since = None
            self.command('takes' if held or self.studio.mode in ('takes', 'delete-take') else 'performance', now)
            return
        if self.tempo_hold and self.tempo_hold[0] == name:
            self.finish_tempo_hold()
            return
        if name == 'sustain' and self.studio.pedal_since is not None:
            with self.clip_lock:
                self.studio.finish_sustain(now)
                self.studio.pedal_since = None
            for playing in self.sounding():
                if playing[4] == 'sustained':
                    release_note(playing[2].stem, playing[3])
                    playing[4] = 'hold'
            self.board.changed()
            return
        if name in self.pressed_tracks:
            elapsed = now - self.pressed_tracks.pop(name)
            self.command(('record_' if elapsed >= HOLD else 'tap_') + name, now)
        if name == 'banks' and self.shift_at is not None:
            if self.picker == 'banks' and (self.shift_used or now-self.shift_at >= HOLD):
                self.finish_tempo_hold()
                self.picker = None
            self.shift_at = None
            self.board.changed()

    def can_name_track(self):
        """Quantise or Undo can be held and a track tapped for it."""
        return not self.picker and self.studio.mode == 'play' and self.studio.record_at is None

    def actions(self):
        return {f'bank:{k}': f'Bank {k+1}: {bank["name"]}' for k, bank in enumerate(self.banks())} | {
            f'track:{i}': f'Track {i+1}' for i in range(8)} | {f'mute:{i}': f'Mute track {i+1}' for i in range(8)}

    def lamp(self, name, now):
        if self.studio.mode == 'duplicate' and self.studio.target is not None and name == f'slot:{self.studio.target}':
            return TRACKS[self.studio.target % 8]
        if name in ('performance', 'takes') and self.studio.mode in ('takes', 'delete-take'):
            return WHITE
        if self.track_modifier and self.can_name_track() and self.track_modifier[1] == self.studio.slot:
            modifier = self.track_modifier[0]
            if name.startswith('track:'):
                if not self.available(modifier + '_' + name):
                    return BLACK
                i = int(name[6:])
                timing = self.studio.song['tracks'][i]['timing']
                return mix(BLACK, TRACKS[i], TIMING_LIGHT[timing] if modifier == 'quantise' else 1)
            if name == modifier and self.available(name):
                return WHITE
        if name in ('undo', 'quantise') and not self.available(name) and self.can_name_track():
            if any(self.available(f'{name}_track:{i}') for i in range(8)):
                return WHITE if self.track_modifier and self.track_modifier[0] == name else READY
        if not (self.available(name) or (name.startswith('track:') and self.available('tap_' + name))):
            return BLACK
        studio = self.studio
        cmd, _, arg = name.partition(':')
        pulse = .24 + .76*(1+math.sin(now*6))/2
        if name == 'takes':
            return (38, 143, 123) if studio.song.get('takes') else READY
        if name == 'performance':
            if studio.performance is not None:
                elapsed = now - studio.performance.start
                return mix(BLACK, (255, 35, 45), .65 + .35 * math.cos(elapsed * 4))
            return mix(BLACK, (255, 45, 70), .6)
        if cmd == 'take':
            if studio.mode == 'delete-take':
                return mix(BLACK, AMBER, 1 if studio.target == arg else pulse if studio.target is None else .18)
            return mix(BLACK, (70, 220, 185), (pulse if studio.take_playing else 1) if studio.selected_take == arg else .3)
        if cmd == 'level':
            if int(arg) == self.board.volume:
                return WHITE
            return (44, 155, 136) if int(arg) < self.board.volume else (17, 55, 49)
        if cmd == 'bank':
            i = int(arg)
            return mix(BLACK, TRACKS[i%8], 1 if i == self.bank else .25)
        if cmd in ('track', 'slot'):
            i = int(arg)
            colour = TRACKS[i%8]
            if studio.mode.startswith('delete'):
                return mix(BLACK, AMBER, 1 if studio.target == i else pulse if studio.target is None else .18)
            if cmd == 'slot':
                if studio.mode == 'duplicate':
                    return mix(BLACK, colour, 1 if studio.target == i else pulse)
                return mix(BLACK, colour, pulse if studio.queued == i else 1 if studio.slot == i else .55 if studio.slots[i] else .04)
            track = studio.song['tracks'][i]
            if i == studio.recording_on and studio.record_at is not None:
                if studio.count_in(now) is not None:
                    return mix(BLACK, AMBER, 1 if studio.beat(now) % 1 < .35 else .18)
                return mix(BLACK, colour, .28 if studio.recording(now) and studio.beat(now) % 1 >= .5 else 1)
            if not track['events']:
                return BLACK
            return mix(BLACK, colour, .13 if track['muted'] else .58)
        if name == 'sustain':
            return WHITE if studio.pedal_since is not None else READY
        if name == 'delete' and studio.mode.startswith('delete'):
            return mix(BLACK, AMBER, pulse if studio.target is not None else 1)
        if name == 'record':
            brightness = .6
            if studio.record_at is not None:
                counting = studio.count_in(now) is not None
                bright = studio.recording(now) or (counting and studio.beat(now) % 1 < .35)
                brightness = 1 if bright else .18
            return mix(BLACK, (255, 45, 70), brightness)
        if name in ('tempo_up', 'tempo_down') or name == 'click' and studio.click:
            beat = studio.metronome_beat(now)
            return WHITE if beat % 1 < .2 else READY
        if name == 'play':
            return (60, 255, 130) if (studio.take_playing if studio.mode in ('takes', 'delete-take') else studio.playing) else (30, 92, 55)
        if name == 'stop':
            return (255, 0, 0) if self.voices else (90, 0, 0)
        if name in ('volume_up', 'volume_down'):
            return mix(BLACK, WHITE, .2+.16*self.board.volume if name=='volume_up' else 1-.16*self.board.volume)
        if name == self.picker or name == 'songs' and studio.mode=='songs' or name=='duplicate' and studio.mode=='duplicate':
            return WHITE
        return READY

    def lights(self, now):
        return {action: self.lamp(action, now) for action in set(self.board.config.get('buttons', {}).values())}

    def power_off(self, now):
        self.resume = False  # (a loop that waited on another page does not start by itself after the board was dark)
        self.studio.finish_performance(now)
        if self.studio.playing or self.studio.preview or self.studio.take_playing:
            with self.clip_lock:
                self.studio.stop(now)
        self.close()

    def sweep_samples(self, revision):
        # A quarantined song book may be repaired later and still owns its audio.
        if any(self.clips.parent.glob('songs.broken*')):
            return
        with self.clip_lock:
            referenced = set(self.studio.clip_names())
        for clip in self.clips.glob('Song sample *.wav'):
            if clip.stem in referenced or not self.captured(clip.stem):
                continue
            with self.clip_lock:
                if (self.stopped or revision != self.studio.saved_revision or not self.studio.storage_safe
                        or self.studio.performance is not None or self.studio.record_at is not None):
                    self.sample_revision = -1
                    return
                placed = any(isinstance(pad, dict) and pad.get('thing') == clip.stem
                             for bank in self.banks() for pad in bank.get('pads', {}).values())
                if not placed:
                    try:
                        clip.unlink(missing_ok=True)
                    except OSError:
                        pass  # The next saved revision retries an unavailable file.

    def external_voice(self, path, now, gain, voice):
        if self.studio.performance is None and not self.studio.accepts_loop_hit(now):
            return voice
        with self.clip_lock:
            try:
                clip = self.samples.keep(Path(path))
            except OSError:
                return voice  # The other page may already have removed a temporary sound.
            self.has_private_samples = True
            self.studio.hit(clip, now, gain=gain, voice=voice)
        self.board.changed()
        if self.studio.performance is not None:
            return RecordedVoice(voice, lambda: self.studio.cut_voice(voice, self.board.now))
        return voice

    def close(self):
        self.board.prepare_sounds(())
        self.finish_tempo_hold()
        self.pressed_tracks.clear()
        self.pressed_pads.clear()
        self.shift_at = self.picker = self.arm_since = self.track_modifier = None
        if self.studio.mode.startswith('delete') or self.studio.mode == 'duplicate':
            self.studio.action('undo', self.studio.now)
        with self.clip_lock:
            self.studio.release_all(self.board.now)
            self.studio.pedal_since = None
            # Another page has the pads now: the music waits. Not while it is being recorded, which that page may play into.
            if self.studio.record_at is None and self.studio.performance is None and (self.studio.playing or self.studio.take_playing):
                self.resume = self.studio.playing
                self.studio.stop(self.board.now)
        self.hush([v for v in self.sounding() if v[4] in ('hold', 'sustained')])

    def stop(self):
        self.board.prepare_sounds(())
        self.finish_tempo_hold()
        with self.clip_lock:
            self.studio.finish_performance(self.studio.now)
            self.studio.stop(self.studio.now)
            self.studio.flush()
        with self.seed_lock:
            self.stopped = True
        self.hush(list(self.voices))  # (switched off: nothing keeps sounding)

    def prepare_bank(self):
        self.prepare_after_seed = False
        names = (pad.get('thing') for pad in self.pads().values())
        self.board.prepare_sounds(self.clips / (name + '.wav') for name in names
                                  if isinstance(name, str) and Path(name).name == name)

    def open(self):
        self.board.wake_speaker()  # (the amplifier's wake-up click now, not on the first clip)
        self.prepare_bank()
        with self.clip_lock:
            if self.resume and not self.studio.playing:
                self.studio.action('play', self.board.now)  # (the loop that waited, from its start)
            self.resume = False

    # ---- for the management page
    def things(self):
        banks = self.banks()
        try:
            stamp = self.clips.stat().st_mtime_ns, self.hide_factory_coin
        except FileNotFoundError:
            self.catalog_banks = self.catalog_version = None
            return []
        if banks is self.catalog_banks and stamp == self.catalog_stamp:
            return self.catalog
        groups = self.clip_groups.copy()
        order = list(self.group_order)
        for bank in banks:
            clips = bank.get('pack_clips')
            if isinstance(clips, list) and isinstance(bank.get('name'), str):
                order.append(bank['name'])
                for clip in clips:
                    if isinstance(clip, str):
                        groups.setdefault(clip, bank['name'])
        rank = {kind: i for i, kind in enumerate(dict.fromkeys(order))}
        names = (clip.stem for clip in self.clips.glob('*.wav'))
        items = [{'id': name, 'name': name, 'colour': tint(name), 'kind': groups.get(name, 'My clips')}
                 for name in names if not self.captured(name) and not (self.hide_factory_coin and name == 'Coin')]
        self.catalog = sorted(items, key=lambda item: (rank[item['kind']], item['name'].casefold()))
        self.catalog_banks, self.catalog_stamp = banks, stamp
        self.catalog_version = secrets.token_hex(8)
        return self.catalog

    def label(self, action):
        in_takes = self.studio.mode in ('takes', 'delete-take')
        playing = self.studio.take_playing if in_takes else self.studio.playing
        arm_label = 'Tap to record full performance, hold for Takes'
        if in_takes:
            arm_label = 'Back to tracks'
        elif self.studio.performance is not None:
            arm_label = 'Save take'
        names = {'songs':'Track Select · Songs', 'play':'Stop Clip · Stop' if playing else 'Stop Clip · Play',
                 'performance': arm_label,
                 'takes':'Back to tracks' if in_takes else 'Takes',
                 'banks':'Shift · Banks', 'record':'Record a new track', 'sustain':'Sustain pedal',
                 'volume':f'Volume · {self.board.volume}/5', 'tempo_up':'▲ Faster', 'tempo_down':'▼ Slower',
                 'banks_prev':'Previous', 'banks_next':'Next'}
        if action in names:
            return names[action]
        cmd, _, arg = action.partition(':')
        if arg.isdecimal() and len(arg) < 6:
            i = int(arg)
            if cmd == 'slot' and i < 16:
                return f'{i+1} · {self.studio.slots[i]["title"] if self.studio.slots[i] else "Empty"}'
            if cmd == 'bank' and i < len(self.banks()):
                return self.banks()[i]['name']
            if cmd == 'level':
                return str(i)
            if cmd in ('track', 'mute') and i < 8:
                return f'{"T" if cmd == "track" else "Mute "}{i+1}'
        return action.replace('_', ' ').capitalize()

    def state(self):
        self.pads()
        state = self.studio.state()
        commands = ['performance', 'takes', 'play', 'record', 'undo', 'delete', 'click', 'quantise', 'songs', 'duplicate',
                    'banks', 'volume', 'sustain'] + list(self.actions()) + [f'slot:{i}' for i in range(16)]
        commands += [f'{gesture}_track:{i}' for i in range(8) for gesture in ('tap', 'record', 'quantise', 'undo')]
        commands += [f'clear_track:{i}' for i in range(8)] + ['move_track', 'restore', 'delete_song', 'duplicate_song']
        state['available'] = [action for action in commands if self.available(action)]
        labels = {pos: self.label(action) for pos, action in self.board.config.get('buttons', {}).items()}
        labels.update({pad: self.label(to['action']) for pad, to in self.pads().items() if 'action' in to})
        order = self.bank_order()
        result = {'bank': self.bank, 'bank_order': order, 'catalog_version': self.catalog_version, 'studio': state, 'picker': self.picker, 'labels': labels}
        in_takes = self.studio.mode in ('takes', 'delete-take')
        choices = self.take_choices()
        if self.picker in ('banks', 'volume') or in_takes:
            result['pad_hints'] = {}
        if self.picker or self.studio.mode != 'play':
            seen = set()
            for y in reversed(range(8)):
                for x in range(8):
                    action = self.choice(x, y, choices, order)
                    key = f'{x+1},{y+1}'
                    if in_takes:
                        labels[key] = choices.get((x, y), ('', ''))[1]
                        if action:
                            result['pad_hints'][key] = labels[key]
                        continue
                    labels[key] = '' if action in seen else self.label(action)
                    seen.add(action)
                    if self.picker == 'banks' and self.available(action):
                        result['pad_hints'][key] = self.label(action)
                    elif self.picker == 'volume' and action:
                        level = int(action[6:])
                        result['pad_hints'][key] = f'Volume {level} of 5' + (' · Current' if level == self.board.volume else '')
        return result

    @staticmethod
    def captured(name):
        return bool(re.fullmatch(r'Song sample [0-9a-f]{64}', name))

    def studio_request(self, body):
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            return {'error': 'Choose a studio control.'}
        if not isinstance(data, dict) or not isinstance(data.get('action'), str):
            return {'error': 'Choose a studio control.'}
        if data['action'] == 'tempo':
            self.studio.tick(self.board.now)
        context = {'slot': self.studio.slot, 'track': self.studio.recording_on, 'mode': self.studio.mode,
                   'target': self.studio.target, 'wasRecording': self.studio.performance is not None}
        if any(key in data and data[key] != current for key, current in context.items()):
            return {'error': 'The selection changed. Choose the track or song again.'}
        action = data['action']
        fields = {'rename_song':'title', 'rename_track':'track_name', 'tempo':'bpm', 'timing':'timing'}
        index = data.get('index')  # (the track meant)
        if index is not None and not (type(index) is int and 0 <= index < 8):
            return {'error': 'Choose a track.'}
        if action in ('play_take', 'rename_take', 'delete_take'):
            take = data.get('take')
            if not isinstance(take, str) or not self.studio.available('take:' + take):
                return {'error': 'Choose a saved take.'}
            if action == 'rename_take':
                done = self.studio.edit('rename_take', data.get('value'), take=take)
                if done:
                    self.board.changed()
            else:
                done = self.command(f'{action}:{take}', self.board.now)
        elif action in fields:
            done = self.studio.edit(fields[action], data.get('value'), track=index)
            if done:
                self.board.changed()
        elif action in ('move_track', 'duplicate_song'):
            try:
                done = self.studio.move_track(data.get('from'), data.get('to')) if action == 'move_track' else self.studio.duplicate_song()
            except OSError:
                return {'error': 'Could not save the song. Check free space and try again.'}
            self.board.changed()
            if action == 'duplicate_song' and done is not None:
                return {'slot': done}
            done = bool(done)
        else:
            done = self.command(action, self.board.now)
        return {} if done else {'error': (self.studio.performance_error if action in ('performance', 'play_take', 'play') else '') or 'This control is unavailable here.'}

    def request(self, name, params, body):
        """upload (name: the file's name; body: the file), clip (id): the WAV to listen to, delete (id)."""
        if name == 'import-bank':
            return prepare_bank(body, self, convert)
        if name == 'import-song':
            return song_import.prepare_song(body, self, convert)
        if name == 'studio':
            with self.clip_lock:
                return self.studio_request(body)
        if name in ('project', 'take-project'):
            with self.clip_lock:
                return self.export_project(name, params)
        clip_id = params.get('id', '')
        if name == 'delete':
            if not clip_id or Path(clip_id).name != clip_id:
                return {'error': 'Choose a clip in the library.'}
            with self.clip_lock:
                if self.captured(clip_id) or self.studio.uses_clip(clip_id):
                    return {'error': 'A song or a take plays this clip, so it stays. Clear it from them first.'}
                self.board.clear_clip_pads(clip_id)
                (self.clips / (clip_id + '.wav')).unlink(missing_ok=True)
            return {}
        if name == 'clip' and clip_id and Path(clip_id).name == clip_id:
            exact = self.clips / (clip_id+'.wav')
            if self.captured(clip_id) and not exact.is_file():
                try:
                    self.samples.flush(clip_id)
                except OSError:
                    return {'error': 'This recording could not save its sound.'}
            if exact.is_file():
                return 'audio/wav', exact.read_bytes()
        clip = self.clips / (re.sub(r'[^\w \-]', '', Path(params.get('name') or params.get('id') or '').stem).strip()[:40] + '.wav')
        if clip.stem == '':
            return {'error': 'The clip needs a name.'}
        used = {'error': 'A recording uses this clip. Choose a different clip name.'}
        if name == 'upload':
            try:
                with tempfile.TemporaryDirectory(dir=self.board.data) as tmp:
                    ready = Path(tmp) / clip.name
                    convert(body, ready, levelled=params.get('levelled') == '1')
                    with self.clip_lock:
                        # Never over another clip unasked: this one gets the next free name. (A hidden factory
                        # sound of that name is nobody's clip.)
                        number = 1
                        while (params.get('replace') != '1' and clip.exists()
                               and not (clip.stem == 'Coin' and self.hide_factory_coin)):
                            number += 1
                            clip = clip.with_name(f'{ready.stem[:40 - len(str(number)) - 1].rstrip()} {number}.wav')
                        if self.studio.uses_clip(clip.stem):
                            return used
                        ready.replace(clip)
                        if clip.stem == 'Coin':
                            self.hide_factory_coin = False
            except ValueError as error:
                return {'error': str(error)}
            return {'id': clip.stem}
        if name == 'clip' and clip.exists():
            return 'audio/wav', clip.read_bytes()
        return {'error': f'There is no clip called {clip.stem}.'}

    def export_project(self, name, params):
        if name == 'take-project':
            if str(self.studio.slot) != params.get('slot'):
                return {'error': 'Choose this song again to download its take.'}
            song = self.studio.take_project(params.get('id', ''))
            if song is None:
                return {'error': self.studio.performance_error or 'This take is unavailable.'}
        else:
            if self.studio.record_at is not None:
                return {'error': 'Finish this layer before exporting the song.'}
            if not self.studio.song:
                return {'error': 'Choose a song to export.'}
            song = deepcopy({k: self.studio.song[k] for k in ('title', 'bpm', 'tracks')})
            song['importLimits'] = dict(archive=song_import.MAX_ARCHIVE, samples=song_import.LIMIT,
                                        project=song_import.MAX_PROJECT, clips=song_import.MAX_CLIPS)
        gated = {event['clip'] for track in song['tracks'] for event in track['events'] if 'length' in event}
        song['releaseSeconds'] = {clip: release_seconds(clip) for clip in gated}
        clips = {event['clip'] for track in song['tracks'] for event in track['events']}
        song['chordNotes'] = {clip: synth.CHORD_NOTES[clip] for clip in clips & synth.CHORD_NOTES.keys()}
        return song
