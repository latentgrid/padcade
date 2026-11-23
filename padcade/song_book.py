"""Validate the on-disk song index before handing its slots or undo history to the recorder."""
import json
import math
import re
from uuid import uuid4

BEATS = 8
MIN_BPM, MAX_BPM = 45, 200
TIMING = ('As played', 'Gentle', 'Fine', 'Tight')  # in the order Quantise steps through them


def index(value, size):
    return type(value) is int and 0 <= value < size


def number(value):
    return type(value) in (int, float) and 0 <= value < math.inf


def track_ok(track, *, max_beat=BEATS):
    if not (isinstance(track, dict) and isinstance(track.get('name'), str) and track.get('timing') in TIMING
            and isinstance(track.get('muted'), bool) and isinstance(track.get('events'), list)):
        return False
    for hit in track['events']:
        if not (isinstance(hit, dict) and isinstance(hit.get('clip'), str)
                and number(hit.get('beat')) and hit['beat'] <= max_beat and type(hit.get('take')) is int
                and all(number(hit[key]) and hit[key] <= max_beat
                        for key in ('length', 'pedalDown', 'sustain', 'hardStop') if key in hit)
                and number(hit.get('gain', 1)) and hit.get('gain', 1) <= 1):
            return False
        if hit.get('sustain') and ('pedalDown' not in hit or 'length' not in hit):
            return False
    return True


def take_ok(take):
    return (isinstance(take, dict) and isinstance(take.get('id'), str) and re.fullmatch('[0-9a-f]{32}', take['id'])
            and isinstance(take.get('title'), str) and number(take.get('duration'))
            and isinstance(take.get('clips'), list) and all(isinstance(clip, str) for clip in take['clips']))


def song_ok(song):
    if not (isinstance(song, dict) and isinstance(song.get('title'), str) and type(song.get('bpm')) is int
            and MIN_BPM <= song['bpm'] <= MAX_BPM and isinstance(song.get('tracks'), list) and len(song['tracks']) == 8
            and all(track_ok(track) for track in song['tracks'])):
        return False
    takes = song.get('takes', [])
    deleted = song.get('deletedTake')
    return (isinstance(takes, list) and all(take_ok(take) for take in takes)
            and type(song.get('nextTake', 1)) is int and song.get('nextTake', 1) > 0
            and (deleted is None or isinstance(deleted, dict) and index(deleted.get('index'), len(takes) + 1)
                 and take_ok(deleted.get('item'))))


def valid(book):
    if not isinstance(book, dict) or 'slot' not in book:
        return False
    slots, selected, undo = book.get('slots'), book['slot'], book.get('undo')
    if not (isinstance(slots, list) and len(slots) == 16 and all(s is None or song_ok(s) for s in slots)
            and (selected is None or index(selected, len(slots)) and slots[selected] is not None)):
        return False
    if undo is None:
        return True
    if not isinstance(undo, dict) or not index(undo.get('slot'), len(slots)):
        return False
    if undo.get('kind') == 'song':
        return slots[undo['slot']] is None and song_ok(undo.get('item'))
    return (undo.get('kind') == 'track' and slots[undo['slot']] is not None and index(undo.get('track'), 8)
            and track_ok(undo.get('item')))


def read_book(path):
    try:
        book = json.loads(path.read_text())
        if valid(book):
            return book, None
    except FileNotFoundError:
        return None, None
    except (ValueError, RecursionError):
        pass
    broken = path.with_suffix('.broken')
    if broken.exists():
        broken = path.with_suffix('.broken.' + uuid4().hex)
    path.replace(broken)
    return None, f'The song index could not be read. The original is in {broken.name}. A new song book is ready.'
