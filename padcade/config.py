"""config.toml, the one file that holds what the owner set: read with the standard library, written here (it has no
TOML writer)."""
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import tomllib
import wave
from pathlib import Path

UNPAIRED = 'local owner setup required'  # Locks a pre-pairing release after rollback from an unreadable layout.
PASSWORD_ROUNDS = 100_000  # Keep existing owner's password verifiers valid.

HEADER = '''# Everything set on the management page is in this file, and the page writes it: edit either.
# Round buttons are top1-top8 and bottom1-bottom8 (left to right), left1-left8 and right1-right8 (top to bottom).
# Pads are "column,row", both 1-8, counted from the bottom left. Comments you add are lost when the page saves.
'''


def load(path):
    """What the file says, {} without one. A file with a mistake is set aside as config.broken, not overwritten."""
    try:
        return tomllib.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        broken = path.with_suffix('.broken')
        path.replace(broken)
        print(f'{path.name} has a mistake ({error}): kept as {broken.name}, starting with the defaults', flush=True)
        return {}


# The version-12 grid is also the input for upgrades from earlier releases.
_PRE_KIT_ROWS = (
    (('#ff9838', 'Kick|Snare|Closed hat|Open hat'), ('#49bac4', 'Pedal hat|Crash|Splash|Ride bell')),
    (('#ffd23f', 'Clap|Shaker|Low tom|High tom|Floor tom|Mid tom'), ('#db8be3', 'Electric kick|Electric snare')),
    (('#ff9838', 'Deep kick|Rim click|Tambourine|Ride'), ('#7bd48f', 'Cowbell|Claves|High block|Low block')),
    (('#ef9270', 'Low bongo|High bongo|Low conga|Open conga|Mute conga'), ('#7bd48f', 'Short guiro'), ('#49bac4', 'Mute triangle|Open triangle')),
    (('#db8be3', 'Punch kick|Snap snare|Electric closed hat|Electric open hat|Sub kick|Electric clap|Electric rim|Electric shaker'),),
    (('#db8be3', 'Click kick|Noise snare|Low zap tom|High zap tom|Pulse bell'), ('#ffba80', 'Snare flam|Hat double|Kick double')),
    (('#db8be3', 'Classic kick|Electric kick C1|Electric kick G1|Electric kick C2|Electric kick G2'),),
)


def upgrade_soundboard(table, defaults, data):
    previous = table.get('factory_banks', 1)
    try:
        _upgrade_string_edges(data / 'clips')
        table['factory_banks'] = defaults['factory_banks']
    except OSError as error:
        # Leave a pending audio upgrade if the card is full. Other bank upgrades still run.
        print(f'Factory string update will retry on restart: {error}', flush=True)
        table['factory_banks'] = 16
    if previous == 16:
        return
    banks = table.setdefault('banks', [])
    if not isinstance(banks, list) or not all(isinstance(bank, dict) and isinstance(bank.get('name'), str) for bank in banks):
        return
    stock = {b['name']: b for b in defaults['banks']}
    if previous == 15:
        _upgrade_v15_chords(table, stock['All chords']['pads'])
        if (data / 'clips').is_dir() and not any(p.is_dir() for p in data.glob('.*-sounds')):
            (data / '.add-high-chord-sounds').mkdir(exist_ok=True)
        return
    # The old melodic and chord rows still identify earlier upgrades, including renamed banks.
    old_synth = {}
    voices = (('Studio bass', 2, '#a070ff'), ('Bright pluck', 4, '#3fd0ff'),
              ('Soft keys', 4, '#5fe07a'), ('Warm pad', 4, '#6498ff'))
    for y, (voice, octave, colour) in enumerate(voices, 1):
        for x, note in enumerate('CDEFGABC', 1):
            pad = {'thing': f'{voice} {note}{octave + (x == 8)}', 'colour': colour}
            if y >= 3:
                pad['mode'] = 'hold'
            old_synth[f'{x},{y}'] = pad
    for y, row in ((5, 'C3 Dm Em F G Am Bdim C'), (6, 'Cm Eb Ab Bb')):
        for x, chord in enumerate(row.split(), 1):
            label = {'C3': 'C', 'C': 'High C'}.get(chord, chord.replace('b','♭'))
            old_synth[f'{x},{y}'] = {'thing': 'Chord ' + chord, 'name': label, 'mode': 'hold',
                                     'colour': '#ffba80' if y == 5 else '#ff7abb'}

    instrument = stock['Synth']
    modern = _factory_pads(banks, 'Synth', instrument['pads'])
    have_grid = modern is not None and all(isinstance(modern.get(pos), dict)
                                           and modern[pos].get('thing') == pad['thing']
                                           for pos, pad in instrument['pads'].items())
    if previous < 13:
        count = len(banks)
        _upgrade_through_v13(table, stock, old_synth, data, previous)
        banks = table['banks']
        if have_grid:
            # An older migration can offer its own instrument. A complete modern grid already serves that role.
            banks[count:] = [bank for bank in banks[count:] if bank.get('pads') != old_synth]
    current = _factory_pads(banks, 'Synth', old_synth)
    replacement = next((b for b in banks if 'pack_clips' not in b and _stock_v14_synth(b.get('pads'))), None)
    if replacement is None and current == old_synth:
        replacement = next(b for b in banks if b.get('pads') is current)
    if replacement is not None:
        replacement.update(pads=instrument['pads'], family='Synth')
    elif not have_grid and (current is not None or modern is not None or any(b['name'] == 'Synth' for b in banks)):
        banks.append(_new_bank(banks, instrument))
    for name in ('All chords', 'Synth high'):
        if not any(b.get('pads') == stock[name]['pads'] and 'pack_clips' not in b for b in banks):
            banks.append(_new_bank(banks, stock[name]))
    if (data / 'clips').is_dir() and not any(p.is_dir() for p in data.glob('.*-sounds')):
        marker = '.add-chromatic-bank-sounds' if previous >= 14 else '.add-synth-grid-sounds'
        (data / marker).mkdir(exist_ok=True)


def _upgrade_string_edges(clips):
    from .synth import RATE, STRING_CLIPS, fade_string_edges, normalized_note, stereo

    for name in STRING_CLIPS:
        path = clips / (name + '.wav')
        try:
            with wave.open(str(path), 'rb') as wav:
                if wav.getparams()[:3] != (2, 2, RATE):
                    continue
                mono = normalized_note(name)
                if wav.getnframes() != len(mono) or wav.readframes(len(mono)) != stereo(mono):
                    continue
        except (FileNotFoundError, EOFError, wave.Error):
            continue
        fade_string_edges(mono)
        fd, name = tempfile.mkstemp(dir=clips)
        temp = Path(name)
        try:
            with os.fdopen(fd, 'wb') as out:
                with wave.open(out, 'wb') as wav:
                    wav.setparams((2, 2, RATE, 0, 'NONE', ''))
                    wav.writeframes(stereo(mono))
                out.flush()
                os.fsync(out.fileno())
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def _upgrade_v15_chords(table, all_pads):
    """Replace exact stock layouts and keep shortcuts aimed at the instrument they used to open."""
    banks = table['banks']
    basic_hash = '5a58010db576979a12f32f005465f13a0f40224cae334d11e9e3b34328cf3a55'
    full_hash = '364a35b66f06421dcc3fa05430afcf506985f365516adc5d27a0abc1119a151e'
    old = target = None
    for i, bank in enumerate(banks):
        if bank.get('family') != 'Synth' or 'pack_clips' in bank:
            continue
        name = re.sub(r' \(factory(?: \d+)?\)$', '', bank['name'])
        pads = bank.get('pads')
        if not isinstance(pads, dict):
            continue
        # Include labels, gates and colours when recognising a historical factory grid.
        digest = hashlib.sha256(json.dumps(pads, sort_keys=True, default=str).encode()).hexdigest()
        if name == 'Chords' and digest == basic_hash and set(bank) == {'name', 'pads', 'family'}:
            old = i
        if name == 'All chords' and digest == full_hash:
            bank['pads'] = all_pads
        if name == 'All chords' or digest == full_hash or pads == all_pads:
            target = i
    if old is None or target is None:
        return

    count = len(banks)
    banks.pop(old)
    def redirect(action):
        match = re.fullmatch(r'bank:([0-9]{1,6})', action) if isinstance(action, str) else None
        if not match:
            return action
        index = int(match[1])
        if index < old or index >= count:
            return action
        if index == old:
            index = target
        return f'bank:{index - (index > old)}'

    def redirect_pads(pads):
        if isinstance(pads, dict):
            for pad in pads.values():
                if isinstance(pad, dict):
                    if 'action' in pad:
                        pad['action'] = redirect(pad['action'])
                    redirect_pads(pad.get('sub'))

    for key, action in table.get('buttons', {}).items():
        table['buttons'][key] = redirect(action)
    for bank in banks:
        redirect_pads(bank.get('pads'))


def _stock_v14_synth(pads):
    # The entire previous 64-pad factory map, including customisable colours, labels and gates, must match.
    if not isinstance(pads, dict) or len(pads) != 64:
        return False
    digest = hashlib.sha256(json.dumps(pads, sort_keys=True, default=str).encode()).hexdigest()
    return digest == '024a225dd5eda1c1dd3c6beb4feb07594c6258eeb67bbdca989e7203487e8269'


def _upgrade_through_v13(table, stock, old_synth, data, previous):
    banks = table['banks']
    from .synth import RESTORED_EFFECT_CLIPS
    memes = stock['Memes']['pads']
    old_memes = {pos: pad for pos, pad in memes.items() if pad['thing'] not in RESTORED_EFFECT_CLIPS}
    historic = {}
    for y, groups in enumerate(_PRE_KIT_ROWS, 1):
        row = ((name, colour) for colour, sounds in groups for name in sounds.split('|'))
        for x, (name, colour) in enumerate(row, 1):
            historic[f'{x},{y}'] = {'thing': name, 'colour': colour}
    if previous < 12:
        old_pack = [stock[name] for name in ('Drums', 'Synth', 'Effects', 'Memes', 'Keys')]
        previous_layouts = {'Drums': historic, 'Memes': old_memes, 'Synth': old_synth}
        old_pack = [b | {'pads': previous_layouts.get(b['name'], b['pads'])} for b in old_pack]
        _upgrade_through_v12(table, old_pack, data, previous)
        banks = table['banks']
    # Reorder only a completely stock grid. Custom pad positions, actions and colours belong to the player.
    pads = _factory_pads(banks, 'Drums', historic)
    if pads == historic:
        pads.clear()
        pads.update(stock['Drums']['pads'])
    pads = _factory_pads(banks, 'Memes', old_memes)
    if pads is not None:
        _add_factory_pads(pads, memes, RESTORED_EFFECT_CLIPS)
    jam = stock['Jam classics']
    if not any(b.get('pads') == jam['pads'] and 'pack_clips' not in b for b in banks):
        banks.append(_new_bank(banks, jam))
    if (data / 'clips').is_dir() and not any(p.is_dir() for p in data.glob('.*-sounds')):
        (data / '.add-jam-sounds').mkdir(exist_ok=True)


def _upgrade_through_v12(table, pack, data, previous):
    banks = table['banks']
    from .synth import ELECTRONIC_DRUM_CLIPS, STUDIO_BASS_CLIPS, TUNED_KICK_CLIPS
    drums = next(bank['pads'] for bank in pack if bank['name'] == 'Drums')
    instrument = next(bank['pads'] for bank in pack if bank['name'] == 'Synth')
    additions = TUNED_KICK_CLIPS if previous < 11 else frozenset()
    if previous < 10:
        additions |= ELECTRONIC_DRUM_CLIPS
    old_drums = {pos: pad for pos, pad in drums.items() if pad['thing'] not in additions}
    previous_bass = {10: 'Plucked bass ', 11: 'Finger bass '}.get(previous, 'Round bass ')
    old_synth = instrument.copy()
    for pos, pad in instrument.items():
        if pad['thing'] in STUDIO_BASS_CLIPS:
            old_synth[pos] = pad | {'thing': pad['thing'].replace('Studio bass ', previous_bass)}
    if previous < 9:
        # Upgrade historical layouts against the positions and names they originally shipped with.
        legacy = {'Synth': old_synth, 'Drums': old_drums}
        previous_pack = [bank | {'pads': legacy.get(bank['name'], bank['pads']).copy()} for bank in pack]
        _upgrade_through_v9(table, previous_pack, data, previous)
    banks = table['banks']
    pads = _factory_pads(banks, 'Drums', old_drums)
    if pads is not None:
        _add_factory_pads(pads, drums, additions)
    clips = data / 'clips'
    pending = any(p.is_dir() for p in data.glob('.*-sounds'))
    pads = _factory_pads(banks, 'Synth', old_synth)
    if pads is not None:
        assigned = {pad.get('thing') for pad in pads.values() if isinstance(pad, dict) and isinstance(pad.get('thing'), str)}
        for pos, old in old_synth.items():
            new = instrument[pos]['thing']
            current = pads.get(pos)
            if (new not in STUDIO_BASS_CLIPS or new in assigned or not isinstance(current, dict)
                    or current.get('thing') != old['thing']):
                continue
            path = clips / (old['thing'] + '.wav')
            if path.exists():
                if not _factory_recording(path):
                    continue
            elif clips.is_dir() and not pending:
                continue  # A finished library may have had this sound deliberately removed.
            replacement = clips / (new + '.wav')
            if not replacement.exists() or _factory_recording(replacement):
                current['thing'] = new
    if clips.is_dir() and not pending:
        marker = {10: '.add-tuned-sounds', 11: '.add-studio-bass-sounds'}.get(previous, '.add-electronic-sounds')
        (data / marker).mkdir(exist_ok=True)


def _factory_recording(path):
    """Identify a stock clip without overwriting a user recording under either instrument name."""
    from .synth import factory_clips
    try:
        with wave.open(str(path), 'rb') as wav:
            if wav.getnchannels() != 2 or wav.getsampwidth() != 2:
                return False
            _, rate, pcm = next(factory_clips(path.parent, [path.stem]))
            return (wav.getframerate() == rate and wav.getnframes() * 4 == len(pcm)
                    and wav.readframes(wav.getnframes()) == pcm)
    except (OSError, EOFError, wave.Error):
        return False


def _add_factory_pads(pads, stock, additions):
    assigned = {pad['thing'] for pad in pads.values() if isinstance(pad, dict) and isinstance(pad.get('thing'), str)}
    for pos, pad in stock.items():
        if pad['thing'] in additions and pad['thing'] not in assigned:
            pads.setdefault(pos, pad)


def _upgrade_through_v9(table, pack, data, previous):
    if previous < 7:
        _upgrade_older_banks(table, pack, data, previous)
    banks = table['banks']
    synth = next(bank['pads'] for bank in pack if bank['name'] == 'Synth')
    pads = _factory_pads(banks, 'Synth', synth)
    if pads is not None:
        for pos, pad in synth.items():
            current = pads.get(pos)
            if (pad['thing'].startswith('Soft keys ') and isinstance(current, dict)
                    and current.get('thing') == pad['thing']):
                current.setdefault('mode', 'hold')

    if previous >= 8:
        return
    from .synth import DRUM_FACTORY_CLIPS
    drums = next(bank['pads'] for bank in pack if bank['name'] == 'Drums')
    previous_drums = {pos: pad for pos, pad in drums.items() if pad['thing'] not in DRUM_FACTORY_CLIPS}
    pads = _factory_pads(banks, 'Drums', previous_drums)
    if pads is not None:
        _add_factory_pads(pads, drums, DRUM_FACTORY_CLIPS)

    chords = {pos: pad for pos, pad in synth.items() if pad['thing'].startswith('Chord ')}
    destinations = chords.keys() - _OLD_CHORD_ROW.keys()
    melody = {pos: pad for pos, pad in synth.items() if pos not in chords}
    pads = _factory_pads(banks, 'Synth', melody | _OLD_CHORD_ROW)
    # Move a complete factory row only when none of its destinations is already in use.
    if (pads is not None and destinations.isdisjoint(pads)
            and all(pads.get(pos) == old for pos, old in _OLD_CHORD_ROW.items())):
        pads.update(chords)
    if previous == 7 and (data / 'clips').is_dir() and not any(p.is_dir() for p in data.glob('.*-sounds')):
        (data / '.add-rhythm-sounds').mkdir(exist_ok=True)


_OLD_CHORD_ROW = {
    f'{x},5': {'thing': 'Chord ' + name, 'mode': 'hold', 'colour': '#ff7abb' if x <= 4 else '#ffba80'}
    for x, name in enumerate(('Cm', 'Eb', 'Ab', 'Bb', 'C', 'G', 'Am', 'F'), 1)
}


def _factory_pads(banks, name, stock):
    """Identify a renamed factory bank by the surviving notes in their original positions."""
    candidates = []
    for bank in banks:
        pads = bank.get('pads')
        if not isinstance(pads, dict) or 'pack_clips' in bank:
            continue
        matched = sum(isinstance(pads.get(pos), dict) and pads[pos].get('thing') == pad['thing']
                      for pos, pad in stock.items())
        named = bank['name'] == name or re.fullmatch(re.escape(name) + r' \(factory(?: \d+)?\)', bank['name']) is not None
        if matched >= 4 or (named and matched):
            candidates.append((matched, named, pads))
    if candidates:
        return max(reversed(candidates), key=lambda candidate: candidate[:2])[2]
    return None


def _new_bank(banks, bank):
    names = {b['name'] for b in banks}
    base = name = bank['name']
    if name in names:
        name = f'{base} (factory)'
        i = 2
        while name in names:
            name = f'{base} (factory {i})'
            i += 1
    return dict(bank, name=name)


def _upgrade_older_banks(table, pack, data, previous):
    banks = table['banks']

    def covers_layout(bank, target):
        pads = bank.get('pads')
        if not isinstance(pads, dict):
            return False
        expected = target['pads']
        if target['name'] == 'Synth' and all(isinstance(pads.get(pos), dict) and pads[pos].get('thing') == pad['thing']
                                             for pos, pad in _OLD_CHORD_ROW.items()):
            expected = {pos: pad for pos, pad in expected.items() if not pad['thing'].startswith('Chord ')}
        for pos, pad in expected.items():
            names = (pad['thing'],)
            if target['name'] == 'Synth' and pad['thing'].startswith('Round bass '):
                pitch = pad['thing'].removeprefix('Round bass ')
                names += tuple(voice + pitch for voice in ('Plucked bass ', 'Finger bass ', 'Studio bass '))
            current = pads.get(pos)
            if not isinstance(current, dict) or current.get('thing') not in names:
                return False
        return True

    if previous == 6:
        from .synth import CHORD_FACTORY_CLIPS
        melody = next(b['pads'] for b in pack if b['name'] == 'Synth')
        stock = {pos: pad for pos, pad in melody.items() if not pad['thing'].startswith('Chord ')} | _OLD_CHORD_ROW
        candidates = []
        for bank in banks:
            pads = bank.get('pads')
            if not isinstance(pads, dict) or 'pack_clips' in bank:
                continue
            matched = {pos for pos, pad in stock.items() if isinstance(pads.get(pos), dict)
                       and pads[pos].get('thing') == pad['thing'] and pad['thing'] not in CHORD_FACTORY_CLIPS}
            # Recognise a renamed instrument by its chord or melody row. Prefer the closest factory layout.
            named = bank['name'] == 'Synth' or re.fullmatch(r'Synth \(factory(?: \d+)?\)', bank['name'])
            if named or all(f'{x},5' in matched for x in range(1, 5)) or len(matched) >= 8:
                candidates.append((len(matched), pads))
        if candidates:
            _, pads = max(reversed(candidates), key=lambda candidate: candidate[0])
            assigned = {p['thing'] for p in pads.values() if isinstance(p, dict) and isinstance(p.get('thing'), str)}
            for pos, pad in stock.items():
                if pad['thing'] in CHORD_FACTORY_CLIPS and pad['thing'] not in assigned:
                    pads.setdefault(pos, pad)
        if (data / 'clips').is_dir() and not any(p.is_dir() for p in data.glob('.*-sounds')):
            (data / '.add-chord-sounds').mkdir(exist_ok=True)
        return

    if previous >= 2:
        # Only complete old factory layouts can be replaced in place. Names, positions and edits belong to the owner.
        old_layouts = {
            'Synth': {'7b4441ae9b5b32fe5a1d3df7d36388eb666f342d7515a53f8123b2961794d54e',
                      'b68beae8d56abf5dde59b5107c8032454fccd72e1982459b361ff13f4fd1c872'},
            'Keys': {'eb3f51613063aba218aec426459b48ab0097fce761819832861f3fbd35f56159',
                     '98a62d090ec82c608457e4c44d239f00defb368eb92d8ca8c4fd4568421613c5'},
        }
        for name, layouts in old_layouts.items():
            target = next(b for b in pack if b['name'] == name)
            present = False
            offer = name == 'Keys'
            original = None
            for bank in banks:
                pads = bank.get('pads')
                if not isinstance(pads, dict):
                    continue
                if name == 'Synth':
                    offer |= (bank['name'] == name or bank['name'].startswith('Synth (factory') or
                              any(isinstance(pad, dict) and isinstance(pad.get('thing'), str)
                                  and pad['thing'].startswith(('Round bass ', 'Soft keys ', 'Warm pad ', 'Chord '))
                                  for pad in pads.values()))
                if covers_layout(bank, target):
                    present = True
                    break
                if hashlib.sha256(json.dumps(pads, sort_keys=True, default=str).encode()).hexdigest() in layouts:
                    original = bank
            if present:
                continue
            if original is not None:
                original['pads'] = target['pads']
            elif offer:
                banks.append(_new_bank(banks, target))
        if previous >= 3:
            earlier = ('.first-sounds', '.add-pack-sounds', '.expand-pack-sounds', '.add-keys-sounds', '.add-chromatic-sounds')
            if (data / 'clips').is_dir() and not any((data / marker).is_dir() for marker in earlier):
                (data / '.add-piano-sounds').mkdir(exist_ok=True)
            return
        from .synth import EXPANDED_FACTORY_CLIPS
        for bank in pack:
            if bank['name'] not in ('Drums', 'Effects'):
                continue
            additions = {pos: pad for pos, pad in bank['pads'].items() if pad['thing'] in EXPANDED_FACTORY_CLIPS}
            if not additions:
                continue
            name = bank['name']
            # An older upgrade kept a same-name owner bank and gave the factory bank a suffix.
            suffix = re.compile(re.escape(name) + r' \(factory(?: \d+)?\)')
            matches = [b for b in banks if b['name'] == name or suffix.fullmatch(b['name'])]
            if not matches:
                continue  # Removed or renamed banks remain the owner's layout.
            # If names collide, prefer the bank that still has most of its factory sounds in place.
            # A factory bank added by the previous upgrade came after the owner's same-name banks.
            def original_pads(candidate):
                pads = candidate.get('pads')
                if not isinstance(pads, dict):
                    return -1
                return sum(isinstance(pads.get(pos), dict) and pads[pos].get('thing') == pad['thing']
                           for pos, pad in bank['pads'].items() if pos not in additions)
            target = max(reversed(matches), key=original_pads)
            pads = target.setdefault('pads', {})
            if isinstance(pads, dict):
                assigned = {p['thing'] for p in pads.values() if isinstance(p, dict) and isinstance(p.get('thing'), str)}
                for pos, pad in additions.items():
                    if pad['thing'] not in assigned:
                        pads.setdefault(pos, pad)
        if (data / 'clips').is_dir() and not any((data / marker).is_dir() for marker in ('.first-sounds', '.add-pack-sounds')):
            (data / '.expand-pack-sounds').mkdir(exist_ok=True)
        return
    memes = next(bank for bank in pack if bank['name'] == 'Memes')
    sounds = dict(memes, name='Sounds')
    with_coin = dict(sounds, pads=sounds['pads'] | {'3,7': {'thing': 'Coin'}})
    old_sounds = next((i for i, bank in enumerate(banks) if bank == sounds or bank == with_coin), None)
    # Match the complete original Jam layout, including colours and modes, before replacing it.
    stock_pair = (old_sounds == 0 and len(banks) == 2 and
                  hashlib.sha256(json.dumps(banks[1], sort_keys=True, default=str).encode()).hexdigest() ==
                  'a6b1f65f893c9cead6afd173137b29cd2af633f62a3329e31f5adee475887cf1')
    if stock_pair and not any(action.startswith('bank:') for action in table.get('buttons', {}).values()):
        table['banks'] = pack
    else:
        for bank in pack:
            if bank in banks or bank['name'] in ('Synth', 'Keys') and any(covers_layout(b, bank) for b in banks):
                continue
            added = _new_bank(banks, bank)
            if old_sounds is not None and bank is memes:
                banks[old_sounds] = added
            else:
                banks.append(added)
    clips = data / 'clips'
    if clips.is_dir() and not (data / '.first-sounds').is_dir():
        # Imported-only libraries never had the stock samples. Older factory libraries may have deliberate deletions.
        had_factory = old_sounds is not None or (clips / 'Pluck C4.wav').exists() or (clips / 'Bass C2.wav').exists()
        (data / ('.add-pack-sounds' if had_factory else '.first-sounds')).mkdir(exist_ok=True)


def save(path, data):
    """Written whole or not at all, so a power cut leaves the old file or the new one. For its owner alone: it may
    hold a token or a password."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.settings-', suffix='.tmp')
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(dumps(data))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def credentials(home):
    """Return the verifier and whether the entire page is locked. Old owner files lock the page."""
    try:
        data = tomllib.loads((home / 'owner.toml').read_text())
        verifier = data['login']
        if not isinstance(verifier, str) or not re.fullmatch(r'pbkdf2\$(?:[0-9a-f]{2})+\$[0-9a-f]{64}', verifier):
            return '', True
        return verifier, data.get('admin_only') is not True
    except FileNotFoundError:
        return None, False
    except (KeyError, ValueError, OSError):
        return '', True


def owner(home):
    return credentials(home)[0]


def set_password(home, password, *, admin_only=False):
    if not password:
        (home / 'owner.toml').unlink(missing_ok=True)
        return
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PASSWORD_ROUNDS).hex()
    verifier = f'pbkdf2${salt.hex()}${digest}'
    save(home / 'owner.toml', {'login': verifier, 'admin_only': admin_only})
    return verifier


def matches(verifier, password):
    try:
        kind, salt, wanted = verifier.split('$')
        tried = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS).hex()
        return kind == 'pbkdf2' and hmac.compare_digest(tried, wanted)
    except (ValueError, TypeError, AttributeError):
        return False


def key(name):
    return name if re.fullmatch(r'[A-Za-z0-9_-]+', name) else text(name)


def text(string):
    return json.dumps(string, ensure_ascii=False).replace('\x7f', '\\u007f')


def value(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, str):
        return text(v)
    if isinstance(v, dict):
        return '{' + ', '.join(f'{key(k)} = {value(x)}' for k, x in v.items() if x is not None) + '}'
    if isinstance(v, (list, tuple)):
        return '[' + ', '.join(value(x) for x in v) + ']'
    return v.isoformat() if isinstance(v, (datetime.date, datetime.time)) else repr(v)


def flat(v):
    """A table short enough for one line: only plain values in it."""
    return isinstance(v, dict) and not any(isinstance(x, (dict, list, tuple)) for x in v.values())


def dumps(data):
    lines = [HEADER]

    def table(path, d):
        records = bool(d) and all(flat(v) for v in d.values())  # (pad after pad: one line each)
        sections = {k: v for k, v in d.items() if not records and v and
                    (isinstance(v, dict) or isinstance(v, list) and all(isinstance(x, dict) for x in v))}
        for k, v in d.items():
            if k not in sections and v is not None:
                lines.append(f'{key(k)} = {value(v)}')
        for k, v in sections.items():
            name = '.'.join(path + [key(k)])
            for item, title in ((v, f'[{name}]'),) if isinstance(v, dict) else ((x, f'[[{name}]]') for x in v):
                lines.extend(('', title))
                table(path + [key(k)], item)

    table([], data)
    return '\n'.join(lines) + '\n'
