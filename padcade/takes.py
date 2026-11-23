"""Linear performances. The take clock stays fixed even when the backing loop changes tempo."""
import json
import os
from bisect import bisect_left
from uuid import uuid4


def write_json(path, value):
    tmp = path.with_suffix('.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, separators=(',', ':'))
            output.flush()
            os.fsync(output.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class Recording:
    def __init__(self, song, now):
        self.start, self.bpm, self.id = now, song['bpm'], uuid4().hex
        self.tracks = [{'name': name, 'muted': False, 'timing': 'As played', 'events': []}
                       for name in [*(track['name'] for track in song['tracks']), 'Live pads']]
        self.clips, self.held, self.pedalled, self.voices = set(), {}, [], {}
        self.backing_gates = []

    def beat(self, now):
        return max(0., (now - self.start) * self.bpm / 60)

    def hit(self, clip, gain, now, track, voice=None, pad=None):
        event = {'beat': self.beat(now), 'clip': clip, 'take': 1}
        if gain != 1:
            event['gain'] = gain
        self.tracks[track]['events'].append(event)
        self.clips.add(clip)
        if pad is not None:
            self.held[pad] = event
        if voice is not None:
            self.voices[voice] = event
            # Only voices still in the mix can be cut. Drop finished one-shots from this lookup.
            if len(self.voices) > 32:
                self.voices = {v: e for v, e in self.voices.items() if v.poll() is None}
        return event

    def gate(self, event, bpm, late, finger, length, pedal):
        at, scale = event['beat'], self.bpm / bpm
        event['length'] = max(0., finger - late) * scale
        if pedal is not None:
            extra = max(0., length - late) * scale - event['length']
            if extra > 0:
                event.update(sustain=extra, pedalDown=max(0., at + (pedal - late) * scale))
        self.backing_gates = [e for e in self.backing_gates if e['beat'] + e['length'] + e.get('sustain', 0) > at]
        self.backing_gates.append(event)

    def retime(self, now, ratio):
        # A tempo change moves only finger and pedal transitions which have yet to sound.
        at = self.beat(now)
        def moved(beat):
            return at + (beat - at) * ratio if beat > at else beat
        for event in self.backing_gates:
            finger = event['beat'] + event['length']
            if 'sustain' in event:
                event['sustain'] = moved(finger + event['sustain']) - moved(finger)
                event['pedalDown'] = moved(event['pedalDown'])
            event['length'] = moved(finger) - event['beat']

    def lift(self, pad, now, pedal):
        event = self.held.pop(pad, None)
        if event is not None:
            event['length'] = max(0., self.beat(now) - event['beat'])
            if pedal is not None:
                self.pedalled.append((event, self.beat(pedal)))

    def pedal_up(self, now):
        for event, down in self.pedalled:
            extra = self.beat(now) - event['beat'] - event['length']
            if extra > 0:
                event.update(sustain=extra, pedalDown=down)
        self.pedalled.clear()

    def cut(self, voice, now):
        event = self.voices.pop(voice, None)
        if event is not None:
            event['hardStop'] = self.beat(now)

    def finish(self, now, folder, title):
        folder.mkdir(exist_ok=True)
        duration = max(0., now - self.start)
        # Complete keys still held at Stop in the saved score. A failed write must leave them held for a retry.
        updates = {id(event): dict(event, length=max(0., self.beat(now) - event['beat']))
                   for event in self.held.values()}
        for event, down in self.pedalled:
            extra = self.beat(now) - event['beat'] - event['length']
            if extra > 0:
                updates[id(event)] = dict(event, sustain=extra, pedalDown=down)
        tracks = [dict(t, events=[updates.get(id(e), e) for e in t['events']]) for t in self.tracks]
        write_json(folder / (self.id + '.json'), {'bpm': self.bpm, 'tracks': tracks})
        return {'id': self.id, 'title': title, 'duration': duration, 'clips': sorted(self.clips)}


class Playback:
    def __init__(self, project, now):
        self.end = now + project['duration']
        seconds = 60 / project['bpm']
        self.events = []
        for track in project['tracks']:
            for event in track['events']:
                at = now + event['beat'] * seconds
                gate = at + (event['length'] + event.get('sustain', 0)) * seconds if 'length' in event else None
                cut = now + event['hardStop'] * seconds if 'hardStop' in event else None
                self.events.append((at, event['clip'], event.get('gain', 1.), gate, cut))
        self.events.sort(key=lambda e: e[0])
        self.index = 0
        self.stops = []

    def next_deadline(self):
        attack = self.events[self.index][0] if self.index < len(self.events) else self.end
        release = min((stop[0] for stop in self.stops), default=self.end)
        return min(attack, release, self.end)

    def tick(self, now, play, release):
        if now >= self.end:
            return False
        for end, clip, voice, hard in self.stops:
            if end <= now:
                if hard:
                    voice.terminate()
                else:
                    release(clip, voice)
        self.stops = [s for s in self.stops if s[0] > now]
        # A sleeping device must not burst all the notes it missed on waking.
        self.index = bisect_left(self.events, (now - .125,), lo=self.index)
        while self.index < len(self.events) and self.events[self.index][0] <= now:
            _, clip, gain, gate, cut = self.events[self.index]
            self.index += 1
            if cut is not None and cut <= now:
                continue
            voice = play(clip, gain)
            if voice is not None:
                if gate is not None and (cut is None or gate < cut):
                    self.stops.append((gate, clip, voice, False))
                if cut is not None:
                    self.stops.append((cut, clip, voice, True))
        return True
