import json
import tempfile
import unittest
from pathlib import Path

from padcade.song_studio import Studio, new_song


class Performances(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.heard = []
        self.s = self.reopen()

    def reopen(self):
        return Studio(Path(self.home.name), lambda clip, gain: self.heard.append((clip, gain)),
                      lambda: None, lambda count: None)

    def test_a_missing_or_damaged_take_leaves_the_rest_of_the_library_playable(self):
        s = self.s
        for start in (0, 2):
            s.action('performance', start)
            s.hit('Kick', start + .2)
            s.action('performance', start + 1)
        s.action('takes', 4)
        broken, good = [item['id'] for item in s.take_items()]
        file = Path(self.home.name, 'takes', broken + '.json')
        file.unlink()
        for contents in (None, b'{', b'{"bpm":0,"tracks":[]}'):
            if contents is not None:
                file.write_bytes(contents)
            self.assertFalse(s.action('take:' + broken, 4))
            self.assertIsNone(s.take_project(broken))
            self.assertIn('Take 2', s.state()['performance']['error'])
        self.assertTrue(s.action('take:' + good, 5))
        s.tick(5.2)
        self.assertEqual(self.heard[-1], ('Kick', 1.))

    def test_a_take_replays_the_backing_and_live_pads_over_multiple_laps(self):
        s = self.s
        s.edit('bpm', 120)
        s.action('record', 0)
        s.hit('Kick', 2)
        s.tick(6)
        s.action('play', 6.1)
        self.assertTrue(s.action('performance', 7))
        s.action('play', 7)
        s.hit('Airhorn', 8, gain=.5)
        s.tick(11)
        s.action('mute:0', 11.1)
        s.tick(15)
        s.action('performance', 16)
        self.assertTrue(s.state()['playing'])
        again = self.reopen()
        again.action('takes', 20)
        take = again.state()['performance']['items'][0]
        self.assertEqual(take['duration'], 9)
        project = again.take_project(take['id'])
        self.assertEqual([e['beat'] for e in project['tracks'][0]['events']], [0, 8])
        self.assertEqual(project['tracks'][-1]['events'][0]['clip'], 'Airhorn')
        self.heard.clear()
        again.action('play', 20)
        again.tick(21)
        again.tick(24)
        again.tick(29)
        self.assertEqual(self.heard, [('Kick', 1.), ('Airhorn', .5), ('Kick', 1.)])
        self.assertFalse(again.state()['performance']['playing'])

    def test_take_playback_sounds_and_releases_a_note_at_its_own_deadlines(self):
        from tests.kit import Sound
        speaker = Sound()
        s = Studio(Path(self.home.name), speaker.play, lambda: None, lambda _: None)
        s.action('performance', 0)
        s.hit('Chord C', .11, held_pad='chord')
        s.release_hit('chord', .23)
        s.action('performance', .31)
        s.action('takes', 20)
        s.action('play', 20)
        s.tick(s.next_deadline())
        voice, = speaker.heard
        self.assertIsNone(voice.release_seconds)
        s.tick(s.next_deadline())
        self.assertIsNotNone(voice.release_seconds)
        s.tick(s.next_deadline())
        self.assertFalse(s.state()['performance']['playing'])
        self.assertIsNone(s.next_deadline())

    def test_a_short_live_stab_in_a_performance_sounds_between_playback_frames(self):
        from tests.kit import Sound
        speaker = Sound()
        s = Studio(Path(self.home.name), speaker.play, lambda: None, lambda _: None)
        s.action('performance', 0)
        s.hit('Chord C', .5, held_pad='chord')
        s.release_hit('chord', .51)
        s.action('performance', 2)
        s.action('takes', 3)
        s.action('play', 3)
        s.tick(3.53)
        voice, = speaker.heard
        self.assertIsNone(voice.release_seconds)
        s.tick(3.55)
        self.assertGreater(voice.release_seconds, 0)

    def test_library_delete_and_undo_keep_each_song_and_its_samples_independent(self):
        s = self.s
        s.action('performance', 0)
        s.hit('My clip', .2)
        s.action('performance', 1)
        s.action('songs', 1)
        s.action('duplicate', 1)
        s.action('slot:0', 1)
        s.action('slot:1', 1)
        s.action('takes', 1)
        key = s.state()['performance']['selected']
        self.assertTrue(s.edit('rename_take', 'My copy'))
        s.action('delete', 1)
        s.action('take:' + key, 1)
        s.action('delete', 1)
        self.assertEqual(s.take_items(), [])
        self.assertTrue(s.uses_clip('My clip'))
        again = self.reopen()
        again.action('takes', 2)
        self.assertTrue(again.action('undo', 2))
        self.assertEqual(again.take_project(key)['title'], 'My copy')
        again.action('takes', 2)
        again.action('songs', 2)
        again.action('slot:0', 2)
        self.assertEqual(again.take_project(key)['title'], 'Take 1')

    def test_any_take_is_renamed_and_deleted_by_itself_and_comes_back(self):
        s = self.s
        for start in (0, 2):
            s.action('performance', start)
            s.hit('My clip', start + .2)
            s.action('performance', start + 1)
        s.action('takes', 4)
        newest, older = [item['id'] for item in s.state()['performance']['items']]
        self.assertEqual(s.state()['performance']['selected'], newest)
        self.assertTrue(s.edit('rename_take', 'First try', take=older))
        self.assertTrue(s.action('delete_take:' + older, 4))
        performance = s.state()['performance']
        self.assertEqual(([item['id'] for item in performance['items']], performance['deleted']), ([newest], {'id': older, 'title': 'First try'}))
        self.assertTrue(s.action('undo', 4))
        self.assertEqual([item['title'] for item in s.state()['performance']['items']], ['Take 2', 'First try'])

    def test_a_performance_can_be_saved_while_a_new_layer_is_counted_in(self):
        s = self.s
        s.edit('bpm', 120)
        s.action('record', 0)
        s.hit('Tom', 2)
        s.action('record', 2.1)
        s.action('mute:0', 2.2)
        s.action('performance', 3)
        s.action('record', 3)
        self.assertEqual(s.state()['recording_track'], 1)
        self.assertTrue(s.action('performance', 4))
        self.assertEqual(s.take_items()[0]['duration'], 1)

    def test_a_failed_save_can_be_retried_with_the_recording_intact(self):
        s = self.s
        s.action('performance', 0)
        s.hit('Kick', 1)
        # A regular file in this location makes creating the take impossible.
        obstacle = Path(self.home.name) / 'takes'
        obstacle.touch()
        self.assertFalse(s.action('performance', 2))
        self.assertTrue(s.state()['performance']['recording'])
        obstacle.unlink()
        self.assertTrue(s.action('performance', 3))
        item = s.take_items()[0]
        self.assertEqual(s.take_project(item['id'])['tracks'][-1]['events'][0]['clip'], 'Kick')

    def test_backing_pedal_and_tempo_changes_keep_editable_finger_and_pedal_releases(self):
        from tests.kit import Sound
        song = new_song(0, 120)
        song['tracks'][0]['events'] = [{'clip': 'Piano C4', 'beat': 0., 'length': 1., 'take': 1,
                                        'pedalDown': .5, 'sustain': 1.}]
        home = Path(self.home.name)
        (home / 'songs.json').write_text(json.dumps({'slots': [song] + [None] * 15, 'slot': 0}))
        sound = Sound()
        s = Studio(home, sound.play, lambda: None, lambda _: None)
        s.action('play', 6)
        s.action('performance', 9.9)
        s.tick(10)
        s.tick(10.25)
        s.edit('bpm', 60)
        s.tick(11.75)
        s.action('performance', 12)
        note = s.take_project(s.take_items()[0]['id'])['tracks'][0]['events'][0]
        self.assertAlmostEqual(note['length'], 1.5)
        self.assertAlmostEqual(note['sustain'], 2.)
        self.assertAlmostEqual(note['pedalDown'], .7)
