"""What the management page asks of a song in one step: its tracks moved, renamed and cleared, the song copied."""
import json
import unittest

from padcade.song_studio import new_song
from tests.kit import Home, note, ship


class SongRequests(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'soundboard')
        data = self.home.path / 'data/soundboard'
        note(data / 'clips/Kick.wav')
        song = new_song(0, 120)
        song['tracks'][5].update(name='Drums', events=[{'beat': 0, 'clip': 'Kick', 'take': 1}])
        (data / 'songs.json').write_text(json.dumps({'slot': 0, 'slots': [song] + [None] * 15}))
        self.board = self.home.board()
        self.board.open('soundboard')

    def ask(self, action, **more):
        return self.board.request('soundboard', 'studio', {}, json.dumps({'action': action, **more}))

    def studio(self):
        return self.board.state()['plugins']['soundboard']['state']['studio']

    def names(self):
        return [track['name'] for track in self.studio()['tracks']]

    def test_a_track_is_moved_to_the_place_the_page_names(self):
        self.assertEqual(self.ask('move_track', **{'from': 5, 'to': 1}), {})
        self.assertEqual(self.names()[:3], ['Track 1', 'Drums', 'Track 3'])
        for wrong in ({'from': 1, 'to': 9}, {'from': '1', 'to': 2}, {'to': 2}):
            with self.subTest(wrong=wrong):
                self.assertIn('error', self.ask('move_track', **wrong))
        self.assertEqual(self.names()[:3], ['Track 1', 'Drums', 'Track 3'])

    def test_a_track_is_renamed_retimed_and_cleared_by_naming_it(self):
        self.assertEqual(self.ask('rename_track', index=5, value='Beat'), {})
        self.assertEqual(self.ask('timing', index=5, value='Tight'), {})
        track = self.studio()['tracks'][5]
        self.assertEqual((track['name'], track['timing']), ('Beat', 'Tight'))
        self.assertIn('error', self.ask('rename_track', index=8, value='Nowhere'))
        self.assertEqual(self.ask('clear_track:5'), {})
        self.assertEqual(self.studio()['tracks'][5]['hits'], 0)
        self.assertEqual(self.ask('restore'), {})
        self.assertEqual(self.studio()['tracks'][5]['hits'], 1)

    def test_copying_the_song_says_where_the_copy_went(self):
        self.assertEqual(self.ask('duplicate_song'), {'slot': 1})
        self.assertEqual(self.studio()['slots'][:2], ['Song 1', 'Song 1 (copy)'])


if __name__ == '__main__':
    unittest.main()
