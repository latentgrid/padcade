"""A song exported from the Soundboard can be brought back, on the same board or another."""
import io
import json
import tempfile
import time
import unittest
import wave
import zipfile
from pathlib import Path

from padcade.song_import import read_song
from tests.kit import Home, note, ship


class SongImport(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'soundboard')
        self.clips = self.home.path / 'data/soundboard/clips'
        self.kick = note(self.clips / 'Kick.wav').read_bytes()
        self.other = note(self.home.path / 'other.wav', 2000).read_bytes()
        self.board = self.home.board()

    def pack(self, samples, **changes):
        """A ZIP as the page's Export makes it: project.json and the song's sounds under samples/."""
        tracks = [{'name': f'Track {i + 1}', 'timing': 'Gentle', 'muted': False, 'events': []} for i in range(8)]
        tracks[2].update(name='Drums', events=[{'beat': 0, 'clip': clip, 'take': 1} for clip in samples])
        sounds = {clip: {'file': f'samples/{n} {clip}.wav'} for n, clip in enumerate(samples, 1)}
        project = dict({'format': 'Padcade song 1', 'loopBeats': 8, 'title': 'Night loop', 'bpm': 103,
                        'tracks': tracks, 'sounds': sounds}, **changes)
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('project.json', json.dumps(project))
            archive.writestr('tracks/3 Drums.wav', self.kick)  # (the stems and the MIDI file are not needed to come back)
            for clip, contents in samples.items():
                archive.writestr(sounds[clip]['file'], contents)
        return data.getvalue()

    def bring(self, pack):
        return self.board.request('soundboard', 'import-song', {}, pack)

    def do(self, action):
        return self.board.request('soundboard', 'studio', {}, json.dumps({'action': action}))

    def studio(self):
        return self.board.state()['plugins']['soundboard']['state']['studio']

    def play_slot(self, slot):
        self.do('songs')
        self.do(f'slot:{slot}')
        self.do('play')
        self.board.frame(self.board.now + .05)
        return [voice.clip for voice in self.home.sound.heard]

    def test_an_exported_song_comes_back_in_the_first_empty_slot_and_plays(self):
        self.assertEqual(self.bring(self.pack({'Kick': self.kick})), {'slot': 1, 'title': 'Night loop'})
        self.assertEqual(self.studio()['slots'][:3], ['Song 1', 'Night loop', None])
        self.assertEqual(self.play_slot(1), ['Kick'])
        studio = self.studio()
        self.assertEqual((studio['title'], studio['bpm']), ('Night loop', 103))
        self.assertEqual([(t['name'], t['hits']) for t in studio['tracks'][1:4]], [('Track 2', 0), ('Drums', 1), ('Track 4', 0)])

    def test_a_sound_the_board_lacks_comes_along_into_the_clip_library(self):
        self.bring(self.pack({'Bell': self.other}))
        self.assertEqual(self.play_slot(1), ['Bell'])
        self.assertIn('Bell', [item['id'] for item in self.board.state()['plugins']['soundboard']['items']])

    def test_a_sound_that_differs_from_the_boards_own_of_that_name_is_kept_apart(self):
        self.bring(self.pack({'Kick': self.other}))
        heard = self.play_slot(1)
        self.assertNotEqual(heard, ['Kick'])
        self.assertEqual((self.clips / f'{heard[0]}.wav').read_bytes(), self.other)
        self.assertEqual((self.clips / 'Kick.wav').read_bytes(), self.kick)
        self.assertNotIn(heard[0], [item['id'] for item in self.board.state()['plugins']['soundboard']['items']])

    def test_what_is_not_an_exported_song_is_refused_and_leaves_nothing_behind(self):
        good = self.pack({'Bell': self.other})
        take = self.pack({'Bell': self.other}, format='Padcade performance 1')
        no_sound = self.pack({'Bell': self.other}, sounds={})
        outside = self.pack({'Bell': self.other}, sounds={'Bell': {'file': '../Bell.wav'}})
        broken = self.pack({'Bell': self.other[:60]})
        odd_name = self.pack({'../Bell': self.other})
        too_fast = self.pack({'Bell': self.other}, bpm=900)
        unwritable = self.pack({'Bell': self.other}, title='\ud800')  # (half a character: it cannot be saved)
        for pack in (b'not a zip', good[:200], take, no_sound, outside, broken, odd_name, too_fast, unwritable):
            with self.subTest(pack=pack[:40]):
                self.assertIn('error', self.bring(pack))
        self.assertEqual(self.studio()['slots'][1], None)
        self.assertEqual([clip.name for clip in self.clips.parent.rglob('*Bell*')], [])
        self.assertEqual(self.bring(good), {'slot': 1, 'title': 'Night loop'})  # (and the song book still saves)

    def test_sounds_that_unpack_to_more_than_a_song_may_hold_are_refused(self):
        many = {f'Bell {n}': self.other for n in range(12)}
        big = lambda data, target: target.write_bytes(bytes(8 * 2**20))  # (what converting a small odd WAV can come to)
        low = io.BytesIO()
        with wave.open(low, 'wb') as wav:
            wav.setparams((1, 1, 8000, 0, 'NONE', ''))
            wav.writeframes(bytes(800))
        with tempfile.TemporaryDirectory() as staging, self.assertRaises(ValueError):
            read_song(self.pack(dict.fromkeys(many, low.getvalue())), Path(staging), big)

    def test_a_songs_own_sound_is_cleared_away_once_the_song_is_gone_for_good(self):
        private = 'Song sample ' + 'a' * 64
        self.bring(self.pack({private: self.other}))
        self.do('songs')
        self.do('slot:2')  # (a second song, so that deleting it too leaves nothing to restore the first from)
        self.do('songs')
        for slot in (1, 2):
            self.do('delete')
            self.do(f'slot:{slot}')
            self.do('delete')
        deadline = time.monotonic() + 3
        while (self.clips / f'{private}.wav').exists() and time.monotonic() < deadline:
            self.board.frame(self.board.now + .05)
            time.sleep(.02)
        self.assertFalse((self.clips / f'{private}.wav').exists())

    def test_a_full_song_book_refuses_the_song_and_keeps_its_sounds_out(self):
        self.do('songs')
        for slot in range(1, 16):
            self.do(f'slot:{slot}')
            self.do('songs')
        answer = self.bring(self.pack({'Bell': self.other}))
        self.assertIn('Delete a song', answer['error'])
        self.assertFalse((self.clips / 'Bell.wav').exists())

    def test_a_song_is_not_brought_in_while_a_layer_is_being_recorded(self):
        self.do('record_track:0')
        self.assertIn('Finish recording', self.bring(self.pack({'Kick': self.kick}))['error'])
        self.assertEqual(self.studio()['slots'][1], None)


if __name__ == '__main__':
    unittest.main()
