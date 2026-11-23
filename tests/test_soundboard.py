"""The Soundboard page: the owner's clips on the pads, on as many banks as they like."""
import io
import shutil
import subprocess
import time
import threading
import unittest
import wave
from unittest.mock import patch

from tests.kit import BLACK, Home, level, note, pads, ship


def pad(board, x, y, now):
    return level(board.frame(now)[10 * y + x])


class Clips(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'soundboard')
        self.clips = self.home.path / 'data/soundboard/clips'
        for name in ('a', 'b', 'c'):
            note(self.clips / f'{name}.wav')
        self.board = self.home.board()
        self.put({'1,8': {'thing': 'a'}, '2,8': {'thing': 'b'}}, {'1,1': {'thing': 'c'}})
        self.heard = self.home.sound.heard

    def put(self, *banks):
        settings = self.board.config
        settings['plugins']['soundboard']['banks'] = [{'name': f'Bank {k}', 'pads': bank} for k, bank in enumerate(banks)]
        settings['plugins']['soundboard']['buttons'] = {'right1': 'volume_up', 'right2': 'volume_down', 'right8': 'stop',
                                                        'bottom1': 'bank:0', 'bottom2': 'bank:1', 'bottom3': 'next_bank'}
        self.board.configure(settings)

    def test_clip_library_groups_survive_custom_bank_layouts(self):
        names = {'Kick': 'Drums', 'Pluck Eb5': 'Synth', 'Chord Cm': 'All chords',
                 'Reverse cymbal': 'Effects', 'Laser': 'Effects', 'Airhorn': 'Memes', 'b': 'My clips'}
        for name in names:
            note(self.clips / f'{name}.wav')
        self.put({'1,1': {'thing': 'Airhorn'}, '2,2': {'thing': 'b'}})
        items = self.board.state()['plugins']['soundboard']['items']
        self.assertEqual({item['id']: item.get('kind') for item in items if item['id'] in names}, names)

    def test_a_pad_plays_its_clip_and_lights_while_it_sounds(self):
        b = self.board
        resting = pad(b, 2, 8, 1.)
        self.assertGreater(resting, 0)
        self.assertEqual(pad(b, 3, 8, 1.), 0)  # (a pad without a clip: dark)
        b.press(2, 8, 127, 2.)
        self.assertEqual([v.clip for v in self.heard], ['b'])
        self.assertGreater(pad(b, 2, 8, 2.1), resting)
        self.heard[0].code = 0  # (it ends)
        self.assertEqual(pad(b, 2, 8, 2.2), resting)
        b.press(4, 4, 127, 3.)  # (an empty pad: nothing)
        self.assertEqual(len(self.heard), 1)

    def test_a_bank_button_pressed_right_after_its_bank_was_removed_goes_on_from_the_last_bank(self):
        b = self.board
        b.press(2, 0, 127, .2)  # (the second bank)
        self.put({'1,8': {'thing': 'a'}})  # (only one is left, and no frame has been drawn since)
        b.press(3, 0, 127, .3)  # (next bank)
        soundboard = b.state()['plugins']['soundboard']
        self.assertFalse(soundboard.get('error'))
        self.assertEqual(soundboard['state']['bank'], 0)

    def test_pressure_switch_temporarily_overrides_a_pads_strike_response(self):
        self.put({'1,8': {'thing': 'a', 'dynamics': 'velocity'}})
        b = self.board
        def strikes():
            for strength in (32, 112):
                b.press(1, 8, strength, 2.)
                b.release(1, 8, 2.1)
            return [voice.gain for voice in self.heard[-2:]]
        soft, loud = strikes()
        self.assertLess(soft, loud)
        for enabled in (False, True):
            settings = b.state()['config']
            settings['plugins']['soundboard'].setdefault('settings', {})['pressure_sensitive'] = enabled
            b.from_page(settings)
            first, second = strikes()
            if enabled:
                self.assertEqual([first, second], [soft, loud])
            else:
                self.assertEqual(first, second)

    def test_holding_a_clip_across_a_bank_switch_stops_it_on_release(self):
        self.put({'1,8': {'thing': 'a', 'mode': 'hold'}}, {})
        self.board.press(1, 8, 127, 1.)
        self.board.press(2, 0, 127, 2.)
        self.board.release(1, 8, 3.)
        self.assertGreater(self.heard[0].release_seconds, 0)

    def test_closing_the_page_releases_held_clips(self):
        self.put({'1,8': {'thing': 'a', 'mode': 'hold'}, '2,8': {'thing': 'b'}})
        self.board.press(1, 8, 127, 1.)
        self.board.press(2, 8, 127, 1.1)
        self.board.set_power(False)
        self.assertIsNotNone(self.heard[0].poll())
        self.assertIsNone(self.heard[1].poll())

    def test_round_buttons_switch_banks_and_stop_everything(self):
        b = self.board
        b.press(1, 8, 127, .1)
        b.press(2, 0, 127, .2)  # the second bank, then a pad before the next frame
        b.press(1, 1, 127, .3)
        self.assertEqual([v.clip for v in self.heard], ['a', 'c'])
        frame = b.frame(.4)
        self.assertEqual(frame[81], BLACK)  # (a is still sounding, but its light belongs to the first bank)
        self.assertGreater(level(frame[2]), level(frame[1]))  # (the open bank's button brighter)
        b.press(3, 0, 127, .5)  # (next bank: round to the first)
        b.press(2, 8, 127, .6)
        self.assertEqual(self.heard[-1].clip, 'b')
        b.press(9, 1, 127, .7)
        self.assertEqual([v.code for v in self.heard], [-15, -15, -15])

    def test_the_first_sound_sets_the_starting_volume_and_buttons_change_it(self):
        b = self.board
        b.press(1, 8, 127, 1.)
        b.press(1, 8, 127, 1.1)
        levels = self.home.sound.levels
        self.assertEqual(len(levels), 1)
        b.press(9, 8, 127, 2.)
        b.press(9, 8, 127, 2.1)
        self.assertEqual(levels[1:], [levels[0] + 1, levels[0] + 2])
        b.press(9, 7, 127, 3.)
        self.assertEqual(levels[-1], levels[0] + 1)

    def test_the_quietest_sound_gives_way_when_too_many_sound_at_once(self):
        self.board.press(1, 8, 127, 1.)
        self.board.press(1, 8, 127, 1.1)
        self.heard[1].level = .01  # (the second has all but faded; the first still rings)
        for k in range(100):
            self.board.press(1, 8, 127, 2. + k / 100)
            if any(v.code for v in self.heard):
                break
        self.assertEqual([i for i, v in enumerate(self.heard) if v.code], [1])

    def test_a_bank_with_nothing_on_it_shows_a_speaker(self):
        self.put({})
        self.assertGreater(sum(level(c) > 0 for c in pads(self.board, 1.)), 8)

    def test_a_pad_set_to_play_while_held_stops_when_let_go(self):
        self.put({'1,8': {'thing': 'a', 'mode': 'hold'}})
        self.board.press(1, 8, 127, 1.)
        self.assertIsNone(self.heard[0].code)
        self.board.release(1, 8, 1.5)
        self.assertGreater(self.heard[0].release_seconds, 0)

    def test_a_looping_pad_starts_over_when_it_ends_until_it_is_pressed_again(self):
        b = self.board
        self.put({'1,8': {'thing': 'a', 'mode': 'loop'}})
        b.press(1, 8, 127, 1.)
        self.heard[0].code = 0  # (the clip ends)
        b.frame(1.5)
        self.assertEqual(len(self.heard), 2)
        b.press(1, 8, 127, 2.)  # (again: stops)
        self.assertEqual(self.heard[1].code, -15)
        b.frame(2.5)
        self.assertEqual(len(self.heard), 2)

    def test_a_pad_shows_in_the_colour_the_owner_gave_it(self):
        self.put({'1,8': {'thing': 'a', 'colour': '#0000ff'}})
        colour = self.board.frame(1.)[81]
        self.assertGreater(colour[2], colour[0] + colour[1])

    def test_switched_off_while_a_clip_sounds_it_goes_quiet_and_comes_back_when_switched_on(self):
        b = self.board
        b.press(1, 8, 127, 1.)
        settings = b.config
        for enabled in (False, True):
            settings['plugins']['soundboard']['enabled'] = enabled
            b.configure(settings)
        self.assertEqual(self.heard[0].code, -15)
        self.assertIsNone(b.state()['plugins']['soundboard']['error'])

    def test_opening_the_soundboard_wakes_the_amplifier_before_any_clip(self):
        self.home.plugin('other', button='top1')
        board = self.home.board()
        board.press(1, 9, 127, 0.)
        woken = self.home.sound.woken
        board.press(3, 9, 127, 1.)
        self.assertEqual(self.home.sound.woken, woken + 1)


class Uploads(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'soundboard')
        (self.home.path / 'data/soundboard/clips').mkdir(parents=True)
        self.board = self.home.board()

    def clips(self):
        return [clip['name'] for clip in self.board.state()['plugins']['soundboard']['items']]

    def test_an_uploaded_clip_can_be_put_on_a_pad_heard_on_the_page_and_deleted(self):
        b = self.board
        wav = note(self.home.path / 'x.wav').read_bytes()
        self.assertEqual(b.request('soundboard', 'upload', {'name': 'My horn!.wav'}, wav), {'id': 'My horn'})
        self.assertEqual(self.clips(), ['My horn'])
        kind, data = b.request('soundboard', 'clip', {'id': 'My horn'}, b'')
        self.assertEqual((kind, data[:4]), ('audio/wav', b'RIFF'))  # (levelled, so not byte for byte)
        settings = b.config
        settings['plugins']['soundboard']['banks'] = [{'name': 'One', 'pads': {'5,5': {'thing': 'My horn'}}}]
        b.configure(settings)
        b.press(5, 5, 127, 1.)
        self.assertEqual([v.clip for v in self.home.sound.heard], ['My horn'])
        self.assertEqual(b.request('soundboard', 'delete', {'id': 'My horn'}, b''), {})
        self.assertEqual(self.clips(), [])
        self.assertEqual(b.state()['config']['plugins']['soundboard']['banks'][0]['pads'], {})

    def test_a_second_tab_cannot_restore_a_pad_after_its_clip_was_deleted(self):
        b = self.board
        wav = note(self.home.path / 'x.wav').read_bytes()
        b.request('soundboard', 'upload', {'name': 'Bell.wav'}, wav)
        settings = b.state()['config']
        settings['plugins']['soundboard']['banks'] = [{'name': 'One', 'pads': {'2,3': {'thing': 'Bell'}}}]
        b.from_page(settings)
        undo = b.state()['config']
        settings['plugins']['soundboard']['banks'][0]['pads'].clear()
        b.from_page(settings)
        self.assertEqual(b.request('soundboard', 'delete', {'id': 'Bell'}, b''), {})
        with self.assertRaisesRegex(ValueError, 'Bell'):
            b.from_page(undo)
        self.assertEqual(b.state()['config']['plugins']['soundboard']['banks'][0]['pads'], {})

    def test_deleting_a_library_clip_uses_its_exact_name(self):
        # Older libraries can contain filenames with punctuation an upload would strip.
        for name in ('Bell!', 'Bell'):
            note(self.home.path / 'data/soundboard/clips' / (name + '.wav'))
        self.assertEqual(self.board.request('soundboard', 'delete', {'id': 'Bell!'}, b''), {})
        self.assertEqual(self.clips(), ['Bell'])

    def test_a_file_that_is_not_a_sound_is_refused_with_a_reason(self):
        answer = self.board.request('soundboard', 'upload', {'name': 'notes.wav'}, b'just text')
        self.assertIn('error', answer)
        self.assertEqual(self.clips(), [])
        self.assertIsNone(self.board.state()['plugins']['soundboard']['error'])

    def test_without_ffmpeg_long_wavs_are_trimmed_and_incomplete_wavs_leave_the_clip_intact(self):
        wav = note(self.home.path / 'long.wav', 21 * 44100).read_bytes()
        with patch('shutil.which', return_value=None):
            upload = lambda data: self.board.request('soundboard', 'upload', {'name': 'Long.wav'}, data)
            self.assertEqual(upload(wav), {'id':'Long'})
            _, saved = self.board.request('soundboard', 'clip', {'id':'Long'}, b'')
            with wave.open(io.BytesIO(saved)) as clip:
                self.assertEqual(clip.getnframes(), 20 * clip.getframerate())
            self.assertIn('error', upload(wav[:100]))
            self.assertEqual(self.board.request('soundboard', 'clip', {'id':'Long'}, b'')[1], saved)

    def test_an_upload_never_replaces_another_clip_unless_asked_to(self):
        first = note(self.home.path / 'a.wav', 4410).read_bytes()
        second = note(self.home.path / 'b.wav', 8820).read_bytes()
        upload = lambda name, data, **more: self.board.request('soundboard', 'upload', {'name': name} | more, data)
        with patch('shutil.which', return_value=None):
            self.assertEqual(upload('Bell.wav', first), {'id': 'Bell'})
            self.assertEqual(upload('Bell!.wav', second), {'id': 'Bell 2'})  # (the same name once the sign is gone)
            self.assertEqual(self.board.request('soundboard', 'clip', {'id': 'Bell'}, b'')[1], first)
            self.assertEqual(upload('Bell.wav', second, replace='1'), {'id': 'Bell'})
            self.assertEqual(self.board.request('soundboard', 'clip', {'id': 'Bell'}, b'')[1], second)

    def test_a_recording_levelled_where_it_was_made_is_taken_as_it_is_without_the_converter(self):
        wav = note(self.home.path / 'mine.wav', 44100).read_bytes()
        called = []
        def converter(command, **options):
            called.append(command[0])
            return subprocess.CompletedProcess(command, 1)
        with patch('shutil.which', return_value='ffmpeg'), patch('subprocess.run', side_effect=converter):
            self.assertEqual(self.board.request('soundboard', 'upload', {'name':'Mine.wav', 'levelled':'1'}, wav), {'id':'Mine'})
            self.assertEqual(called, [])
            self.assertEqual(self.board.request('soundboard', 'clip', {'id':'Mine'}, b'')[1], wav)
            # (anything else that says so still goes through the converter: only the mixer's own format is taken as it is)
            self.assertIn('error', self.board.request('soundboard', 'upload', {'name':'Other.wav', 'levelled':'1'}, b'not a wav'))
            self.assertEqual(called, ['ffmpeg'])

    def test_a_busy_decoder_refuses_another_clip_and_a_timeout_allows_retry(self):
        wav = note(self.home.path / 'original.wav').read_bytes()
        def upload(name):
            return self.board.request('soundboard', 'upload', {'name':name}, wav)
        entered, finish = threading.Event(), threading.Event()
        answer = []
        def stalled(command, *, timeout, **options):
            entered.set()
            finish.wait(3)
            raise subprocess.TimeoutExpired(command, timeout)
        with patch('shutil.which', return_value='ffmpeg'), patch('subprocess.run', side_effect=stalled):
            worker = threading.Thread(target=lambda: answer.append(upload('First')))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertIn('Another sound', upload('Second')['error'])
            finally:
                finish.set()
                worker.join(3)
        self.assertIn('too long', answer[0]['error'])
        with patch('shutil.which', return_value=None):
            self.assertEqual(upload('First'), {'id':'First'})

    @unittest.skipUnless(shutil.which('ffmpeg'), 'needs ffmpeg')
    def test_other_formats_are_converted_to_what_the_mixer_plays(self):
        from padcade.drivers.sound import pcm_samples
        source = note(self.home.path / 'x.wav', 44100)
        for extension in ('mp3', 'ogg', 'flac', 'm4a', 'aiff'):  # (m4a: what a phone's voice memo is)
            with self.subTest(extension=extension):
                encoded = source.with_suffix('.' + extension)
                subprocess.run(['ffmpeg', '-loglevel', 'error', '-i', str(source), '-ar', '22050', '-ac', '1', str(encoded)], check=True)
                name = extension + '.' + extension
                self.assertEqual(self.board.request('soundboard', 'upload', {'name':name}, encoded.read_bytes()), {'id':extension})
                self.assertIsNotNone(pcm_samples(self.home.path / f'data/soundboard/clips/{extension}.wav'))

    @unittest.skipUnless(shutil.which('ffmpeg'), 'needs ffmpeg')
    def test_an_upload_that_names_another_file_does_not_bring_that_file_in(self):
        other = note(self.home.path / 'elsewhere.wav', 44100)
        for playlist in (f"ffconcat version 1.0\nfile '{other}'\n", f'#EXTM3U\n#EXTINF:1,\n{other}\n'):
            with self.subTest(playlist=playlist[:10]):
                self.assertIn('error', self.board.request('soundboard', 'upload', {'name':'list.wav'}, playlist.encode()))
                self.assertEqual(self.clips(), [])


class StringUpgrade(unittest.TestCase):
    def test_old_factory_string_gets_click_free_edges_and_an_owners_sample_survives(self):
        from array import array
        from padcade import config, synth
        home = Home(self)
        ship(home, 'soundboard')
        clips = home.path / 'data/soundboard/clips'
        clips.mkdir(parents=True)
        stock, custom = 'Bass Eb2', 'Pluck G5'
        source = synth.RECIPES[stock]()
        peak = max(map(abs, source))
        old = synth.stereo(array('h', (int(s / peak * 26000) for s in source)))
        with wave.open(str(clips / (stock + '.wav')), 'wb') as wav:
            wav.setparams((2, 2, synth.RATE, 0, 'NONE', ''))
            wav.writeframes(old)
        own = note(clips / (custom + '.wav')).read_bytes()
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {'factory_banks': 16}}})
        board = home.board()
        self.addCleanup(board.discard, board.running['soundboard'])
        with wave.open(str(clips / (stock + '.wav')), 'rb') as wav:
            audio = wav.readframes(wav.getnframes())
        self.assertEqual(audio[:4] + audio[-4:], bytes(8))
        self.assertEqual(audio, next(synth.factory_clips(home.shipped / 'soundboard/sounds', [stock]))[2])
        self.assertEqual((clips / (custom + '.wav')).read_bytes(), own)
        self.assertFalse((clips / 'Bass G3.wav').exists())


class BankUpgrades(unittest.TestCase):
    def setUp(self):
        # Upgrades exercise file selection and placement. The fresh-install test renders the real instruments.
        def tiny_factory(recordings, names):
            return ((name, 44100, bytes(4)) for name in names)

        self.enterContext(patch('padcade.synth.factory_clips', tiny_factory))
        self.enterContext(patch('padcade.synth.background_factory_clips', tiny_factory))

    def test_reorganised_drums_keep_an_edited_kit_and_offer_the_original_jam(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        drums = {'name': 'My drums', 'pads': {'1,1': {'thing': 'Kick', 'name': 'Main kick'},
                                             '2,1': {'thing': 'Snare'}, '3,1': {'thing': 'Closed hat'},
                                             '4,1': {'thing': 'Open hat'}, '1,2': {'action': 'play'}}}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {
            'enabled': False, 'factory_banks': 12, 'banks': [drums]}}})
        banks = home.board().state()['config']['plugins']['soundboard']['banks']
        self.assertEqual(banks[0], drums)
        jam = next(bank for bank in banks if bank['name'] == 'Jam classics')
        self.assertEqual(len(jam['pads']), 28)
        self.assertIn('Jam clap', {pad['thing'] for pad in jam['pads'].values()})

    def test_chord_banks_follow_synth_in_shift_without_redirecting_saved_bank_buttons(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        data = home.path / 'data/soundboard/clips'
        note(data / 'Kick.wav')
        synth = {'name': 'Synth', 'pads': {'1,1': {'thing': 'Studio bass C2', 'name': 'My bass'}}}
        drums = {'name': 'My drums', 'pads': {'1,1': {'thing': 'Kick'}}}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {'factory_banks': 14,
            'banks': [synth, drums], 'buttons': {'left1': 'banks', 'bottom1': 'bank:1'}}}})
        b = home.board()
        self.addCleanup(b.discard, b.running['soundboard'])
        b.open('soundboard')
        b.press(1, 0, 100, 0.)
        b.release(1, 0, .1)
        self.assertEqual(b.state()['plugins']['soundboard']['state']['bank'], 1)
        banks = b.config['plugins']['soundboard']['banks']
        self.assertEqual(banks[:2], [synth, drums])
        b.act('banks', .2)
        state = b.state()['plugins']['soundboard']['state']
        names = [banks[i]['name'] for i in state['bank_order']]
        start = names.index('Synth (factory)')
        self.assertEqual(names[start:start+3], ['Synth (factory)', 'All chords', 'Synth high'])
        for pos, bank in enumerate(names):
            self.assertEqual(state['labels'][f'{2*(pos%3)+1},{8-pos//3}'], bank)

    def test_removing_factory_chords_keeps_custom_banks_and_redirects_bank_shortcuts(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        # The shipped v15 layout. The owner also has an edited copy.
        triads = ('C3', 'Dm', 'Em', 'F', 'G', 'Am', 'Bdim', 'C')
        sevenths = ('C3 major seventh', 'D3 minor seventh', 'E3 minor seventh', 'F3 major seventh',
                    'G7', 'Am7', 'B3 half diminished', 'Cmaj7')
        stock = {}
        for y, (names, labels, normal, root) in enumerate((
                (triads, ('C','Dm','Em','F','G','Am','Bdim','C ↑'), '#da8e50', '#ffdfb1'),
                (sevenths, ('Cmaj7','Dm7','Em7','Fmaj7','G7','Am7','Bm7♭5','Cmaj7 ↑'), '#c3659c', '#ffbddb')), 1):
            for x, (clip, label) in enumerate(zip(names, labels), 1):
                stock[f'{x},{y}'] = {'name': label, 'thing': 'Chord ' + clip, 'mode': 'hold',
                                     'colour': root if x in (1, 8) else normal}
        chords = {'name': 'Chords', 'family': 'Synth', 'pads': stock}
        edited = dict(chords, name='Chords (factory)', pads=stock | {'1,1': {'thing': 'My chord'}})
        all_chords = {'name': 'All chords', 'family': 'Synth', 'pads': {'1,1': {'thing': 'My major'}}}
        controls = {'name': 'Controls', 'pads': {'1,1': {'action': 'bank:2'},
                                                '2,1': {'sub': {'1,1': {'action': 'bank:3'}}}}}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {
            'enabled': False, 'factory_banks': 15, 'banks': [controls, chords, all_chords, edited],
            'buttons': {'left1': 'bank:1', 'left2': 'bank:0'}}}})
        table = home.board().state()['config']['plugins']['soundboard']
        self.assertEqual(table['banks'][1:], [all_chords, edited])
        self.assertEqual(table['buttons']['left1'], 'bank:1')  # The former shortcut now opens All chords.
        self.assertEqual(table['buttons']['left2'], 'bank:0')
        pads = table['banks'][0]['pads']
        self.assertEqual(pads['1,1']['action'], 'bank:1')
        self.assertEqual(pads['2,1']['sub']['1,1']['action'], 'bank:2')

    def test_new_octave_is_available_on_upgrade_without_reinstalling_removed_chords(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        clips = home.path / 'data/soundboard/clips'
        note(clips / 'My chord.wav')
        mine = {'name': 'All chords', 'family': 'Synth', 'pads': {'1,1': {'thing': 'My chord'}}}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {'factory_banks': 15, 'banks': [mine]}}})
        board = home.board()
        self.addCleanup(board.discard, board.running['soundboard'])
        self.assertEqual(board.config['plugins']['soundboard']['banks'], [mine])
        stop = time.monotonic() + 3
        while time.monotonic() < stop and not (clips / 'Chord Db4 major.wav').exists():
            time.sleep(.02)
        items = {item['id'] for item in board.state()['plugins']['soundboard']['items']}
        self.assertIn('Chord Db4 major', items)
        self.assertNotIn('Chord C3', items)

    def test_synth_upgrade_keeps_edited_pads_and_offers_the_new_instrument(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        clips = home.path / 'data/soundboard/clips'
        own = note(clips / 'Jam bass D2.wav').read_bytes()
        chosen = {'name': 'Synth', 'pads': {'1,1': {'thing': 'Studio bass C2', 'name': 'My C'},
                                             '2,1': {'action': 'record'}}}
        samples = {'name': 'My samples', 'pads': {}}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {
            'factory_banks': 13, 'banks': [chosen, samples]}}})
        board = home.board()
        self.addCleanup(board.discard, board.running['soundboard'])
        banks = board.state()['config']['plugins']['soundboard']['banks']
        self.assertEqual(banks[:2], [chosen, samples])
        grid = next(b for b in banks if b['name'] == 'Synth (factory)')
        self.assertIn('Jam pluck D4', {p['thing'] for p in grid['pads'].values()})
        deadline = time.monotonic() + 3
        while not (clips / 'Jam pluck D4.wav').is_file() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertIn('Jam pluck D4', {item['id'] for item in board.state()['plugins']['soundboard']['items']})
        self.assertEqual(board.request('soundboard', 'clip', {'id': 'Jam bass D2'}, b'')[1], own)

    def test_upgrading_the_electronic_kit_keeps_imports_edits_and_replaced_recordings(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        clips = home.path / 'data/soundboard/clips'
        custom_bass = note(clips / 'Round bass D2.wav').read_bytes()
        custom_drum = note(clips / 'Electric clap.wav').read_bytes()
        with wave.open(str(clips / 'Round bass C2.wav'), 'wb') as wav:
            wav.setparams((2, 2, 44100, 0, 'NONE', ''))
            wav.writeframes(bytes(4))  # The factory renderer used by these migration tests.
        legacy = (clips / 'Round bass C2.wav').read_bytes()
        bass = {'1,1': {'thing':'Round bass C2', 'name':'Low C'}, '2,1': {'thing':'Round bass D2'},
                '3,1': {'action':'record'}, '4,1': {'thing':'Round bass F2'}, '1,8': {'thing':'Round bass E2'}}
        drums = {f'{i},1': {'thing': n} for i, n in enumerate(('Kick', 'Snare', 'Closed hat', 'Open hat'), 1)}
        drums.update({'1,5': {'action':'play'}, '1,8': {'thing':'Sub kick'}})
        imported = {'name':'Imported', 'pack_clips':['Round bass C2'], 'pads':bass}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {'factory_banks':9, 'banks': [
            imported, {'name':'Synth', 'pads':bass}, {'name':'My kit', 'pads':drums}]}}})
        board = home.board()
        self.addCleanup(lambda: board.configure({'plugins': {'soundboard': {'enabled': False}}}))
        kept, instrument, kit = board.state()['config']['plugins']['soundboard']['banks'][:3]
        self.assertEqual(kept, imported)
        self.assertEqual(instrument['pads'], bass | {'1,1': {'thing':'Studio bass C2', 'name':'Low C'}})
        self.assertEqual({pos:kit['pads'][pos] for pos in drums}, drums)
        self.assertNotIn('5,5', kit['pads'])  # Sub kick was already placed elsewhere.
        self.assertEqual(kit['pads']['2,5']['thing'], 'Snap snare')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            items = {i['id']:i['kind'] for i in board.state()['plugins']['soundboard']['items']}
            if {'Plucked bass C2', 'Snap snare'} <= items.keys():
                break
            time.sleep(.03)
        self.assertEqual(items['Plucked bass C2'], 'Synth')
        self.assertEqual(items['Round bass C2'], 'Synth')
        self.assertEqual(items['Snap snare'], 'Drums')
        self.assertNotIn('Round bass F2', items)
        for name, data in [('Round bass C2', legacy), ('Round bass D2', custom_bass), ('Electric clap', custom_drum)]:
            self.assertEqual(board.request('soundboard', 'clip', {'id':name}, b'')[1], data)

    def test_tuned_kits_upgrade_stock_pads_without_restoring_a_deleted_hit(self):
        for version, old in ((10, 'Plucked bass'), (11, 'Finger bass')):
            with self.subTest(version=version):
                self.check_kit_upgrade(version, old)

    def check_kit_upgrade(self, version, old):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        clips = home.path / 'data/soundboard/clips'
        custom = note(clips / f'{old} D2.wav').read_bytes()
        with wave.open(str(clips / f'{old} C2.wav'), 'wb') as wav:
            wav.setparams((2, 2, 44100, 0, 'NONE', ''))
            wav.writeframes(bytes(4))
        banks = [{'name': 'Synth', 'pads': {'1,1': {'thing': f'{old} C2', 'name': 'My C'},
                                            '2,1': {'thing': f'{old} D2'}}},
                 {'name': 'Drums', 'pads': {'1,1': {'thing': 'Kick'}, '1,7': {'action': 'play'}}}]
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {'factory_banks': version, 'banks': banks}}})
        board = home.board()
        self.addCleanup(lambda: board.configure({'plugins': {'soundboard': {'enabled': False}}}))
        instrument, kit = board.state()['config']['plugins']['soundboard']['banks'][:2]
        self.assertEqual(instrument['pads']['1,1'], {'thing': 'Studio bass C2', 'name': 'My C'})
        self.assertEqual(instrument['pads']['2,1'], banks[0]['pads']['2,1'])
        self.assertEqual(kit['pads']['1,7'], {'action': 'play'})
        if version == 10:
            self.assertEqual(kit['pads']['2,7']['thing'], 'Electric kick C1')
        else:
            self.assertNotIn('2,7', kit['pads'])
        self.assertNotIn('1,5', kit['pads'])
        deadline = time.monotonic() + 15
        while any(clips.parent.glob('.*-sounds')) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue((clips / 'Studio bass C2.wav').is_file())
        self.assertEqual((clips / 'Classic kick.wav').is_file(), version == 10)
        self.assertFalse((clips / 'Punch kick.wav').exists())
        self.assertEqual((clips / f'{old} D2.wav').read_bytes(), custom)

    def test_pedal_upgrade_gates_existing_soft_keys_and_preserves_custom_play_modes(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        pads = {f'{x},3': {'thing': f'Soft keys {pitch}4'} for x, pitch in enumerate('CDEF', 1)}
        pads['2,3'].update(mode='once')
        pads['3,3'].update(mode='loop')
        custom = {'name': 'My imported keys', 'pack_clips': ['Soft keys C4'], 'pads': pads}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {
            'factory_banks': 8, 'enabled': False, 'banks': [custom, {'name': 'Renamed synth', 'pads': pads}]}}})
        imported, upgraded = home.board().state()['config']['plugins']['soundboard']['banks'][:2]
        self.assertEqual(imported, custom)
        self.assertEqual([upgraded['pads'][f'{x},3']['mode'] for x in range(1, 5)],
                         ['hold', 'once', 'loop', 'hold'])
        self.assertEqual(set(upgraded['pads']), set(pads))

    def test_stock_chords_follow_the_melody_row_and_preserve_the_original_chords_above_it(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        chords = ('Cm', 'Eb', 'Ab', 'Bb', 'C', 'G', 'Am', 'F')
        pads = {f'{x},5': {'thing': 'Chord ' + name, 'mode': 'hold',
                            'colour': '#ff7abb' if x <= 4 else '#ffba80'}
                for x, name in enumerate(chords, 1)}
        edited = dict(pads, **{'3,6': {'action': 'play'}})
        copy = {'name': 'My saved chord preset', 'pads': pads}
        for original in (edited, pads):
            config.save(home.path / 'config.toml', {'plugins': {'soundboard': {
                'factory_banks': 7, 'enabled': False, 'banks': [copy, {'name': 'Synth', 'pads': original}]}}})
            kept, bank = home.board().state()['config']['plugins']['soundboard']['banks'][:2]
            self.assertEqual(kept, copy)
            if original is edited:
                self.assertEqual(bank['pads'], edited)
            else:
                for row, names in ((5, ['C', 'Dm', 'Em', 'F', 'G', 'Am', 'Bdim', 'High C']),
                                   (6, ['Cm', 'E♭', 'A♭', 'B♭'])):
                    self.assertEqual([bank['pads'][f'{x},{row}']['name'] for x in range(1, len(names) + 1)], names)
                self.assertLessEqual({'Chord ' + name for name in chords},
                                     {pad['thing'] for pad in bank['pads'].values()})

    def test_drum_upgrade_expands_the_renamed_kit_without_disturbing_user_pads_or_audio(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        clips = home.path / 'data/soundboard/clips'
        original = note(clips / 'Cowbell.wav').read_bytes()
        custom = {'name': 'Drums', 'pack_clips': ['Glass'], 'pads': {'1,1': {'thing': 'Glass'}}}
        pads = {f'{i},1': {'thing': name} for i, name in enumerate(('Kick','Snare','Closed hat','Open hat'), 1)}
        pads.update({'5,1': {'action': 'play'}, '8,8': {'thing': 'Splash'}})
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {
            'factory_banks': 7, 'banks': [custom, {'name': 'My kit', 'pads': pads}]}}})
        board = home.board()
        self.addCleanup(lambda: board.configure({'plugins': {'soundboard': {'enabled': False}}}))
        imported, kit = board.state()['config']['plugins']['soundboard']['banks'][:2]
        self.assertEqual(imported, custom)
        expanded = kit['pads']
        self.assertEqual({pos: expanded[pos] for pos in pads}, pads)
        self.assertNotIn('7,1', expanded)  # Splash was already moved elsewhere.
        self.assertNotIn('1,2', expanded)  # The owner removed the old Clap pad.
        self.assertEqual(expanded['6,1']['thing'], 'Crash')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            library = {item['id']: item['kind'] for item in board.state()['plugins']['soundboard']['items']}
            if library.get('Open triangle') == 'Drums':
                break
            time.sleep(.05)
        board.act('bank:1', 0.)
        board.press(8, 4, 90, 1.)
        self.assertEqual(home.sound.heard[-1].clip, 'Open triangle')
        self.assertEqual(board.request('soundboard', 'clip', {'id':'Cowbell'}, b'')[1], original)
        self.assertNotIn('Clap', library)

    def test_chord_upgrade_fills_free_pads_and_keeps_custom_and_moved_chords(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        clips = home.path / 'data/soundboard/clips'
        original = note(clips / 'Chord G.wav').read_bytes()
        pads = {f'{x},5': {'thing': 'Chord ' + name, 'mode': 'hold'}
                for x, name in enumerate(('Cm', 'Eb', 'Ab', 'Bb'), 1)}
        pads.update({'5,5': {'action': 'play'}, '8,8': {'thing': 'Chord Am'}})
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {
            'factory_banks': 6, 'banks': [{'name': 'My synth', 'pads': pads}]}}})
        board = home.board()
        self.addCleanup(lambda: board.configure({'plugins': {'soundboard': {'enabled': False}}}))
        bank, = board.state()['config']['plugins']['soundboard']['banks'][:1]
        self.assertEqual(bank['name'], 'My synth')
        self.assertEqual({pos: bank['pads'][pos] for pos in pads}, pads)
        self.assertNotIn('7,5', bank['pads'])
        self.assertEqual([bank['pads'][pos]['thing'] for pos in ('6,5', '8,5')], ['Chord G', 'Chord F'])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            items = {item['id'] for item in board.state()['plugins']['soundboard']['items']}
            if {'Chord C', 'Chord Am', 'Chord F'} <= items:
                break
            time.sleep(.05)
        board.press(8, 5, 110, 1.)
        self.assertEqual(home.sound.heard[-1].clip, 'Chord F')
        self.assertEqual(board.request('soundboard', 'clip', {'id': 'Chord G'}, b'')[1], original)
        self.assertNotIn('Kick', items)

    def test_keys_upgrade_appends_playable_octaves_without_replacing_the_owners_keys_bank(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        clips = home.path / 'data/soundboard/clips'
        original = note(clips / 'Bright pluck C2.wav').read_bytes()
        custom = {'name': 'Keys', 'pads': {'1,1': {'thing': 'Bright pluck C2'}}}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {'factory_banks': 3, 'banks': [custom]}}})
        board = home.board()
        self.addCleanup(lambda: board.configure({'plugins': {'soundboard': {'enabled': False}}}))
        existing, keys = board.state()['config']['plugins']['soundboard']['banks'][:2]
        self.assertEqual(existing, custom)
        self.assertEqual(keys['name'], 'Keys (factory)')
        board.act('bank:1', 0.)
        high = 'Piano B5'
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if high in {c['id'] for c in board.state()['plugins']['soundboard']['items']}:
                break
            time.sleep(.05)
        board.press(1, 1, 127, .2)
        board.press(7, 7, 127, .4)
        self.assertEqual([v.clip for v in home.sound.heard], ['Piano C2', high])
        self.assertEqual(board.request('soundboard', 'clip', {'id':'Bright pluck C2'}, b'')[1], original)
        self.assertNotIn('Kick', {c['id'] for c in board.state()['plugins']['soundboard']['items']})

    def test_duplicate_keys_upgrade_in_place_but_custom_layouts_keep_every_pad(self):
        from padcade import config
        old = {}
        for row in range(1, 9):
            for column, note_name in enumerate(('C', 'Eb', 'F', 'G', 'Bb'), 1):
                pitch = f'{note_name}{2 + (row - 1) // 2}'
                old[f'{column},{row}'] = {'name': pitch, 'thing': 'Bright pluck ' + pitch,
                                          'colour': '#5fe07a' if column == 1 else '#3fd0ff'}
        edited = old.copy()
        edited['8,2'] = dict(edited.pop('3,1'), name='My moved note')
        for bank_name, original in [('My piano', old), ('Keys', edited)]:
            with self.subTest(bank_name=bank_name):
                home = Home(self)
                ship(home, 'soundboard')
                sample = note(home.path / 'data/soundboard/clips/Bright pluck Eb4.wav').read_bytes()
                bank = {'name': bank_name, 'pads': original}
                config.save(home.path / 'config.toml', {'plugins': {'soundboard': {'factory_banks': 4, 'banks': [bank]}}})
                board = home.board()
                self.addCleanup(lambda b=board: b.configure({'plugins': {'soundboard': {'enabled': False}}}))
                banks = [b for b in board.state()['config']['plugins']['soundboard']['banks'] if b['name'] not in ('Jam classics', 'All chords', 'Synth high')]
                if original is edited:
                    self.assertEqual(banks[:-1], [bank])
                    self.assertEqual(banks[-1]['name'], 'Keys (factory)')
                else:
                    self.assertEqual([b['name'] for b in banks], [bank_name])
                keys = banks[-1]['pads']
                self.assertEqual(len(keys), 48)
                self.assertEqual(len({p['thing'] for p in keys.values()}), 48)
                self.assertEqual([keys[pos]['name'] for pos in ('2,1','3,1','4,2','5,2','7,7')],
                                 ['D2', 'E2', 'F♯2', 'G♯2', 'B5'])
                self.assertNotIn('3,2', keys)
                self.assertEqual(keys['2,6']['thing'], 'Piano Eb4')
                self.assertEqual(keys['2,6']['mode'], 'hold')
                self.assertEqual(board.request('soundboard', 'clip', {'id':'Bright pluck Eb4'}, b'')[1], sample)

    def test_interrupted_keys_install_finishes_old_plucks_that_an_edited_bank_still_uses(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        data = home.path / 'data/soundboard'
        (data / 'clips').mkdir(parents=True)
        (data / '.add-chromatic-sounds').mkdir()
        bank = {'name': 'My keyboard', 'pads': {'6,6': {'thing':'Bright pluck Ab5'}}}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {'factory_banks':5,'banks':[bank]}}})
        board = home.board()
        self.addCleanup(lambda: board.configure({'plugins': {'soundboard': {'enabled': False}}}))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            items = {c['id'] for c in board.state()['plugins']['soundboard']['items']}
            if 'Bright pluck Ab5' in items:
                break
            time.sleep(.05)
        board.press(6, 6, 127, 1.)
        self.assertEqual([v.clip for v in home.sound.heard], ['Bright pluck Ab5'])

    def test_major_synth_upgrades_old_stock_in_place_and_preserves_custom_scales(self):
        from padcade import config
        rows = [('Round bass', 2, '#a070ff'), ('Bright pluck', 4, '#3fd0ff'),
                ('Soft keys', 4, '#5fe07a'), ('Warm pad', 4, '#6498ff')]
        for version in (2, 3, 4, 5):
            with self.subTest(version=version):
                old = {}
                for y, (voice, octave, colour) in enumerate(rows[:3] if version == 2 else rows, 1):
                    for x, n in enumerate(('C','Eb','F','G','Bb','C'), 1):
                        pad = {'thing': f'{voice} {n}{octave + (x == 6)}', 'colour': colour}
                        if y == 4:
                            pad['mode'] = 'hold'
                        old[f'{x},{y}'] = pad
                if version >= 3:
                    for x, chord in enumerate(('Cm','Eb','Ab','Bb'), 1):
                        old[f'{x},5'] = {'thing': 'Chord ' + chord, 'colour': '#ff7abb', 'mode': 'hold'}
                renamed = {'name':'My original instrument', 'pads':old}
                custom = {'name':'Synth', 'pads':dict(old, **{'2,1':{'thing':'My voice'}})}
                home = Home(self)
                ship(home, 'soundboard')
                config.save(home.path / 'config.toml', {'plugins': {'soundboard':
                            {'factory_banks': version, 'enabled': False, 'banks':[custom, renamed]}}})
                banks = home.board().state()['config']['plugins']['soundboard']['banks']
                self.assertEqual(banks[0], custom)
                self.assertEqual([b['name'] for b in banks], ['Synth', 'My original instrument', 'Keys', 'Jam classics', 'All chords', 'Synth high'])
                for row, voice, octave in ((1, 'Studio bass', 2), (3, 'Jam bass', 2), (5, 'Jam pluck', 4)):
                    self.assertEqual([banks[1]['pads'][f'{x},{row}']['thing'] for x in range(1,9)],
                                     [f'{voice} {n}{octave}' for n in 'CDEFGAB'] + [f'{voice} C{octave+1}'])
                self.assertEqual(banks[1]['pads']['1,2']['thing'], 'Studio bass Db2')

    def test_an_upgrade_reuses_an_existing_playable_major_scale_even_under_a_different_name(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        path = home.path / 'config.toml'
        config.save(path, {'plugins': {'soundboard': {'enabled':False}}})
        # Start from a configured instrument, as if the owner had already built their own C major layout.
        banks = home.board().state()['config']['plugins']['soundboard']['banks']
        pads = next(b['pads'] for b in banks if b['name'] == 'Synth')
        pads['1,3']['name'] = 'My middle C'
        own = {'name':'My favourite scale', 'pads':pads}
        for version in (1, 5):
            config.save(path, {'plugins': {'soundboard': {'enabled':False, 'factory_banks':version, 'banks':[own]}}})
            banks = home.board().state()['config']['plugins']['soundboard']['banks']
            self.assertEqual(banks[0], own)
            self.assertNotIn('Synth', [bank['name'] for bank in banks])

    def test_bank_expansion_fills_spare_pads_and_preserves_custom_and_deleted_sounds(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        clips = home.path / 'data/soundboard/clips'
        sample = note(clips / 'My hit.wav').read_bytes()
        chosen = {'name': 'Drums', 'pads': {'1,1': {'thing': 'My hit'}}}
        settings = {'factory_banks': 2, 'banks': [
            chosen, {'name': 'Drums (factory)', 'pads': {'1,1': {'thing': 'Kick'}, '1,3': {'action': 'bank:0'}}},
            {'name': 'Synth', 'pads': {}}, {'name': 'Effects', 'pads': {}}]}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': settings}})
        board = home.board()
        self.addCleanup(lambda: board.configure({'plugins': {'soundboard': {'enabled': False}}}))
        banks = board.state()['config']['plugins']['soundboard']['banks']
        self.assertEqual(chosen, banks[0])
        self.assertEqual(banks[1]['pads']['1,3'], {'action': 'bank:0'})
        self.assertEqual(banks[1]['pads']['2,3']['thing'], 'Rim click')
        self.assertNotIn('2,1', banks[1]['pads'])  # The owner cleared this old pad.
        self.assertEqual(banks[2]['pads'], {})  # Keep the owner's cleared instrument.
        added = next(b for b in banks if b['name'] == 'Synth (factory)')
        self.assertEqual(added['pads']['1,6']['thing'], 'Jam pluck Db4')
        self.assertEqual(banks[3]['pads']['4,3']['thing'], 'Echo hit')
        # An upgrade makes even the unassigned new sounds available in the picker.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            items = {item['id'] for item in board.state()['plugins']['soundboard']['items']}
            if {'Deep kick', 'Warm pad C5', 'Chord Bb', 'Echo hit'} <= items:
                break
            time.sleep(.05)
        self.assertTrue({'Deep kick', 'Warm pad C5', 'Chord Bb', 'Echo hit'} <= items)
        self.assertNotIn('Snare', items)
        self.assertEqual(board.request('soundboard', 'clip', {'id': 'My hit'}, b'')[1], sample)

    def test_upgrade_adds_playable_banks_without_moving_custom_pads_or_replacing_clips(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        clips = home.path / 'data/soundboard/clips'
        own = note(clips / 'Soft keys C4.wav').read_bytes()
        note(clips / 'Bass C2.wav')  # An older factory library with some sounds deliberately removed.
        chosen = {'name': 'My samples', 'pads': {'7,5': {'thing': 'Soft keys C4'}, '3,3': {'action': 'bank:1'}}}
        jam = {'name': 'Jam', 'pads': {'2,2': {'thing': 'Soft keys C4'}}}
        settings = {'banks': [chosen, jam], 'buttons': {'left1': 'bank:1'}}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': settings}})

        board = home.board()
        banks = board.config['plugins']['soundboard']['banks']
        self.assertEqual(banks[:2], [chosen, jam])
        self.assertEqual([bank['name'] for bank in banks[2:]], ['Drums', 'Synth', 'Effects', 'Memes', 'Keys', 'Jam classics', 'All chords', 'Synth high'])
        board.press(0, 8, 127, 0.)
        board.press(2, 2, 127, .1)
        self.assertEqual([v.clip for v in home.sound.heard], ['Soft keys C4'])
        self.assertEqual(board.request('soundboard', 'clip', {'id': 'Soft keys C4'}, b'')[1], own)

        # The pack seeds new sounds once; a clip the owner removed stays gone on the next start.
        def items(b):
            return {item['id'] for item in b.state()['plugins']['soundboard']['items']}
        expected = {pad['thing'] for bank in banks[2:] if bank['name'] != 'Memes' for pad in bank['pads'].values()} - {'Kick', 'Snare', 'Clap', 'Laser'}
        deadline = time.monotonic() + 20
        while not expected <= items(board) and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertFalse(expected - items(board))
        self.assertIn('Metallic hit', items(board))
        self.assertNotIn('Airhorn', items(board))
        self.assertEqual(board.request('soundboard', 'delete', {'id':'Metallic hit'}, b''), {})
        # Shut down its plugin like a real restart before opening the same home again.
        config_before_stop = board.state()['config']
        board.configure(dict(config_before_stop, plugins={'soundboard': dict(config_before_stop['plugins']['soundboard'], enabled=False)}))
        config.save(home.path / 'config.toml', config_before_stop)
        again = home.board()
        self.assertEqual(again.config['plugins']['soundboard']['banks'], config_before_stop['plugins']['soundboard']['banks'])
        self.assertNotIn('Metallic hit', items(again))


class FirstRun(unittest.TestCase):
    def test_open_bank_is_playable_before_other_banks_finish_installing(self):
        home = Home(self)
        ship(home, 'soundboard')
        other, resume = threading.Event(), threading.Event()

        def installing(recordings, names):
            from padcade.synth import factory_clips
            for name in names:
                if name == 'Laser':
                    other.set()
                    resume.wait(3)
                yield from factory_clips(recordings, [name])

        with patch('padcade.synth.background_factory_clips', installing):
            board = home.board()
            try:
                self.assertTrue(other.wait(3))
                drums = board.state()['config']['plugins']['soundboard']['banks'][0]['pads']
                position = next(pos for pos, pad in drums.items() if pad['thing'] == 'Low bongo')
                x, y = map(int, position.split(','))
                board.press(x, y, 127, 1.)
                self.assertEqual([voice.clip for voice in home.sound.heard], ['Low bongo'])
            finally:
                resume.set()
                board.configure({'plugins': {'soundboard': {'enabled': False}}})

    def test_factory_clips_are_listed_only_when_the_whole_recording_can_be_played(self):
        home = Home(self)
        ship(home, 'soundboard')
        writing, resume, workers = threading.Event(), threading.Event(), []
        write = wave.Wave_write.writeframes

        def slow_write(wav, samples):
            if not workers:
                workers.append(threading.current_thread())
            writing.set()
            resume.wait(3)
            write(wav, samples)

        with patch('padcade.synth.background_factory_clips', return_value=[('First', 44100, bytes(4))]), patch.object(wave.Wave_write, 'writeframes', slow_write):
            b = home.board()
            try:
                self.assertTrue(writing.wait(3))
                self.assertNotIn('First', [clip['id'] for clip in b.state()['plugins']['soundboard']['items']])
            finally:
                resume.set()
                workers[0].join(3)

    def test_disabling_during_first_run_stops_publication_and_next_start_fills_only_missing_clips(self):
        home = Home(self)
        ship(home, 'soundboard')
        started, resume = threading.Event(), threading.Event()
        workers = []

        def factory(recordings, names):
            workers.append(threading.current_thread())
            started.set()
            resume.wait(3)
            return [('Keep', 44100, bytes(4)), ('Missing', 44100, bytes(4))]

        with patch('padcade.synth.background_factory_clips', side_effect=factory):
            b = home.board()
            try:
                self.assertTrue(started.wait(3))
                source = note(home.path / 'upload.wav').read_bytes()
                self.assertEqual(b.request('soundboard', 'upload', {'name': 'Keep.wav'}, source), {'id': 'Keep'})
                uploaded = b.request('soundboard', 'clip', {'id': 'Keep'}, b'')
                settings = b.state()['config']
                settings['plugins']['soundboard']['enabled'] = False
                b.configure(settings)
            finally:
                resume.set()
                workers[0].join(3)
            self.assertFalse((home.path / 'data/soundboard/clips/Missing.wav').exists())
            started.clear()
            settings['plugins']['soundboard']['enabled'] = True
            b.configure(settings)
            self.assertTrue(started.wait(3))
            workers[-1].join(3)
            self.assertIn('Missing', [clip['id'] for clip in b.state()['plugins']['soundboard']['items']])
            self.assertEqual(b.request('soundboard', 'clip', {'id': 'Keep'}, b''), uploaded)

    def test_a_new_board_has_sounds_on_its_pads_that_the_mixer_plays_directly(self):
        from padcade.drivers.sound import pcm_samples
        home = Home(self)
        ship(home, 'soundboard')
        board = home.board()
        self.addCleanup(board.discard, board.running['soundboard'])
        placed = {to['thing'] for bank in board.config['plugins']['soundboard']['banks'] for to in bank['pads'].values()}
        deadline = time.monotonic() + 90
        while not placed <= {c['id'] for c in board.state()['plugins']['soundboard']['items']} and time.monotonic() < deadline:
            time.sleep(.15)
        self.assertEqual([bank['name'] for bank in board.config['plugins']['soundboard']['banks']],
                         ['Drums', 'Synth', 'Effects', 'Memes', 'Keys', 'Jam classics', 'All chords', 'Synth high'])
        for name in placed:
            self.assertTrue(pcm_samples(home.path / f'data/soundboard/clips/{name}.wav'), name)
        board.press(1, 1, 127, 0.)
        self.assertEqual(len(home.sound.heard), 1)

class StudioControls(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'soundboard')
        note(self.home.path / 'data/soundboard/clips/Kick.wav')
        self.board = self.home.board()
        self.addCleanup(self.board.discard, self.board.running['soundboard'])

    def tap(self, x, y, now):
        self.board.press(x, y, 100, now)
        self.board.release(x, y, now + .02)

    def test_tapping_record_captures_the_downbeat_and_holding_it_opens_takes(self):
        b = self.board
        def studio():
            return b.state()['plugins']['soundboard']['state']['studio']

        b.act('record', 0.)
        self.tap(1, 1, 2.5)  # Kick on the first beat of the backing loop.
        b.act('record', 2.7)
        b.frame(7.45)
        b.press(0, 1, 127, 7.48)
        b.release(0, 1, 7.5)  # Complete the tap on the backing downbeat.
        self.assertTrue(studio()['performance']['recording'])
        b.press(0, 1, 127, 7.6)
        b.frame(8.2)
        b.release(0, 1, 8.25)
        self.assertTrue(studio()['performance']['recording'])  # Holding cannot save an unfinished take.
        self.tap(0, 1, 8.5)
        b.press(0, 1, 127, 9.)
        b.frame(10.)
        self.assertEqual(studio()['mode'], 'takes')
        self.assertTrue(studio()['playing'])
        self.assertFalse(studio()['performance']['recording'])
        self.assertEqual(len(studio()['performance']['items']), 1)
        before = len(self.home.sound.heard)
        self.tap(1, 8, 10.2)  # Audition the take while Record is still held.
        b.release(0, 1, 10.25)
        self.assertTrue(studio()['performance']['playing'])
        self.assertFalse(studio()['playing'])
        self.assertEqual([v.clip for v in self.home.sound.heard[before:]], ['Kick'])
        self.tap(1, 8, 10.4)  # The same pad stops it.
        self.assertFalse(studio()['performance']['playing'])
        self.assertEqual([v.clip for v in self.home.sound.heard[before:]], ['Kick'])
        self.tap(0, 1, 10.5)
        self.assertEqual(studio()['mode'], 'play')

    def test_the_performance_clock_does_not_invalidate_the_browser_state(self):
        b = self.board
        self.tap(0, 1, 1.)
        b.frame(1.1)
        revision = b.revision
        b.frame(3.)
        self.assertGreater(b.state()['plugins']['soundboard']['state']['studio']['performance']['elapsed'], 1.)
        self.assertEqual(b.revision, revision)

    def test_record_pulses_during_capture_and_unassigned_record_arm_stays_dark(self):
        b = self.board
        self.assertEqual(b.frame(1.)[1], BLACK)
        self.tap(0, 1, 3.)
        self.tap(1, 1, 3.5)
        self.assertGreater(len({b.frame(4. + step / 10)[10] for step in range(15)}), 1)
        self.tap(0, 1, 6.)
        self.assertEqual(b.frame(7.)[1], BLACK)
        self.tap(1, 0, 7.5)
        self.assertEqual(b.state()['plugins']['soundboard']['state']['studio']['mode'], 'play')

    def test_track_arrow_holds_record_then_add_a_layer_and_taps_toggle_mute(self):
        b = self.board
        def studio():
            return b.state()['plugins']['soundboard']['state']['studio']

        for start in (1., 12.):
            b.press(9, 7, 100, start)
            b.frame(start + 1.)
            self.assertEqual((studio()['recording_track'], studio()['count_in']), (1, 4))
            b.release(9, 7, start + 1.1)
            self.tap(1, 1, start + 3.6)
            self.tap(9, 7, start + 4.)  # Finish this layer.
            self.assertEqual(studio()['recording'], 'off')
            self.tap(8, 0, start + 4.1)  # Stop the loop.
        self.assertEqual(studio()['tracks'][1]['layers'], 2)
        self.tap(9, 7, 20.)
        self.assertTrue(studio()['tracks'][1]['muted'])
        self.tap(9, 7, 21.)
        self.assertFalse(studio()['tracks'][1]['muted'])

    def test_holding_quantise_shows_and_changes_the_timing_of_the_track_tapped(self):
        b = self.board
        def studio():
            return b.state()['plugins']['soundboard']['state']['studio']

        b.act('record', 1.)
        self.tap(1, 1, 3.6)
        b.act('record', 4.)
        initial = [t['timing'] for t in studio()['tracks']]
        self.assertIn('error', b.request('soundboard', 'studio', {}, b'{"action":"timing","value":"Tight"}'))
        b.press(0, 4, 100, 5.)
        before = b.frame(5.1)
        self.assertNotEqual(before[89], BLACK)
        self.assertEqual(before[79], BLACK)
        self.tap(9, 6, 5.2)  # Empty track three ignores the gesture.
        self.tap(9, 8, 5.3)
        self.assertNotEqual(b.frame(5.4)[89], before[89])
        b.release(0, 4, 5.5)
        self.tap(0, 4, 5.6)  # Quantise by itself changes nothing.
        after = studio()
        timing = [t['timing'] for t in after['tracks']]
        self.assertNotEqual(timing[0], initial[0])
        self.assertEqual(timing[1:], initial[1:])
        self.assertEqual(after['recording'], 'off')

    def test_a_tap_on_an_empty_tracks_arrow_changes_no_light(self):
        b = self.board
        self.assertEqual(b.frame(0.)[89], BLACK)
        b.act('record', 1.)
        self.tap(1, 1, 3.6)
        b.act('record', 4.)
        audible = b.frame(4.1)[89]
        self.assertNotEqual(audible, BLACK)
        self.tap(9, 7, 4.2)
        frame = b.frame(4.3)
        self.assertEqual(frame[89], audible)
        self.assertEqual(frame[79], BLACK)

    def test_holding_undo_shows_tracks_with_layers_and_never_removes_one_on_release(self):
        b = self.board
        b.act('record', 1.)
        self.tap(1, 1, 3.6)
        b.act('record', 4.)
        def layers():
            return b.state()['plugins']['soundboard']['state']['studio']['tracks'][0]['layers']
        b.press(0, 6, 100, 5.)  # Undo.
        b.release(0, 6, 6.)
        self.assertEqual(layers(), 1)
        b.press(0, 6, 100, 7.)
        frame = b.frame(7.2)
        self.assertNotEqual(frame[89], BLACK)
        self.assertEqual(frame[79], BLACK)
        self.tap(9, 8, 7.3)
        b.release(0, 6, 7.4)
        self.assertEqual(layers(), 0)

    def test_track_select_opens_songs_at_once_and_leaves_a_muted_track_muted(self):
        b = self.board
        def studio():
            return b.state()['plugins']['soundboard']['state']['studio']

        b.act('record', 1.)
        self.tap(1, 1, 3.6)
        b.act('record', 4.)
        b.act('mute:0', 4.1)
        b.press(2, 0, 100, 5.)
        self.assertEqual(studio()['mode'], 'songs')
        b.release(2, 0, 5.2)
        self.assertEqual(studio()['mode'], 'songs')
        self.tap(2, 0, 6.)
        self.assertEqual(studio()['mode'], 'play')
        self.assertTrue(studio()['tracks'][0]['muted'])

    def test_a_recording_uses_the_clip_on_the_chosen_bank_and_keeps_its_audio(self):
        b = self.board
        note(self.home.path / 'data/soundboard/clips/Studio bass C2.wav')
        self.tap(9, 8, 1.)  # Track 1.
        b.act('record', 2.)  # Record with a four-beat count-in.
        b.press(0, 8, 127, 3.)  # Hold Shift and choose Synth.
        self.tap(3, 8, 3.1)
        b.release(0, 8, 3.2)
        self.assertNotIn('Studio bass C2', [v.clip for v in self.home.sound.heard])
        self.tap(1, 1, 4.6)  # Bass on the newly chosen bank.
        self.tap(9, 8, 4.8)
        self.assertEqual(self.home.sound.heard[-1].clip, 'Studio bass C2')
        exported = b.request('soundboard', 'project', {}, b'')
        self.assertEqual([e['clip'] for e in exported['tracks'][0]['events']], ['Studio bass C2'])
        self.assertIn('error', b.request('soundboard', 'delete', {'id': 'Studio bass C2'}, b''))
        self.assertTrue((self.home.path / 'data/soundboard/clips/Studio bass C2.wav').exists())

    def test_shift_tempo_pads_adjust_the_song_and_keep_the_chooser_open(self):
        b = self.board
        initial = b.state()['plugins']['soundboard']['state']['studio']['bpm']
        self.tap(0, 8, 1.)
        self.tap(8, 8, 2.)
        self.tap(8, 8, 2.5)
        self.tap(8, 7, 3.)
        state = b.state()['plugins']['soundboard']['state']
        self.assertEqual(state['studio']['bpm'], initial + 1)
        self.assertEqual(state['picker'], 'banks')
        self.tap(8, 0, 3.1)  # The tempo controls also work while listening.
        self.tap(8, 7, 3.4)
        self.assertEqual(b.state()['plugins']['soundboard']['state']['studio']['bpm'], initial)
        self.tap(8, 8, 3.6)
        self.assertEqual(self.home.board().request('soundboard', 'project', {}, b'')['bpm'], initial + 1)

    def test_holding_a_tempo_pad_repeats_until_release_or_leaving_shift(self):
        b = self.board
        bpm = lambda: b.state()['plugins']['soundboard']['state']['studio']['bpm']
        self.tap(0, 8, 1.)
        before = bpm()
        b.press(8, 8, 100, 2.)
        self.assertEqual(bpm(), before + 1)
        for n in range(1, 16):
            b.frame(2. + n / 10)
        faster = bpm()
        self.assertGreater(faster, before + 1)
        b.release(8, 8, 3.6)
        b.frame(4.5)
        self.assertEqual(bpm(), faster)
        b.press(8, 7, 100, 5.)
        for n in range(1, 16):
            b.frame(5. + n / 10)
        slower = bpm()
        self.assertLess(slower, faster - 1)
        self.tap(0, 8, 6.6)
        self.tap(0, 8, 6.7)
        b.frame(8.)
        self.assertEqual(bpm(), slower)
        self.assertEqual(self.home.board().request('soundboard', 'project', {}, b'')['bpm'], slower)

    def test_pair_pages_reach_custom_banks_and_reopen_to_the_current_bank(self):
        b = self.board
        cfg = b.state()['config']
        cfg['plugins']['soundboard']['banks'] = [{'name': f'Kit {i+1}', 'pads': {}} for i in range(25)]
        b.configure(cfg)
        self.tap(0, 8, 1.)
        self.tap(5, 1, 1.2)  # Next page.
        self.tap(2, 7, 1.4)  # Right half of the last bank.
        state = b.state()['plugins']['soundboard']['state']
        self.assertEqual(state['bank'], 24)
        self.assertIsNone(state['picker'])
        self.tap(0, 8, 2.)
        self.assertEqual(b.state()['plugins']['soundboard']['state']['pad_hints']['1,7'], 'Kit 25')
        self.tap(1, 1, 2.2)  # Previous page.
        self.tap(4, 8, 2.4)
        self.assertEqual(b.state()['plugins']['soundboard']['state']['bank'], 1)

    def test_tempo_buttons_respect_the_limit_and_lock_during_a_take(self):
        import json
        b = self.board
        b.request('soundboard', 'studio', {}, json.dumps({'action':'tempo', 'value':200}).encode())
        self.tap(0, 8, 1.)
        state = b.state()['plugins']['soundboard']['state']
        self.assertNotIn('8,8', state['pad_hints'])
        self.assertIn('8,7', state['pad_hints'])
        self.tap(8, 8, 1.1)
        self.tap(0, 8, 1.15)  # Close Shift and begin recording.
        self.board.act('record', 1.2)
        self.tap(0, 8, 1.25)
        self.tap(8, 7, 1.3)
        state = b.state()['plugins']['soundboard']['state']
        self.assertNotIn('8,7', state['pad_hints'])
        self.assertEqual(state['studio']['bpm'], 200)
        self.assertTrue(state['studio']['playing'])

    def test_metronome_can_be_heard_without_any_factory_drum_clips(self):
        self.tap(0, 7, 1.)  # Click
        self.tap(8, 0, 2.)  # Play
        self.assertTrue(self.home.sound.heard)
        self.assertEqual([item['id'] for item in self.board.state()['plugins']['soundboard']['items']], ['Kick'])

    def test_count_in_is_a_distinct_sound_even_with_the_metronome_on(self):
        def audio(path):
            with wave.open(str(path), 'rb') as wav:
                return wav.readframes(wav.getnframes())

        with patch.object(self.home.sound, 'play', wraps=self.home.sound.play) as speaker:
            self.tap(0, 7, 1.)  # Click
            self.board.act('record', 2.)  # Record
            beat = 60 / self.board.state()['plugins']['soundboard']['state']['studio']['bpm']
            for n in range(1, 6):
                self.board.frame(2 + n * beat)
        ticks = [audio(call.args[0]) for call in speaker.call_args_list]
        self.assertEqual(len(ticks), 6)
        self.assertTrue(all(any(tick) for tick in ticks))
        self.assertEqual(ticks[:4], [ticks[0]] * 4)
        self.assertNotEqual(ticks[3], ticks[4])
        self.assertEqual(ticks[4], ticks[5])

    def test_recording_track_shows_the_count_in_and_the_start_of_the_layer(self):
        self.board.act('record', 2.)
        beat = 60 / self.board.state()['plugins']['soundboard']['state']['studio']['bpm']
        for n in range(5):
            frames = [self.board.frame(2 + (n + step / 20) * beat) for step in range(20)]
            lights = {frame[89] for frame in frames}
            self.assertGreater(len(lights), 1)
            self.assertEqual(len({frame[10] for frame in frames}), 1)  # Full capture is idle.
            if n == 0:
                count = lights
            if n == 4:
                self.assertNotEqual(lights, count)

    def test_shift_keeps_the_grid_in_the_bank_chooser_until_released(self):
        b = self.board
        note(self.home.path / 'data/soundboard/clips/Studio bass C2.wav')
        b.press(0, 8, 127, 1.)
        self.tap(3, 8, 1.1)  # Choose Synth with either pad of its pair.
        self.tap(4, 8, 1.2)
        self.assertEqual(self.home.sound.heard, [])
        b.release(0, 8, 1.3)
        self.tap(1, 1, 1.4)
        self.assertEqual([v.clip for v in self.home.sound.heard], ['Studio bass C2'])
