"""A performance can include a WAV played on another page and survive reopening the board."""
import json
import os
import threading
import time
import unittest

from unittest.mock import patch

from padcade.song_studio import new_song
from padcade.web import Runner
from tests.kit import Home, Sound, note, ship


def reopen(home, board):
    for run in list(board.running.values()):
        board.discard(run)
    return home.board()


def backing_note(home, clip, beat, **gate):
    ship(home, 'soundboard')
    data = home.path / 'data/soundboard'
    note(data / 'clips' / f'{clip}.wav')
    song = new_song(0, 120)
    song['tracks'][0].update(timing='As played', events=[{'beat': beat, 'clip': clip, 'take': 1, **gate}])
    (data / 'songs.json').write_text(json.dumps({'slot': 0, 'slots': [song] + [None] * 15}))
    board = home.board()
    board.open('soundboard')
    return board


class SongPages(unittest.TestCase):
    def test_a_backing_note_and_its_release_sound_between_display_frames(self):
        released = threading.Event()
        class Speaker(Sound):
            def play(self, clip, gain=1.):
                voice = super().play(clip, gain)
                voice.release = lambda seconds: released.set()
                return voice

        home = Home(self)
        home.sound = Speaker()
        board = backing_note(home, 'Keys C4', .1, length=.05)
        # Leave the next display refresh pending throughout the note's attack and release.
        with patch('padcade.web.FPS', .25):
            runner = Runner(board).start()
            try:
                runner.do(lambda now: board.act('play', now))
                self.assertTrue(released.wait(1), 'The note waited for the next display frame')
                self.assertEqual([voice.clip for voice in home.sound.heard], ['Keys C4'])
            finally:
                runner.stop()

    def test_a_due_backing_note_sounds_between_queued_requests(self):
        home = Home(self)
        board = backing_note(home, 'Kick', .05)
        observed = []
        runner = Runner(board)
        with patch('padcade.web.time') as clock:
            clock.monotonic.return_value = 10.
            def first(now):
                board.act('play', now)
                clock.monotonic.return_value += .05  # Time passes while this request owns the board.
            def second(now):
                observed.extend(voice.clip for voice in home.sound.heard)
                runner.stop()
            runner.post(first)
            runner.post(second)
            runner.run()
        self.assertEqual(observed, ['Kick'])

    def test_a_waiting_input_and_a_backing_note_keep_their_order_after_a_slow_frame(self):
        for arrival, expected in ((10.01, []), (10.05, ['Kick'])):
            with self.subTest(arrival=arrival), patch('padcade.web.time') as clock:
                home = Home(self)
                board = backing_note(home, 'Kick', .05)  # due at 10.025 seconds
                board.act('play', 10.)
                clock.monotonic.return_value = 10.
                observed = []
                runner = Runner(board)
                def input_job(now):
                    observed.extend(voice.clip for voice in home.sound.heard)
                    runner.stop()
                def ready():
                    clock.monotonic.return_value = arrival
                    runner.post(input_job)
                    clock.monotonic.return_value = 10.06  # Previous frame finishes late.
                runner.run(ready)
                self.assertEqual(observed, expected)

    def test_jamming_updates_the_live_sound_without_reloading_the_management_page(self):
        home = Home(self)
        ship(home, 'soundboard')
        note(home.path / 'data/soundboard/clips/Kick.wav')
        board = home.board()
        board.open('soundboard')
        board.press(1, 1, 127, 1.)  # First sound applies the starting volume.
        board.frame(1.)
        revision = board.state()['revision']
        board.press(1, 1, 64, 1.1)
        self.assertEqual([v.clip for v in home.sound.heard], ['Kick', 'Kick'])
        self.assertEqual(board.state()['revision'], revision)

        board.act('record', 2.)
        board.frame(4.5)
        revision = board.state()['revision']
        board.press(1, 1, 127, 4.6)
        state = board.state()
        self.assertGreater(state['revision'], revision)
        self.assertEqual(state['plugins']['soundboard']['state']['studio']['tracks'][0]['hits'], 1)

    def test_idle_click_pauses_on_another_page_and_when_the_board_sleeps(self):
        home = Home(self)
        ship(home, 'soundboard')
        (home.path / 'data/soundboard/clips').mkdir(parents=True)
        home.plugin('other')
        b = home.board()
        b.open('soundboard')
        b.act('click', 1.)
        b.frame(1.)
        b.open('other')
        b.tick(2.)
        b.frame(2.)
        self.assertEqual([v.clip for v in home.sound.heard], ['metronome'])
        b.open('soundboard')
        b.frame(3.)
        b.set_power(False)
        b.tick(4.)
        b.frame(4.)
        self.assertEqual([v.clip for v in home.sound.heard], ['metronome'] * 2)

    def test_the_loop_waits_on_another_page_and_goes_on_from_its_start_when_the_soundboard_is_back(self):
        home = Home(self)
        home.plugin('other')
        b = backing_note(home, 'Kick', 0)
        heard = lambda: [v.clip for v in home.sound.heard]
        b.act('play', 1.)
        b.tick(1.01)
        self.assertEqual(heard(), ['Kick'])
        b.open('other')
        for at in (4.99, 5.01):  # (the loop is four seconds long: its first beat comes round again)
            b.tick(at)
            b.frame(at)
        self.assertEqual(heard(), ['Kick'])
        b.open('soundboard')
        b.tick(5.6)
        b.frame(5.6)
        self.assertEqual(heard(), ['Kick', 'Kick'])

    def test_a_loop_that_waited_stays_silent_when_the_board_went_dark_in_between(self):
        home = Home(self)
        home.plugin('other')
        b = backing_note(home, 'Kick', 0)
        b.act('play', 1.)
        b.tick(1.01)
        b.open('other')
        b.set_power(False)
        b.set_power(True)
        b.open('soundboard')
        b.tick(9.)
        b.frame(9.)
        self.assertEqual([v.clip for v in home.sound.heard], ['Kick'])

    def test_another_page_can_supply_a_sound_to_a_loop(self):
        home = Home(self)
        ship(home, 'soundboard')
        (home.path / 'data/soundboard/clips').mkdir(parents=True)
        source = home.plugin('keys', button='top1', code="""
            class Plugin:
                def __init__(self, board):
                    self.board = board
                def press(self, x, y, velocity, now):
                    self.board.play(self.board.folder / 'note.wav')
                def frame(self, now):
                    return [(0,0,0)] * 64
        """)
        original = note(source / 'note.wav').read_bytes()
        board = home.board()
        board.open('soundboard')
        board.act('track:0', 1.)
        board.act('record', 1.)
        board.open('keys')
        board.tick(3.48)
        board.press(1, 1, 100, 3.48)  # Just before the first downbeat on another page.
        board.tick(9.)
        board.open('soundboard')
        project = board.request('soundboard', 'project', {}, b'')
        [event] = project['tracks'][0]['events']
        self.assertEqual(event['beat'], 0.)
        (source / 'note.wav').unlink()
        self.assertEqual(board.request('soundboard', 'clip', {'id': event['clip']}, b''), ('audio/wav', original))
        reopened = reopen(home, board)
        self.assertEqual(reopened.request('soundboard', 'project', {}, b''), project)

        reopened.open('soundboard')
        reopened.act('undo_track:0', 10.1)
        self.assertFalse(reopened.request('soundboard', 'project', {}, b'')['tracks'][0]['events'])
        for frame in range(120):
            reopened.tick(11. + frame / 50)
            reopened.frame(11. + frame / 50)
            if 'error' in reopened.request('soundboard', 'clip', {'id': event['clip']}, b''):
                break
            time.sleep(.01)
        answer = reopened.request('soundboard', 'clip', {'id': event['clip']}, b'')
        self.assertTrue('error' in answer, (reopened.page, reopened.state()['plugins']['soundboard']['state']['studio'],
                                            sorted(p.name for p in (home.path / 'data/soundboard').iterdir())))

    def test_a_take_keeps_another_pages_hard_stop_and_both_versions_of_its_sample(self):
        home = Home(self)
        ship(home, 'soundboard')
        (home.path / 'data/soundboard/clips').mkdir(parents=True)
        source = home.plugin('instrument', code="""
            class Plugin:
                def __init__(self, board):
                    self.board = board
                def press(self, x, y, velocity, now):
                    self.voice = self.board.play(self.board.folder / 'voice.wav', velocity / 127)
                def release(self, x, y, now):
                    self.voice.terminate()
            """) / 'voice.wav'
        first_sample = note(source, frames=44100).read_bytes()
        board = home.board()
        board.open('soundboard')
        board.act('performance', 1.)
        board.open('instrument')
        board.press(1, 1, 32, 2.)
        board.release(1, 1, 2.25)
        # Replacing plugin assets can preserve the file size and modification time.
        old_stat = source.stat()
        second_sample = first_sample[:-4] + b'\x00' * 4
        source.write_bytes(second_sample)
        os.utime(source, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        board.press(2, 1, 127, 3.)
        board.open('soundboard')
        board.act('performance', 4.)
        source.unlink()

        reopened = reopen(home, board)
        [saved] = reopened.state()['plugins']['soundboard']['state']['studio']['performance']['items']
        take = reopened.request('soundboard', 'take-project', {'slot':'0', 'id':saved['id']}, b'')
        first, second = take['tracks'][-1]['events']
        self.assertEqual(reopened.request('soundboard', 'clip', {'id':first['clip']}, b''), ('audio/wav', first_sample))
        self.assertEqual(reopened.request('soundboard', 'clip', {'id':second['clip']}, b''), ('audio/wav', second_sample))
        self.assertAlmostEqual(first['gain'], 32/127)
        self.assertAlmostEqual((first['hardStop'] - first['beat']) * 60 / take['bpm'], .25)
        self.assertNotIn('hardStop', second)

    def test_a_recorded_held_pad_plays_for_the_performed_length_after_reopening(self):
        home = Home(self)
        ship(home, 'soundboard')
        note(home.path / 'data/soundboard/clips/Chord Cm.wav')
        board = home.board()
        cfg = board.state()['config']
        cfg['plugins']['soundboard']['banks'] = [{'name':'Chords', 'pads':{'1,1':{'thing':'Chord Cm','mode':'hold'}}}]
        board.configure(cfg)
        board.act('record', 0.)
        board.press(1, 1, 127, 2.5)  # First downbeat at 96 BPM.
        board.release(1, 1, 3.125)
        live = home.sound.heard[-1]
        self.assertGreater(live.release_seconds, 0)
        board.act('play', 3.5)
        reopened = reopen(home, board)
        project = reopened.request('soundboard', 'project', {}, b'')
        self.assertEqual(project['releaseSeconds']['Chord Cm'], live.release_seconds)
        event, = project['tracks'][0]['events']
        self.assertEqual(event['length'], 1.)
        reopened.act('play', 10.)
        voice = home.sound.heard[-1]
        self.assertEqual(voice.clip, 'Chord Cm')
        reopened.frame(10.5)
        self.assertIsNone(voice.release_seconds)
        reopened.frame(10.625)
        self.assertEqual(voice.release_seconds, live.release_seconds)

    def test_lifting_a_piano_key_damps_its_voice_live_and_in_the_recording(self):
        home = Home(self)
        ship(home, 'soundboard')
        note(home.path / 'data/soundboard/clips/Piano C4.wav')
        board = home.board()
        settings = board.state()['config']
        settings['plugins']['soundboard']['banks'] = [{'name':'My piano', 'pads':{
            '1,1':{'thing':'Piano C4','mode':'hold'}}}]
        board.configure(settings)
        board.act('record', 0.)
        board.press(1, 1, 100, 2.5)
        live = home.sound.heard[-1]
        board.release(1, 1, 2.8125)
        self.assertIsNone(live.poll())
        self.assertGreater(live.release_seconds, 0)
        board.act('play', 3.)
        self.assertIsNotNone(live.poll())  # Stop also silences the fading voice.
        reopened = reopen(home, board)
        reopened.act('play', 5.)
        replay = home.sound.heard[-1]
        reopened.frame(5.3125)
        self.assertEqual(replay.release_seconds, live.release_seconds)
        self.assertIsNone(replay.poll())
        reopened.act('play', 5.4)
        self.assertIsNotNone(replay.poll())

    def test_pedal_keeps_released_keys_ringing_and_records_finger_and_pedal_lifts(self):
        home = Home(self)
        ship(home, 'soundboard')
        note(home.path / 'data/soundboard/clips/Piano C2.wav')
        board = home.board()
        board.act('bank:4', 0.)
        board.act('record', 0.)
        board.press(7, 0, 127, 2.4)  # Sends is the factory pedal.
        board.press(1, 1, 110, 2.5)
        first = home.sound.heard[-1]
        board.release(1, 1, 2.8125)
        self.assertIsNone(first.release_seconds)
        board.press(1, 1, 55, 3.125)
        repeated = home.sound.heard[-1]
        board.release(7, 0, 3.4375)
        self.assertGreater(first.release_seconds, 0)
        self.assertIsNone(repeated.release_seconds)
        board.release(1, 1, 3.75)
        self.assertEqual(repeated.release_seconds, first.release_seconds)
        board.act('play', 4.)

        reopened = reopen(home, board)
        first_hit, second_hit = reopened.request('soundboard', 'project', {}, b'')['tracks'][0]['events']
        self.assertEqual((first_hit['length'], first_hit['pedalDown'], first_hit['sustain']), (.5, 0., 1.))
        self.assertEqual(second_hit['length'], 1.)
        self.assertNotIn('sustain', second_hit)
        reopened.act('play', 10.)
        first_replay = home.sound.heard[-1]
        reopened.frame(10.625)
        second_replay = home.sound.heard[-1]
        self.assertIsNone(first_replay.release_seconds)
        reopened.frame(10.9375)
        self.assertEqual(first_replay.release_seconds, first.release_seconds)
        self.assertIsNone(second_replay.release_seconds)

    def test_stopping_the_loop_while_holding_the_pedal_keeps_new_notes_sustained(self):
        home = Home(self)
        ship(home, 'soundboard')
        note(home.path / 'data/soundboard/clips/Piano C2.wav')
        board = home.board()
        board.act('bank:4', 0.)
        board.act('play', 0.)
        board.press(7, 0, 100, 1.)
        board.act('play', 2.)  # Stop while the physical pedal is still held.
        board.press(1, 1, 100, 3.)
        board.release(1, 1, 3.1)
        voice = home.sound.heard[-1]
        self.assertIsNone(voice.release_seconds)
        board.release(7, 0, 3.5)
        self.assertIsNotNone(voice.release_seconds)

    def test_synth_pedal_holds_the_chord_while_basses_and_plucks_play_once(self):
        home = Home(self)
        ship(home, 'soundboard')
        rows = ['Studio bass C2', 'Jam bass C2', 'Jam pluck C4', 'Chord C3']
        for clip in rows:
            note(home.path / f'data/soundboard/clips/{clip}.wav')
        board = home.board()
        board.act('bank:1', 0.)
        board.act('record', 0.)
        for row in (1, 3, 5):
            board.press(1, row, 100, 2.5)
            board.release(1, row, 2.8125)
        chords = next(i for i, bank in enumerate(board.config['plugins']['soundboard']['banks']) if bank['name'] == 'All chords')
        board.act(f'bank:{chords}', 2.82)
        board.press(7, 0, 127, 2.85)
        board.press(1, 1, 100, 2.9)
        board.release(1, 1, 3.2125)
        voices = {v.clip: v for v in home.sound.heard if v.clip in rows}
        self.assertTrue(all(v.poll() is None and v.release_seconds is None for v in voices.values()))
        board.release(7, 0, 3.525)
        self.assertTrue(all(voices[clip].release_seconds is None for clip in rows[:3]))
        for clip in rows[3:]:
            self.assertIsNone(voices[clip].poll())
            self.assertGreater(voices[clip].release_seconds, 0)
        board.act('play', 3.6)
        project = reopen(home, board).request('soundboard', 'project', {}, b'')
        self.assertEqual(project['releaseSeconds'], {clip: voices[clip].release_seconds for clip in rows[3:]})
        events = project['tracks'][0]['events']
        self.assertEqual([e['clip'] for e in events if 'length' not in e], rows[:3])
        self.assertEqual([(e['clip'], round(e['length'], 4), round(e['sustain'], 4), round(e['pedalDown'], 4)) for e in events[3:]],
                         [(clip, .5, .5, .56) for clip in rows[3:]])

    def test_quantising_piano_keeps_the_performed_pedal_lift_on_its_beat(self):
        home = Home(self)
        ship(home, 'soundboard')
        note(home.path / 'data/soundboard/clips/Piano C2.wav')
        board = home.board()
        board.act('bank:4', 0.)
        board.act('record', 0.)
        board.press(1, 1, 90, 2.625)  # Recorded beat 0.2 snaps forward to 0.25.
        board.press(7, 0, 127, 2.75)
        board.release(1, 1, 2.875)
        board.release(7, 0, 3.125)  # Pedal off at beat 1.
        board.act('play', 3.5)
        board.act('quantise', 3.6)  # Tight
        reopened = reopen(home, board)
        reopened.act('play', 5.)
        reopened.frame(5.15625)
        voice = home.sound.heard[-1]
        self.assertEqual(voice.clip, 'Piano C2')
        reopened.frame(5.6)
        self.assertIsNone(voice.release_seconds)
        reopened.frame(5.625)
        self.assertGreater(voice.release_seconds, 0)

    def test_recorded_drum_accents_survive_reopening_and_a_meme_stays_at_its_level(self):
        home = Home(self)
        ship(home, 'soundboard')
        for name in ('Kick', 'Airhorn'):
            note(home.path / f'data/soundboard/clips/{name}.wav')
        board = home.board()
        cfg = board.state()['config']
        cfg['plugins']['soundboard']['banks'] = [{'name':'My bank', 'pads':{
            '1,1':{'thing':'Kick'}, '2,1':{'thing':'Airhorn'}}}]
        board.configure(cfg)
        board.act('record', 0.)
        board.press(1, 1, 24, 2.5)
        board.press(1, 1, 115, 3.125)
        board.press(2, 1, 24, 3.75)
        board.press(2, 1, 115, 4.375)
        performed = [v.gain for v in home.sound.heard if v.clip in ('Kick', 'Airhorn')]
        self.assertLess(performed[0], performed[1])
        self.assertEqual(performed[2], performed[3])
        board.act('play', 5.)
        reopened = reopen(home, board)
        project = reopened.request('soundboard', 'project', {}, b'')
        self.assertEqual([e.get('gain', 1.) for e in project['tracks'][0]['events']], performed)
        home.sound.heard.clear()
        reopened.act('play', 10.)
        for beat in range(1, 4):
            reopened.frame(10. + beat * .625)
        self.assertEqual([v.gain for v in home.sound.heard], performed)

    def test_pedal_is_added_once_to_a_free_button_in_existing_layouts(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        previous = {'buttons': {'left1': 'banks'}, 'updated_buttons': True, 'enabled': False}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': previous}})
        board = home.board()
        settings = board.state()['config']
        buttons = settings['plugins']['soundboard']['buttons']
        self.assertEqual(buttons.pop('bottom7', None), 'sustain')
        board.configure(settings)
        self.assertNotIn('bottom7', home.board().state()['config']['plugins']['soundboard']['buttons'])

        previous['buttons']['bottom7'] = 'click'
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': previous}})
        self.assertEqual(home.board().state()['config']['plugins']['soundboard']['buttons']['bottom7'], 'click')
        del previous['buttons']['bottom7']
        config.save(home.path / 'config.toml', {'buttons': {'bottom7': 'show'}, 'plugins': {'soundboard': previous}})
        self.assertNotIn('bottom7', home.board().state()['config']['plugins']['soundboard']['buttons'])

    def test_a_button_that_muted_the_selected_track_is_freed_in_an_existing_layout(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        (home.path / 'data/soundboard/clips').mkdir(parents=True)
        previous = {'buttons': {'left1': 'banks', 'bottom3': 'mute_selected'}, 'updated_buttons': 5, 'enabled': False}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': previous}})
        buttons = home.board().state()['config']['plugins']['soundboard']['buttons']
        self.assertEqual((buttons.get('left1'), 'bottom3' in buttons), ('banks', False))

    def test_old_stock_controls_upgrade_once_and_can_then_be_customised_freely(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        old = {'right1': 'volume_up', 'right2': 'volume_down', 'right8': 'stop',
               'bottom1': 'bank:0', 'bottom2': 'bank:1'}
        config.save(home.path / 'config.toml', {'plugins': {'soundboard': {'buttons': old, 'enabled': False}}})
        board = home.board()
        self.assertEqual(board.config['plugins']['soundboard']['buttons']['bottom2'], 'songs')
        settings = board.state()['config']
        settings['plugins']['soundboard']['buttons'] = old
        board.configure(settings)
        self.assertEqual(home.board().config['plugins']['soundboard']['buttons'], old)

    def test_stock_record_moves_to_the_main_button_and_frees_record_arm_on_upgrade(self):
        from padcade import config
        home = Home(self)
        ship(home, 'soundboard')
        for version, main, arm in ((3, 'record', 'performance'), (4, 'performance', 'takes')):
            stock = {'left8': main, 'bottom1': arm, 'bottom8': 'play'}
            for custom, overrides in (({}, {}), ({'bottom1': 'volume'}, {}), ({'left8': 'banks'}, {}),
                                      ({}, {'left8': 'show'}), ({}, {'bottom1': 'show'})):
                with self.subTest(version=version, custom=custom, overrides=overrides):
                    settings = {'updated_buttons': version, 'enabled': False, 'buttons': stock | custom}
                    config.save(home.path / 'config.toml', {'buttons': overrides, 'plugins': {'soundboard': settings}})
                    board = home.board()
                    controls = board.config['plugins']['soundboard']['buttons']
                    expected = stock | custom if custom or overrides else {'left8': 'performance', 'bottom8': 'play'}
                    self.assertEqual(controls, expected)
                    self.assertEqual({k: board.config['buttons'][k] for k in overrides}, overrides)
                    controls.update(bottom1='takes')
                    board.configure(board.config)
                    self.assertEqual(home.board().config['plugins']['soundboard']['buttons']['bottom1'], 'takes')
