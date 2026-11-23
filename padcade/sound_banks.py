"""Install a data-only sound bank from a ZIP. Nothing from the archive is executed."""
import filecmp
import io
import re
import shutil
import struct
import subprocess
import threading
import tempfile
import tomllib
import wave
import zipfile
from contextlib import contextmanager
from pathlib import Path

LIMIT = 20 * 2**20


# All uploads and bank imports share one decoder on the Pi.
DECODER = threading.BoundedSemaphore()
CLIP_SECONDS = 20
FORMATS = 'wav,w64,aiff,mp3,ogg,flac,mov,aac,matroska,caf,amr'  # (ffmpeg's names: mov is MP4 and M4A, matroska is WebM too)


def plain(data, target):
    """A WAV already as the mixer plays it (44.1 kHz, 16 bits, stereo), written whole and no longer than a clip may be."""
    try:
        with wave.open(io.BytesIO(data)) as source:
            channels, width, rate, available = source.getparams()[:4]
            if (channels, width, rate) != (2, 2, 44100):
                return False
            frames = min(available, CLIP_SECONDS * rate)
            samples = source.readframes(frames)
            if not frames or len(samples) != frames * channels * width:
                raise ValueError('The WAV has no sound or ends early.')
            with wave.open(str(target), 'wb') as out:
                out.setparams(source.getparams())
                out.writeframes(samples)
            return True
    except (wave.Error, EOFError):
        return False


def convert(data, target, levelled=False):
    """A sound as the mixer plays it, at one loudness. `levelled`: whoever made it has seen to the loudness (the
    page's recorder), so one that is already in the mixer's format is taken as it is, at once."""
    if levelled and plain(data, target):
        return
    if not DECODER.acquire(blocking=False):
        raise ValueError('Another sound is being converted. Try again when it finishes.')
    try:
        if shutil.which('ffmpeg'):
            # From a file beside the result: an M4A often keeps its index at the end, which a pipe cannot reach.
            # Only sound formats, and only this file: a playlist cannot make ffmpeg read anything else.
            source = target.with_name(target.name + '.upload')
            try:
                source.write_bytes(data)
                result = subprocess.run([
                    'ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-filter_threads', '1',
                    '-protocol_whitelist', 'file', '-format_whitelist', FORMATS, '-threads', '1',
                    '-t', str(CLIP_SECONDS), '-i', str(source), '-map', '0:a:0', '-t', str(CLIP_SECONDS),
                    '-af', 'loudnorm=I=-18:TP=-2', '-ar', '44100', '-ac', '2', '-c:a', 'pcm_s16le', '-f', 'wav', str(target)
                ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
            except subprocess.TimeoutExpired:
                raise ValueError('That sound took too long to convert. Try a shorter clip.') from None
            finally:
                source.unlink(missing_ok=True)
            if result.returncode:
                raise ValueError('That file is not a sound the board can read. Try WAV, MP3, M4A, OGG or FLAC.')
        elif not plain(data, target):
            raise ValueError('Without ffmpeg installed the board only takes complete WAV files at 44.1 kHz, 16 bits, stereo.')
    finally:
        DECODER.release()


def unzip(body, most=2**20):
    """The archive, opened only once its list of files is known to be short. Python reads that whole list into
    memory, and one of hundreds of thousands of empty files fits in a small upload but not in a small Pi."""
    end = body.rfind(b'PK\x05\x06', max(0, len(body) - 65557))  # (the end record, where zipfile looks for it)
    if end < 0 or len(body) - end < 22:
        raise ValueError('That is not a ZIP archive.')
    listed, = struct.unpack_from('<L', body, end + 12)
    if listed > most or body[max(0, end - 20):end - 16] == b'PK\x06\x07':  # (the second: a list too long for the plain record)
        raise ValueError('That archive lists too many files.')
    return zipfile.ZipFile(io.BytesIO(body))


def read_bank(body, staging, convert):
    with unzip(body) as archive:
        entries = archive.infolist()
        names = set(archive.namelist())
        if (len(entries) > 69 or len(names) != len(entries) or sum(item.file_size for item in entries) > LIMIT
                or 'bank.toml' not in names or archive.getinfo('bank.toml').file_size > 65536):
            raise ValueError('Use a ZIP with one bank.toml at the root, up to 64 WAVs and at most 20 MiB unpacked.')
        manifest = tomllib.loads(archive.read('bank.toml').decode('utf-8'))
        name, pads = manifest.get('name'), manifest.get('pads')
        if not isinstance(name, str) or not name.strip() or len(name) > 48 or not name.isprintable():
            raise ValueError('Give the bank a name of 1 to 48 characters.')
        if not isinstance(pads, dict) or not 1 <= len(pads) <= 64:
            raise ValueError('The bank needs between one and 64 pads.')
        layout, files = {}, {}
        for pos, pad in pads.items():
            if not re.fullmatch(r'[1-8],[1-8]', pos) or not isinstance(pad, dict) or pad.keys() - {'file', 'mode', 'colour', 'dynamics'}:
                raise ValueError('Use pad positions from 1,1 to 8,8 and file, mode, colour or dynamics for their settings.')
            filename = pad.get('file')
            if (not isinstance(filename, str) or not re.fullmatch(r'[\w-][\w -]{0,39}\.wav', filename, re.I)
                    or filename[:-4].endswith(' ') or filename not in names):
                raise ValueError(f'Pad {pos} needs a WAV filename at the root of the ZIP.')
            if pad.get('dynamics', 'auto') not in ('auto', 'velocity', 'fixed'):
                raise ValueError(f'Choose auto, velocity or fixed for the dynamics of pad {pos}.')
            mode, colour = pad.get('mode', 'once'), pad.get('colour')
            if mode not in ('once', 'hold', 'loop') or ('colour' in pad and
                    (not isinstance(colour, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', colour))):
                raise ValueError(f'Check the playback mode and colour of pad {pos}.')
            layout[pos] = {k: v for k, v in pad.items() if k != 'file'}
            layout[pos]['thing'] = filename
            files[filename] = staging / filename
        extras = names - files.keys() - {'bank.toml', 'LICENSE', 'LICENSE.txt', 'README.md', 'CREDITS.md'}
        if extras:
            raise ValueError('Only the manifest, referenced WAVs and optional credits belong in a bank ZIP.')
        total = 0
        for filename, target in files.items():
            data = archive.read(filename)
            with wave.open(io.BytesIO(data)) as wav:
                channels, width, rate, frames = wav.getparams()[:4]
                if (channels not in (1, 2) or width not in (1, 2, 3, 4) or rate <= 0 or not 0 < frames <= 20 * rate
                        or len(wav.readframes(frames)) != frames * channels * width):
                    raise ValueError(f'{filename} needs a complete mono or stereo PCM WAV, up to 20 seconds long.')
            if (channels, width, rate) == (2, 2, 44100):
                target.write_bytes(data)
            else:
                convert(data, target)
            total += target.stat().st_size
            if total > LIMIT:
                raise ValueError('The playable audio in one bank must fit in 20 MiB.')
    return {'name': name.strip(), 'pads': layout}, files


def install_bank(plugin, layout, files):
    # Reserve new names even when a factory WAV has not finished seeding yet.
    pending, ids = {}, {}
    for filename, source in files.items():
        stem = source.stem
        number = 1
        while True:
            suffix = f' {number}' if number > 1 else ''
            clip = stem[:40-len(suffix)] + suffix
            target = plugin.clips / (clip + '.wav')
            if target in pending:
                existing = pending[target]
            elif target.exists():
                existing = target
            elif clip not in plugin.clip_groups:
                pending[target] = source
                break
            else:
                number += 1
                continue
            if filecmp.cmp(source, existing, shallow=False):
                break
            number += 1
        ids[filename] = clip
    for pad in layout['pads'].values():
        pad['thing'] = ids[pad['thing']]
    layout['pack_clips'] = sorted(set(ids.values()))
    index = None
    for i, bank in enumerate(plugin.banks()):
        if bank['name'].casefold() == layout['name'].casefold():
            if bank != layout:
                raise ValueError(f'A different bank is already named {layout["name"]}. Rename the bank before importing.')
            index = i
            break
    installed = []
    try:
        for target, source in pending.items():
            target.hardlink_to(source)
            installed.append(target)
        if index is None:
            index = plugin.board.append_bank(layout)
    except Exception:
        for target in installed:
            target.unlink(missing_ok=True)
        raise
    return {'name': layout['name'], 'bank': index}


@contextmanager
def prepare_bank(body, plugin, convert):
    with tempfile.TemporaryDirectory(dir=plugin.board.data) as staging:
        try:
            layout, files = read_bank(body, Path(staging), convert)
        except (EOFError, wave.Error, zipfile.BadZipFile, UnicodeError, ValueError, RuntimeError) as error:
            message = str(error) or 'The bank ZIP contains an incomplete WAV.'
            yield lambda: {'error': message}
            return

        def finish():
            with plugin.clip_lock, plugin.seed_lock:
                try:
                    return install_bank(plugin, layout, files)
                except ValueError as error:
                    return {'error': str(error)}

        yield finish
