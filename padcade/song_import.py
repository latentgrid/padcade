"""Bring an exported song back from its ZIP. Nothing from the archive is executed."""
import filecmp
import hashlib
import io
import json
import re
import tempfile
import wave
import zipfile

from .sound_banks import unzip
from contextlib import contextmanager
from pathlib import Path

from .song_book import BEATS, song_ok

LIMIT = 60 * 2**20
MAX_ARCHIVE = 80 * 2**20  # Includes rendered stems, MIDI and the index alongside the 60 MiB of playable samples.
MAX_PROJECT, MAX_CLIPS = 2**20, 256
TOO_BIG = 'The sounds of one song must fit in 60 MiB.'
NOT_A_SONG = 'Choose a song exported from the Soundboard: a ZIP with its project.json.'
EVENT = ('beat', 'clip', 'take', 'gain', 'length', 'pedalDown', 'sustain')


def read_song(body, staging, convert):
    """The song as the studio keeps it, and a WAV for each of its sounds: {clip: file}."""
    with unzip(body) as archive:
        names = set(archive.namelist())
        if 'project.json' not in names or archive.getinfo('project.json').file_size > MAX_PROJECT:
            raise ValueError(NOT_A_SONG)
        project = json.loads(archive.read('project.json'))
        if not isinstance(project, dict) or project.get('loopBeats') != BEATS or project.get('format') != 'Padcade song 1':
            raise ValueError(NOT_A_SONG)
        tracks, sounds = project.get('tracks'), project.get('sounds')
        if (not isinstance(tracks, list) or not isinstance(sounds, dict) or not isinstance(project.get('title'), str)
                or not all(isinstance(t, dict) and isinstance(t.get('name'), str) and isinstance(t.get('events'), list)
                           and all(isinstance(e, dict) for e in t['events']) for t in tracks)):
            raise ValueError(NOT_A_SONG)
        song = {'title': project['title'].strip()[:48] or 'Imported song', 'bpm': project.get('bpm'), 'tracks': [
            {'name': t['name'].strip()[:32] or 'Track', 'timing': t.get('timing'), 'muted': t.get('muted'),
             'events': [{key: e[key] for key in EVENT if key in e} for e in t['events']]} for t in tracks]}
        # (printable: a name with half a character in it could never be saved)
        if not song_ok(song) or not all(name.isprintable() for name in [song['title']] + [t['name'] for t in song['tracks']]):
            raise ValueError('The song in this ZIP cannot be read.')
        files, total = {}, 0
        clips = sorted({e['clip'] for t in song['tracks'] for e in t['events']})
        if len(clips) > MAX_CLIPS:
            raise ValueError('A song can bring up to 256 sounds.')
        for clip in clips:
            source = sounds[clip].get('file') if isinstance(sounds.get(clip), dict) else None
            if (not re.fullmatch(r'[\w-][\w -]{0,39}|Song sample [0-9a-f]{64}', clip) or clip.endswith(' ')
                    or not isinstance(source, str) or source not in names):
                raise ValueError(f'The ZIP does not have the sound {clip[:60]!r} of this song.')
            if total + archive.getinfo(source).file_size > LIMIT:
                raise ValueError(TOO_BIG)
            data = archive.read(source)
            with wave.open(io.BytesIO(data)) as wav:
                channels, width, rate, frames = wav.getparams()[:4]
                if (channels not in (1, 2) or width not in (1, 2, 3, 4) or rate <= 0 or not 0 < frames <= 20 * rate
                        or len(wav.readframes(frames)) != frames * channels * width):
                    raise ValueError(f'{source} needs to be a complete mono or stereo PCM WAV, up to 20 seconds long.')
            files[clip] = staging / f'{len(files)}.wav'
            if (channels, width, rate) == (2, 2, 44100):
                files[clip].write_bytes(data)
            else:
                convert(data, files[clip])
            total += files[clip].stat().st_size  # (as it will be played: a small odd WAV grows when converted)
            if total > LIMIT:
                raise ValueError(TOO_BIG)
    return song, files


def install_song(plugin, song, files):
    studio = plugin.studio
    if studio.record_at is not None or studio.performance is not None:
        return {'error': 'Finish recording before importing a song.'}
    installed, names, slot = [], {}, None
    try:
        for clip, source in files.items():
            target = plugin.clips / (clip + '.wav')
            same = target.exists() and filecmp.cmp(source, target, shallow=False)
            if not same and (target.exists() or clip in plugin.clip_groups):
                # The name belongs to another sound here (or to a factory sound still being unpacked): this one
                # is kept as the song's own, under a name made from what it sounds like.
                names[clip] = 'Song sample ' + hashlib.sha256(source.read_bytes()).hexdigest()
                target = plugin.clips / (names[clip] + '.wav')
            if plugin.captured(target.stem):
                plugin.has_private_samples = True  # (cleared away again once no song uses it)
            if not target.exists():
                target.hardlink_to(source)
                installed.append(target)
        for track in song['tracks']:
            for event in track['events']:
                event['clip'] = names.get(event['clip'], event['clip'])
        slot = studio.add_song(song)
    except OSError:
        return {'error': 'Could not save the song. Check free space and try again.'}
    finally:
        if slot is None:  # (not taken in, for whatever reason: none of its sounds stay)
            for target in installed:
                target.unlink(missing_ok=True)
    if slot is None:
        return {'error': 'All 16 song slots are in use. Delete a song first.'}
    return {'slot': slot, 'title': song['title']}


@contextmanager
def prepare_song(body, plugin, convert):
    with tempfile.TemporaryDirectory(dir=plugin.board.data) as staging:
        try:
            song, files = read_song(body, Path(staging), convert)
        except (EOFError, wave.Error, zipfile.BadZipFile, UnicodeError, ValueError, RuntimeError, RecursionError) as error:
            message = str(error) if type(error) is ValueError and str(error) else NOT_A_SONG
            yield lambda: {'error': message}
            return

        def finish():
            with plugin.clip_lock, plugin.seed_lock:
                return install_song(plugin, song, files)

        yield finish
