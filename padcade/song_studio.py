"""An eight-beat recorder driven by the board's monotonic clock. Events keep their original timing."""
import json
import math
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

from .song_book import BEATS, MAX_BPM, MIN_BPM, TIMING, read_book, track_ok
from .takes import Playback, Recording, write_json
from .synth import PEDAL_CLIPS, PIANO_FACTORY_CLIPS

SONG_WRITER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='song-writer')


def release_seconds(clip):
    if clip in PIANO_FACTORY_CLIPS:
        return .32  # Piano damper.
    if clip in PEDAL_CLIPS:
        return .12  # Synth envelope.
    return .006  # De-click other samples assigned to a held pad.


def release_note(clip, voice):
    if hasattr(voice, 'release'):
        voice.release(release_seconds(clip))
    else:
        voice.terminate()


def snap(beat, timing):
    """Tight: on the nearest sixteenth note. Gentle: halfway there. Fine: on the nearest thirty-second, which keeps
    quick double hits apart."""
    if timing == 'Fine':
        return math.floor(beat * 8 + .5) / 8
    grid = math.floor(beat * 4 + .5) / 4
    return grid if timing == 'Tight' else (beat + grid) / 2 if timing == 'Gentle' else beat


def new_track(index):
    return {'name': f'Track {index + 1}', 'timing': TIMING[0], 'muted': False, 'events': []}


def new_song(slot, bpm=96):
    return {'title': f'Song {slot + 1}', 'bpm': bpm, 'tracks': [new_track(i) for i in range(8)]}


def schedule(song):
    notes = []
    for i, track in enumerate(song['tracks']):
        for event in track['events']:
            start = round(snap(event['beat'], track['timing']), 8)
            finger = length = event.get('length', -1)
            pedal = None
            if event.get('sustain'):
                pedal = event['pedalDown'] - start
                pedal_up = event['beat'] + length + event['sustain']
                length = max(length, pedal_up - start)
            notes.append((start % BEATS, i, event['clip'], length, event.get('gain', 1.), finger, pedal))
    return sorted(notes, key=lambda note: note[:3])


def after_beat(events, beat):
    # Removing whole laps must retain the same precision as the score and its clock.
    return bisect_right(events, (round(beat, 8), 8, ''))


class Studio:
    """The board serializes input and transport ticks. Playback callbacks sound a clip or silence existing voices."""

    def __init__(self, data, play_clip, stop_audio, play_click, prepare_save=lambda: None):
        self.prepare_save = prepare_save
        self.file = data / 'songs.json'
        self.writing = self.save_error = self.load_notice = None
        self.dirty, self.retry_at, self.saved_revision = False, 0., 0
        self.play_clip, self.silence, self.play_click = play_clip, stop_audio, play_click
        self.slots = [new_song(0)] + [None] * 15
        self.slot, self.undo_delete = 0, None
        saved, self.load_notice = read_book(self.file)
        if saved is not None:
            self.slots, self.slot, self.undo_delete = saved['slots'], saved['slot'], saved.get('undo')
        self.recording_on = None  # the track a layer is being counted in or recorded on
        self.queued = self.target = None
        self.mode = 'songs' if self.slot is None else 'play'
        self.playing = self.click = False
        self.origin = self.now = self.previous = 0.
        self.idle_pulse = None
        self.record_at = self.preview = None
        self.take = []
        self.held_hits = {}
        self.pedal_since = None
        self.sustained_hits = []
        self.note_offs = []
        self.take_folder = data / 'takes'
        self.performance = self.playback = self.selected_take = self.performance_error = None
        self.known_takes = self.take_ids()
        self.select_newest_take()
        self.refresh_score()

    @property
    def song(self):
        return self.slots[self.slot] if self.slot is not None else None

    def refresh_score(self):
        self.events = schedule(self.song) if self.song else []
        # Each bit marks a half-beat on the track strip, including uncommitted hits while recording.
        self.steps = [0] * 8
        for beat, i, *_ in self.events:
            self.steps[i] |= 1 << int(beat * 2)
        self.layer_counts = [len({e['take'] for e in t['events']}) for t in self.song['tracks']] if self.song else []

    @property
    def storage_safe(self):
        return not (self.dirty or self.writing or self.save_error or self.load_notice)

    def book(self):
        return {'slots': self.slots, 'slot': self.slot, 'undo': self.undo_delete}

    def collect_write(self):
        if self.writing is not None:
            try:
                self.writing.result()
                self.after_save(self.writing_refs)
                self.save_error = None
            except OSError:
                self.save_failed()
            self.writing = None

    def save_failed(self):
        self.dirty, self.retry_at = True, self.now + 2
        self.save_error = 'The song is still in memory. Check free space. Saving will retry automatically.'

    def service_writes(self):
        if self.writing is not None and not self.writing.done():
            return
        self.collect_write()
        if self.dirty and self.now >= self.retry_at:
            self.dirty = False
            self.writing_refs = self.take_ids()
            # Finished notes never change. Snapshot their lists so the next overdub can append safely.
            notes = {id(track['events']): track['events'].copy() for track in self.saved_tracks()}
            self.writing = SONG_WRITER.submit(self.write_book, deepcopy(self.book(), notes))

    def flush(self):
        self.collect_write()
        if self.dirty:
            self.save()

    def save(self, *, required=False, deferred=False):
        if not required and (deferred or self.writing is not None and not self.writing.done()):
            self.dirty = True
            self.service_writes()
            return
        # Explicit edits follow any pending layer so an older snapshot never replaces a newer song.
        self.collect_write()
        try:
            self.write_book(self.book())
        except OSError:
            self.save_failed()
            if required:
                raise
            return
        self.dirty, self.save_error = False, None
        self.after_save(self.take_ids())

    def write_book(self, book):
        self.prepare_save()
        write_json(self.file, book)

    def after_save(self, referenced):
        self.saved_revision += 1
        for old in self.known_takes - referenced:
            try:
                (self.take_folder / (old + '.json')).unlink(missing_ok=True)
            except OSError:
                referenced.add(old)  # The index is already durable. Retry unused-file cleanup on the next save.
        self.known_takes = referenced

    def all_take_refs(self):
        songs = [s for s in self.slots if s]
        if self.undo_delete and self.undo_delete['kind'] == 'song':
            songs.append(self.undo_delete['item'])
        for song in songs:
            yield from song.get('takes', ())
            if song.get('deletedTake'):
                yield song['deletedTake']['item']

    def take_ids(self):
        return {item['id'] for item in self.all_take_refs()}

    def take_items(self):
        return [{key: item[key] for key in ('id', 'title', 'duration')}
                for item in self.song.get('takes', ())] if self.song else []

    def select_newest_take(self):
        items = self.song.get('takes', ()) if self.song else ()
        self.selected_take = items[0]['id'] if items else None

    def take_ref(self, key):
        return next((item for item in self.song.get('takes', ()) if item['id'] == key), None) if self.song else None

    def take_project(self, key):
        item = self.take_ref(key)
        if item:
            try:
                project = json.loads((self.take_folder / (item['id'] + '.json')).read_text())
                if (isinstance(project, dict) and type(project.get('bpm')) is int and MIN_BPM <= project['bpm'] <= MAX_BPM
                        and isinstance(project.get('tracks'), list) and len(project['tracks']) == 9
                        and all(track_ok(track, max_beat=math.inf) for track in project['tracks'])):
                    self.performance_error = None
                    return dict(project, id=item['id'], duration=item['duration'], title=item['title'])
            except (OSError, ValueError, RecursionError):
                pass
            self.performance_error = f'Could not read {item["title"]}. Its saved file is missing or damaged.'

    @property
    def take_playing(self):
        return self.playback is not None

    def performance_hit(self, clip, gain, now, voice=None):
        if self.performance is not None:
            self.performance.hit(clip, gain, now, 8, voice)

    def cut_voice(self, voice, now):
        if self.performance is not None:
            self.performance.cut(voice, now)

    def finish_performance(self, now):
        if self.performance is None:
            return
        number = self.song.get('nextTake', 1)
        try:
            item = self.performance.finish(now, self.take_folder, f'Take {number}')
        except OSError:
            self.performance_error = 'Could not save the take. Check free space and try again.'
            return False
        self.song['nextTake'] = number + 1
        items = self.song.setdefault('takes', [])
        items.insert(0, item)
        try:
            self.save(required=True)
        except OSError:
            items.pop(0)
            self.song['nextTake'] = number
            self.performance_error = 'Could not save the take. Check free space and try again.'
            return False
        self.selected_take = item['id']
        self.performance = self.performance_error = None
        return True

    def end_take_playback(self):
        if self.playback is not None:
            self.playback = None
            self.stop_audio()

    def toggle_take_playback(self, key, now):
        if self.take_playing and self.selected_take == key:
            self.end_take_playback()
            return True
        project = self.take_project(key)
        if project is None:
            return False
        self.stop(now)
        self.mode, self.selected_take = 'takes', key
        self.playback = Playback(project, now)
        self.tick(now)
        return True

    def can_set_tempo(self):
        return (self.song is not None and self.record_at is None
                and self.preview is None and self.mode in ('play', 'songs'))

    def edit(self, field, value, *, persist=True, track=None, take=None):
        if not self.song or self.record_at is not None or self.mode.startswith('delete'):
            return False
        if field == 'rename_take':
            item = self.take_ref(take or self.selected_take)
            if self.mode != 'takes' or not item or not isinstance(value, str) or not value.strip():
                return False
            before = item['title']
            item['title'] = value.strip()[:48]
            try:
                self.save(required=True)
            except OSError:
                item['title'] = before
                return False
            return True
        if field == 'bpm':
            if not self.can_set_tempo() or type(value) not in (int, float, str):
                return False
            try:
                bpm = int(value)
            except (ValueError, OverflowError):
                return False
            if not MIN_BPM <= bpm <= MAX_BPM:
                return False
            self.origin = self.now - self.clock_beat(self.now) * 60 / bpm
            if self.playing:
                ratio = self.song['bpm'] / bpm
                if self.performance is not None:
                    self.performance.retime(self.now, ratio)
                self.note_offs = [(self.now + (end - self.now) * ratio, clip, voice)
                                  for end, clip, voice in self.note_offs]
            self.song['bpm'] = bpm
        elif field == 'title' and isinstance(value, str) and value.strip():
            self.song['title'] = value.strip()[:48]
        elif field == 'track_name' and track is not None and isinstance(value, str) and value.strip():
            self.song['tracks'][track]['name'] = value.strip()[:32]
        elif field == 'timing' and value in TIMING and self.available(f'quantise_track:{track}'):
            self.song['tracks'][track]['timing'] = value
            self.refresh_score()
        else:
            return False
        if persist:
            self.save(deferred=self.playing and field == 'timing')
        return True

    def clock_beat(self, now):
        # Clock subtraction can put an exact beat just below its integer boundary.
        return round((now - self.origin) * self.song['bpm'] / 60, 8)

    def metronome_beat(self, now):
        if self.preview is not None:
            subject, _, _, start = self.preview
            return round((now - start) * subject['bpm'] / 60, 8)
        return self.clock_beat(now)

    def beat(self, now):
        return self.clock_beat(now) if self.playing else 0.

    def start(self, now, count=0):
        if not self.playing and self.click and self.idle_pulse is not None:
            # The next free-running click becomes the downbeat (or the first count).
            now = max(now, self.origin + (self.idle_pulse + 1) * 60 / self.song['bpm'])
        self.idle_pulse = None
        self.playing = True
        self.origin = now + count * 60 / self.song['bpm']
        self.previous = -count - .000001

    def release_hit(self, pad, now):
        if self.performance is not None:
            held = self.performance.held.get(pad)
            pedal = self.pedal_since if held and held['clip'] in PEDAL_CLIPS else None
            self.performance.lift(pad, now, pedal)
        self.release_loop_hit(pad, now)

    def release_loop_hit(self, pad, now):
        event = self.held_hits.pop(pad, None)
        if event is not None:
            end = min(BEATS, self.beat(now) - self.record_at)
            event['length'] = max(0., end - event['beat'])
            if self.pedal_since is not None and event['clip'] in PEDAL_CLIPS:
                self.sustained_hits.append(event)

    def finish_sustain(self, now):
        if self.performance is not None:
            self.performance.pedal_up(now)
        self.finish_loop_sustain(now)

    def finish_loop_sustain(self, now):
        if self.sustained_hits:
            end = min(BEATS, self.beat(now) - self.record_at)
            down = max(0., self.beat(self.pedal_since) - self.record_at)
            for event in self.sustained_hits:
                extra = end - (event['beat'] + event['length'])
                if extra > 0:
                    event.update(sustain=extra, pedalDown=down)
            self.sustained_hits.clear()

    def release_all(self, now):
        for pad in set(self.held_hits) | (self.performance.held.keys() if self.performance is not None else set()):
            self.release_hit(pad, now)
        self.finish_sustain(now)

    def stop_audio(self):
        if self.performance is not None:
            for pad in list(self.performance.held):
                self.performance.lift(pad, self.now, None)
            self.performance.pedal_up(self.now)
        self.silence()
        self.note_offs.clear()

    def finish_take(self):
        for pad in list(self.held_hits):
            self.release_loop_hit(pad, self.now)
        self.finish_loop_sustain(self.now)
        layer, self.take, self.record_at = self.take, [], None
        track, self.recording_on = self.recording_on, None
        if layer:
            self.song['tracks'][track]['events'].extend(layer)
            self.undo_delete = None
            self.refresh_score()
            self.save(deferred=True)

    def hit(self, clip, now, held_pad=None, gain=1., voice=None):
        self.tick(now)
        if self.performance is not None:
            if held_pad is not None:
                self.release_hit(held_pad, now)
            self.performance.hit(clip, gain, now, 8, voice, held_pad)
        if self.accepts_loop_hit(now):
            offset = max(0., self.beat(now) - self.record_at)
            event = {'beat': offset, 'clip': clip, 'take': self.layer}
            if gain != 1.:
                event['gain'] = gain
            if held_pad is not None:
                self.release_loop_hit(held_pad, now)
                self.held_hits[held_pad] = event
            self.take.append(event)
            audible_beat = snap(offset, self.song['tracks'][self.recording_on]['timing']) % BEATS
            self.steps[self.recording_on] |= 1 << int(audible_beat * 2)
            return True  # The recorded pattern shown on the page changed.
        return False

    def emit(self, events, low, high, song, solo=None, with_recording=False):
        # Seek into the last fraction of a beat after a delay to avoid replaying a backlog.
        low = max(low, high - .25)
        for cycle in range(max(0, math.floor(low / BEATS)), math.floor(high / BEATS) + 1):
            offset = cycle * BEATS
            first = after_beat(events, low - offset)
            last = after_beat(events, high - offset)
            for at, i, clip, length, gain, finger, pedal in events[first:last]:
                # (a muted track is heard while a layer is recorded on it: what is there, to play along with)
                audible = i == solo if solo is not None else not song['tracks'][i]['muted'] or (with_recording and i == self.recording_on)
                if not audible:
                    continue
                voice = self.play_clip(clip, gain)
                sounded = self.performance.hit(clip, gain, self.now, i, voice) if self.performance is not None else None
                if length < 0:
                    continue
                late = high - offset - at
                if late >= length and clip not in PIANO_FACTORY_CLIPS:
                    # Both edges are overdue. Let a short note sound its attack.
                    late = 0.
                if sounded is not None:
                    self.performance.gate(sounded, song['bpm'], late, finger, length, pedal)
                if voice is not None:
                    if late >= length and clip in PIANO_FACTORY_CLIPS:
                        release_note(clip, voice)
                    else:
                        self.note_offs.append((self.now + (length - late) * 60 / song['bpm'], clip, voice))

    def next_deadline(self, foreground=True):
        """Monotonic time of the next musical edge, or None while the transport is quiet."""
        if self.playback is not None:
            return self.playback.next_deadline()

        deadlines = [end for end, _, _ in self.note_offs]
        beats = []
        if self.preview is not None:
            song, events, _, origin = self.preview
            previous = self.preview_previous
            beats.append(BEATS)  # end of the audition
        elif self.playing:
            song, events, origin, previous = self.song, self.events, self.origin, self.previous
            if self.queued is not None:
                beats.append(self.queue_beat)
            if self.record_at is not None:
                count = max(self.record_at - 4, math.floor(previous) + 1)
                if count <= self.record_at:
                    beats.append(count)
                beats.append(self.record_at + BEATS)
        else:
            if foreground and self.song and self.click and self.mode not in ('takes', 'delete-take'):
                deadlines.append(self.now if self.idle_pulse is None else
                                 self.origin + (self.idle_pulse + 1) * 60 / self.song['bpm'])
            return min(deadlines, default=None)

        if self.click:
            beats.append(math.floor(previous) + 1)
        if events:
            lap = max(0, math.floor(previous / BEATS)) * BEATS
            i = after_beat(events, previous - lap)
            beats.append(lap + (events[i][0] if i < len(events) else BEATS + events[0][0]))
        if beats:
            deadlines.append(origin + min(beats) * 60 / song['bpm'])
        return min(deadlines, default=None)

    def tick(self, now, foreground=True):
        self.now = now
        self.service_writes()
        if self.playback is not None:
            if not self.playback.tick(now, self.play_clip, release_note):
                self.end_take_playback()
            return
        if self.note_offs:
            for end, clip, voice in self.note_offs:
                if end <= now:
                    release_note(clip, voice)
            self.note_offs = [pending for pending in self.note_offs if pending[0] > now]
        if self.preview is not None:
            subject, events, solo, _ = self.preview
            beat = self.metronome_beat(now)
            if beat < BEATS:
                if self.click and self.song and math.floor(beat) > self.preview_previous:
                    self.play_click(False)
                self.emit(events, self.preview_previous, beat, subject, solo)
                self.preview_previous = beat
                return
            self.end_preview(now)
        if not self.playing:
            if foreground and self.click and self.song and self.mode not in ('takes', 'delete-take'):
                pulse = math.floor(self.clock_beat(now))
                if pulse != self.idle_pulse:
                    self.play_click(False)
                    self.idle_pulse = pulse
            return
        beat = self.beat(now)
        if self.queued is not None and beat >= self.queue_beat:
            boundary = self.origin + self.queue_beat * 60 / self.song['bpm']
            self.open_slot(self.queued, boundary, deferred=True)
            beat = self.beat(now)
        if self.record_at is not None and beat >= self.record_at + BEATS:
            self.previous = max(self.previous, self.record_at + BEATS - .000001)
            self.finish_take()
        if self.previous < beat:
            pulse = math.floor(beat)
            counting = self.record_at is not None and self.record_at - 4 <= pulse < self.record_at
            if pulse > self.previous and (self.click or counting):
                self.play_click(counting)
            self.emit(self.events, self.previous, beat, self.song, with_recording=True)
            self.previous = beat

    def stop(self, now):
        self.tick(now, foreground=False)
        if self.playing and self.click:
            self.idle_pulse = math.floor(self.beat(now))
        self.finish_take()
        self.end_take_playback()
        self.stop_audio()
        self.playing = False
        self.queued = self.preview = self.target = None
        self.mode = 'songs' if self.slot is None else 'play'

    def count_in(self, now):
        if self.record_at is not None:
            remaining = self.record_at - math.floor(self.beat(now))
            if 1 <= remaining <= 4:
                return remaining
        return None

    def accepts_loop_hit(self, now):
        # Allow a small human lead on the first downbeat, including hits from another page.
        return self.record_at is not None and self.record_at - .05 * self.song['bpm'] / 60 <= self.beat(now) < self.record_at + BEATS

    def recording(self, now):
        return self.record_at is not None and self.record_at <= self.beat(now) < self.record_at + BEATS

    def saved_tracks(self):
        for song in self.slots:
            if song:
                yield from song['tracks']
        if self.undo_delete:
            item = self.undo_delete['item']
            yield from item['tracks'] if 'tracks' in item else [item]

    def clip_names(self):
        if self.performance is not None:
            yield from self.performance.clips
        for item in self.all_take_refs():
            yield from item['clips']
        for track in self.saved_tracks():
            yield from (e['clip'] for e in track['events'])
        yield from (e['clip'] for e in self.take)

    def uses_clip(self, name):
        return name in self.clip_names()

    def open_slot(self, i, now, *, deferred=False):
        self.stop_audio()
        self.slot = i
        self.select_newest_take()
        self.mode = 'songs' if i is None else 'play'
        self.target = self.queued = self.preview = None
        self.refresh_score()
        if self.playing and self.song:
            self.start(now)
        else:
            self.playing = False
            self.origin, self.idle_pulse = now, None
        self.save(deferred=deferred)

    def choose_slot(self, i, now):
        empty = self.slots[i] is None
        if self.mode == 'duplicate':
            if self.target is None:
                self.target = i
                return
            self.slots[i] = deepcopy(self.slots[self.target])
            self.slots[i].pop('deletedTake', None)
            self.slots[i]['title'] = self.slots[i]['title'][:41] + ' (copy)'
            self.target, self.mode = None, 'songs'
            empty = False
        elif empty:
            self.slots[i] = new_song(i, self.song['bpm'] if self.song else 96)
        if self.undo_delete and self.undo_delete['kind'] == 'song' and self.undo_delete['slot'] == i:
            self.undo_delete = None
        if i == self.slot:
            self.queued, self.mode = None, 'play'
        elif empty or not self.playing:
            self.open_slot(i, now)
            self.tick(now)
        else:
            self.queued = i
            self.queue_beat = (1 + math.floor(max(0, self.beat(now)) / BEATS)) * BEATS
            self.save()

    def add_song(self, song):
        """A song from outside in the first empty slot: its number, or None when all are taken. A slot whose
        deleted song can still be restored is used last, and the restore is given up for it."""
        held = self.undo_delete['slot'] if self.undo_delete and self.undo_delete['kind'] == 'song' else None
        free = sorted((i for i, s in enumerate(self.slots) if s is None), key=lambda i: i == held)
        if not free:
            return None
        i, undo = free[0], self.undo_delete
        if i == held:
            self.undo_delete = None
        self.slots[i] = song
        try:
            self.save(required=True)
        except BaseException:  # (whatever kept it from being saved: the book stays as it was)
            self.slots[i], self.undo_delete = None, undo
            raise
        return i

    def end_preview(self, now):
        if self.preview is not None:
            self.stop_audio()
            self.preview = None
            self.previous = self.beat(now) - .000001

    def audition(self, i, now):
        self.stop_audio()
        self.target = i
        track = self.mode == 'delete-track'
        subject = self.song if track else self.slots[i]
        self.preview = (subject, self.events if track else schedule(subject), i if track else None, now)
        self.preview_previous = -.000001
        self.tick(now)

    def delete(self, now):
        self.queued = None
        if self.mode in ('songs', 'play'):
            self.mode = 'delete-song' if self.mode == 'songs' else 'delete-track'
            self.target = None
            return
        self.end_preview(now)
        i = self.target
        self.target = None
        song = self.mode == 'delete-song'
        self.mode = 'songs' if song else 'play'
        if i is None:
            return
        if song:
            self.remove_song(i, now)
        else:
            self.clear_track(i)

    def remove_song(self, i, now):
        self.undo_delete = {'kind': 'song', 'slot': i, 'item': self.slots[i], 'id': uuid4().hex[:8]}
        self.slots[i] = None
        if i == self.slot:
            self.open_slot(None, now)
        else:
            self.save()

    def clear_track(self, i):
        item = self.song['tracks'][i]
        self.undo_delete = {'kind': 'track', 'slot': self.slot, 'track': i, 'item': item, 'id': uuid4().hex[:8]}
        self.song['tracks'][i] = dict(item, muted=False, events=[])
        self.refresh_score()
        self.save()

    def move_track(self, i, j):
        """A track goes to another place with all that is its own, and the ones it passes move up. A cleared track
        that can still be restored goes along."""
        if not (self.available('move_track') and all(type(k) is int and 0 <= k < 8 for k in (i, j)) and i != j):
            return False
        tracks, undo = self.song['tracks'], self.undo_delete
        new = lambda k: j if k == i else k - (i < k <= j) + (j <= k < i)
        tracks.insert(j, tracks.pop(i))
        moved = [(tracks[new(k)], k) for k in range(8)]
        if undo and undo['kind'] == 'track' and undo['slot'] == self.slot:
            moved.append((undo['item'], undo['track']))
            undo['track'] = new(undo['track'])
        for track, before in moved:
            if track['name'] == new_track(before)['name']:  # Still called by its number: the number of its new place.
                track['name'] = new_track(new(before))['name']
        self.refresh_score()
        self.save()
        return True

    def duplicate_song(self):
        """The open song again in the first empty slot: that slot, or None when it cannot be copied now."""
        if not self.available('duplicate_song'):
            return None
        copy = deepcopy(self.song)
        copy.pop('deletedTake', None)
        copy['title'] = copy['title'][:41] + ' (copy)'
        return self.add_song(copy)

    def can_restore(self):
        undo = self.undo_delete
        return bool(undo) and (undo['kind'] == 'song' or self.mode == 'play' and self.slot == undo['slot'])

    def undo(self, now, index=None):
        if self.mode.startswith('delete'):
            self.end_preview(now)
            self.mode = 'songs' if self.mode == 'delete-song' else 'play'
            self.target = None
            return
        if index is None and (self.mode == 'duplicate' or self.queued is not None):
            if self.mode == 'duplicate':
                self.mode = 'songs'
            self.target = self.queued = None
            return
        if index is None:
            return self.restore(now)
        else:
            track = self.song['tracks'][index]
            latest = max(e['take'] for e in track['events'])
            track['events'] = [e for e in track['events'] if e['take'] != latest]
        self.refresh_score()
        self.save()

    def restore(self, now):
        last = self.undo_delete
        self.undo_delete = None
        if last['kind'] == 'song':
            self.slots[last['slot']] = last['item']
            if self.slot is None:
                self.open_slot(last['slot'], now)
                return
        else:
            self.song['tracks'][last['track']] = last['item']
        self.refresh_score()
        self.save()

    def available(self, name):
        if not isinstance(name, str):
            return False
        cmd, separator, arg = name.partition(':')
        if cmd in ('take', 'play_take', 'delete_take'):
            return (self.mode in ('takes', 'delete-take') and self.take_ref(arg) is not None and
                    (cmd == 'take' or self.mode == 'takes'))
        track_action = cmd in ('track', 'mute', 'tap_track', 'record_track', 'undo_track', 'quantise_track', 'clear_track')
        indexed = track_action or cmd == 'slot'
        if separator and not indexed:
            return False
        if indexed and (not arg.isascii() or not arg.isdecimal() or len(arg) > 2):
            return False
        i = int(arg) if indexed else None
        if (track_action and not 0 <= i < 8) or (cmd == 'slot' and not 0 <= i < 16):
            return False
        if cmd in ('restore', 'delete_song', 'duplicate_song', 'clear_track', 'move_track'):
            # What the management page asks for in one step. Not while anything is recorded or a song is queued.
            if self.record_at is not None or self.performance is not None or self.mode not in ('play', 'songs') or self.queued is not None:
                return False
            if cmd == 'restore':
                return self.can_restore()
            if cmd in ('delete_song', 'duplicate_song'):
                return self.song is not None and (cmd == 'delete_song' or None in self.slots)
            return self.mode == 'play' and self.song is not None and (cmd == 'move_track' or bool(self.song['tracks'][i]['events']))
        if cmd == 'performance':
            return self.performance is not None or (self.song is not None and self.mode == 'play' and self.queued is None)
        if cmd == 'takes':
            return (self.song is not None and self.performance is None and self.record_at is None and self.queued is None
                    and self.mode in ('play', 'takes', 'delete-take'))
        if self.mode in ('takes', 'delete-take'):
            if cmd == 'delete':
                return bool(self.song.get('takes'))
            if cmd == 'undo':
                return self.mode == 'delete-take' or bool(self.song.get('deletedTake'))
            return cmd == 'play' and self.selected_take is not None and self.mode == 'takes'
        if self.performance is not None and cmd in ('songs', 'delete'):
            return False
        if cmd == 'click':
            return self.song is not None
        if self.mode.startswith('delete'):
            if cmd == 'slot' and self.mode == 'delete-song':
                return self.slots[i] is not None
            if cmd == 'track' and self.mode == 'delete-track':
                return bool(self.song['tracks'][i]['events'])
            return cmd in ('undo', 'delete') or cmd == 'play' and self.playing
        if cmd == 'play':
            return self.slot is not None
        if self.mode in ('songs', 'duplicate'):
            if cmd == 'slot':
                if self.mode == 'duplicate':
                    return bool(self.slots[i]) if self.target is None else self.slots[i] is None
                return True
            if cmd == 'duplicate':
                return any(self.slots) and None in self.slots
            if cmd == 'undo':
                return self.mode == 'duplicate' or self.queued is not None or self.can_restore()
            if cmd == 'delete':
                return self.mode == 'songs' and any(self.slots)
            return cmd == 'songs' and self.slot is not None
        if self.mode != 'play':
            return False
        if not self.song:
            return False
        tracks, recording = self.song['tracks'], self.record_at is not None
        if cmd == 'mute':
            return bool(tracks[i]['events']) and not (recording and i == self.recording_on)
        if cmd == 'tap_track':  # (the arrow of the track being recorded on ends its layer; otherwise it mutes what is recorded)
            return recording and i == self.recording_on or bool(tracks[i]['events'])
        if cmd == 'record_track':
            return not recording
        if cmd in ('undo_track', 'quantise_track'):
            return not recording and bool(tracks[i]['events'])
        if cmd == 'songs':
            return not recording
        if cmd == 'delete':
            return not recording and any(t['events'] for t in tracks)
        if cmd == 'undo':  # (by itself it only takes back a queued song or brings back what was deleted)
            return not recording and (self.queued is not None or self.can_restore())
        if cmd == 'record':  # (by itself: a new part on the first empty track, or the end of the layer being recorded)
            return recording or any(not t['events'] for t in tracks)
        return cmd == 'play'

    def action(self, name, now):
        if name == 'performance' and self.performance is None:
            if not self.available(name):
                return False
            # Capture the backing notes due on the first tick of the full performance.
            self.performance_error = None
            self.performance = Recording(self.song, now)
            self.tick(now, foreground=False)
            return True
        if not isinstance(name, str):
            return False
        recording = self.record_at is not None
        if name.startswith('tap_track:'):
            if not self.available(name):
                return False
            target = int(name.partition(':')[2])
            # The arrow of the track being recorded on cancels its count-in or finishes the layer.
            name = 'record' if recording and target == self.recording_on else f'mute:{target}'
        finishing = name == 'record' and recording
        if name == 'record' and not recording:
            # A new part, on the first empty track of the song this beat belongs to: a queued song may just be starting.
            if self.queued is not None and self.beat(now) >= self.queue_beat:
                self.tick(now, foreground=False)
            if not self.available(name):
                return False
            name = 'record_track:%d' % next(i for i, t in enumerate(self.song['tracks']) if not t['events'])
        arming = name.startswith('record_track:') and self.available(name)
        # Arming owns this beat's click if it is the first count. Advance the clock after setting the take.
        if not arming or (self.queued is not None and self.beat(now) >= self.queue_beat):
            self.tick(now, foreground=False)
        if finishing and self.record_at is None:
            return True  # The layer ended by itself at this very moment: there is nothing left to end.
        if not self.available(name):
            return False
        if arming:
            self.recording_on = int(name.partition(':')[2])
            name = 'record'
        if name == 'performance':
            return self.finish_performance(now)
        elif name == 'takes':
            self.end_take_playback()
            self.target = None
            self.mode = 'takes' if self.mode == 'play' else 'play'
            if self.mode == 'takes':
                self.select_newest_take()
        elif self.mode in ('takes', 'delete-take'):
            return self.library_action(name, now)
        elif name == 'undo' or name.startswith('undo_track:'):
            self.undo(now, int(name.split(':')[1]) if ':' in name else None)
        elif name == 'delete':
            self.delete(now)
        elif self.mode.startswith('delete') and name.startswith(('slot:', 'track:')):
            self.audition(int(name.split(':')[1]), now)
        elif name == 'songs':
            self.mode = 'songs' if self.mode == 'play' else 'play'
            self.target = None
        elif name == 'duplicate':
            self.mode = 'songs' if self.mode == 'duplicate' else 'duplicate'
            self.queued = self.target = None
        elif name.startswith('slot:'):
            self.choose_slot(int(name[5:]), now)
        elif name.startswith('clear_track:'):
            self.clear_track(int(name.partition(':')[2]))
        elif name == 'delete_song':
            self.remove_song(self.slot, now)
        elif name == 'restore':
            self.restore(now)
        elif name.startswith('mute:'):
            track = self.song['tracks'][int(name[5:])]
            track['muted'] = not track['muted']
            self.save(deferred=True)
        elif name.startswith('quantise_track:'):
            i = int(name.split(':')[1])
            current = self.song['tracks'][i]['timing']
            self.edit('timing', TIMING[(TIMING.index(current) + 1) % len(TIMING)], track=i)
        elif name == 'click':
            self.click = not self.click
            if not self.playing:
                self.origin, self.idle_pulse = now, None
        elif name == 'record':
            if not arming:
                if self.started_for_take and self.beat(now) < self.record_at:
                    self.stop(now)
                else:
                    self.finish_take()
            else:
                self.queued = None
                self.started_for_take = not self.playing
                if self.started_for_take:
                    self.start(now, 4)
                self.record_at = 0 if self.started_for_take else BEATS * math.ceil((self.beat(now) + 4) / BEATS)
                if not self.started_for_take and self.previous >= self.record_at - 4:
                    self.record_at += BEATS  # This beat already sounded before Record was pressed.
                track = self.song['tracks'][self.recording_on]
                if not track['events']:
                    track['timing'] = TIMING[0]  # A first layer plays as it was played, whatever the track held before.
                self.layer = 1 + max((e['take'] for e in track['events']), default=0)
                self.tick(now)
        elif name == 'play':
            if self.playing:
                in_songs = self.mode in ('songs', 'duplicate')
                self.stop(now)
                if in_songs:
                    self.mode = 'songs'
            else:
                self.start(now)
                self.tick(now)

        return True

    def library_action(self, name, now):
        if name == 'play' or name.startswith('play_take:'):
            return self.toggle_take_playback(self.selected_take if name == 'play' else name.partition(':')[2], now)
        elif name.startswith('take:'):
            key = name.partition(':')[2]
            if self.mode == 'delete-take':
                self.selected_take = self.target = key
            else:
                return self.toggle_take_playback(key, now)
        elif name == 'delete':
            self.end_take_playback()
            if self.mode == 'takes':
                self.mode, self.target = 'delete-take', None
            elif self.target is not None:
                return self.remove_take(self.target)
            else:
                self.mode = 'takes'
        elif name.startswith('delete_take:'):
            self.end_take_playback()
            return self.remove_take(name.partition(':')[2])
        elif name == 'undo':
            if self.mode == 'delete-take':
                self.mode, self.target = 'takes', None
            else:
                self.end_take_playback()
                deleted = self.song.pop('deletedTake')
                self.song['takes'].insert(deleted['index'], deleted['item'])
                try:
                    self.save(required=True)
                except OSError:
                    self.song['takes'].remove(deleted['item'])
                    self.song['deletedTake'] = deleted
                    return False
                self.selected_take = deleted['item']['id']
        return True

    def remove_take(self, key):
        items = self.song['takes']
        index = next(i for i, item in enumerate(items) if item['id'] == key)
        deleted = {'index': index, 'item': items.pop(index)}
        previous = self.song.get('deletedTake')
        self.song['deletedTake'] = deleted
        try:
            self.save(required=True)
        except OSError:
            items.insert(index, deleted['item'])
            self.song['deletedTake'] = previous
            return False
        self.target, self.mode = None, 'takes'
        self.performance_error = None
        self.select_newest_take()
        return True

    def state(self):
        b = self.beat(self.now)
        song = self.song or {'title': '', 'bpm': 96, 'tracks': []}
        count = self.count_in(self.now)
        recording = 'off'
        if self.record_at is not None:
            recording = 'count-in' if count else 'waiting' if b < self.record_at else 'recording'
        tracks = []
        for i, track in enumerate(song['tracks']):
            live = len(self.take) if i == self.recording_on else 0
            tracks.append({'name': track['name'], 'timing': track['timing'], 'muted': track['muted'],
                           'steps': self.steps[i], 'hits': len(track['events']) + live, 'layers': self.layer_counts[i] + bool(live)})
        lost = self.undo_delete['item'] if self.available('restore') else None
        deleted_take = (self.song or {}).get('deletedTake')
        return dict(title=song['title'], bpm=song['bpm'], tracks=tracks, slot=self.slot,
                    recording_track=self.recording_on if self.record_at is not None else None,
                    # (each thing cleared has a name of its own, so that a page can put its notice away and know the next one)
                    restorable=lost and {'kind': self.undo_delete['kind'], 'name': lost.get('title', lost.get('name')),
                                         'id': self.undo_delete.get('id', '')},
                    slots=[s['title'] if s else None for s in self.slots], playing=self.playing,
                    beat=b % BEATS if b >= 0 else b, recording=recording, count_in=count, click=self.click,
                    mode=self.mode, target=self.target, queued=self.queued,
                    storage_notice=self.save_error or self.load_notice, saving=self.dirty or self.writing is not None,
                    performance=dict(recording=self.performance is not None, error=self.performance_error,
                                     elapsed=max(0., self.now - self.performance.start) if self.performance else 0.,
                                     playing=self.take_playing, selected=self.selected_take, items=self.take_items(),
                                     target=self.target if self.mode == 'delete-take' else None,
                                     deleted=deleted_take and {key: deleted_take['item'][key] for key in ('id', 'title')}))
