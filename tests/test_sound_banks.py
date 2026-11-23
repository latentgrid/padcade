"""An owner can install a bank of playable pads without installing executable code."""
import io
import unittest
import zipfile

from tests.kit import Home, note, ship


class SoundBanks(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'soundboard')
        self.original = note(self.home.path / 'data/soundboard/clips/Hit.wav').read_bytes()
        self.new_sound = note(self.home.path / 'hit.wav', 2000).read_bytes()
        self.board = self.home.board()

    def pack(self, pads, files):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('bank.toml', 'name="Table kit"\n[pads]\n' + pads)
            for name, contents in files.items():
                archive.writestr(name, contents)
        return data.getvalue()

    def test_an_imported_bank_is_playable_editable_and_safe_to_reimport(self):
        pack = self.pack('"2,4"={file="Hit.wav", mode="hold", dynamics="velocity", colour="#248ace"}',
                         {'Hit.wav': self.new_sound})
        banks = self.board.state()['config']['plugins']['soundboard']['banks']
        answer = self.board.request('soundboard', 'import-bank', {}, pack)
        self.assertEqual(answer, {'name': 'Table kit', 'bank': len(banks)})
        board = self.home.board()
        self.assertEqual(board.request('soundboard', 'import-bank', {}, pack), answer)
        plugin = board.state()['config']['plugins']['soundboard']
        self.assertEqual(plugin['banks'][:-1], banks)
        item = plugin['banks'][-1]['pads']['2,4']
        self.assertEqual((item['mode'], item['colour']), ('hold', '#248ace'))
        groups = {c['id']: c['kind'] for c in board.state()['plugins']['soundboard']['items']}
        self.assertEqual(groups[item['thing']], 'Table kit')
        board.act('bank:' + str(answer['bank']), 1)
        board.press(2, 4, 100, 2)
        self.assertEqual(self.home.sound.heard[-1].clip, item['thing'])
        board.release(2, 4, 2.1)
        board.press(2, 4, 40, 3)
        self.assertLess(self.home.sound.heard[-1].gain, self.home.sound.heard[-2].gain)
        self.assertEqual(board.request('soundboard', 'clip', {'id': item['thing']}, b'')[1], self.new_sound)
        self.assertEqual(board.request('soundboard', 'clip', {'id': 'Hit'}, b'')[1], self.original)
        settings = board.state()['config']
        settings['plugins']['soundboard']['banks'][-1]['pads'].clear()
        board.configure(settings)
        self.assertIn('error', board.request('soundboard', 'import-bank', {}, pack))
        self.assertEqual(board.state()['config']['plugins']['soundboard']['banks'][-1]['pads'], {})

    def test_an_archive_with_an_enormous_list_of_files_is_refused_before_the_list_is_read(self):
        from unittest.mock import patch
        from padcade.sound_banks import unzip
        packed = io.BytesIO()
        with zipfile.ZipFile(packed, 'w') as archive:
            for i in range(12000):  # (little to upload, but a long list for a small Pi to hold in memory)
                archive.writestr(f'{i:060d}', b'')
        with patch('zipfile.ZipFile', side_effect=AssertionError('the list was read')), self.assertRaises(ValueError):
            unzip(packed.getvalue())
        small = io.BytesIO()
        with zipfile.ZipFile(small, 'w') as archive:
            archive.writestr('pack.toml', 'name = "x"')
        with unzip(small.getvalue()) as archive:
            self.assertEqual(archive.namelist(), ['pack.toml'])
        with self.assertRaises(ValueError):
            unzip(b'not an archive')

    def test_a_damaged_or_unsafe_pack_leaves_no_partial_bank_or_clips(self):
        banks = self.board.state()['config']['plugins']['soundboard']['banks']
        for filename, data in [('Broken.wav', b'bad wave'), ('../Outside.wav', self.new_sound)]:
            with self.subTest(filename=filename):
                pack = self.pack('"1,1"={file="First.wav"}\n"2,1"={file="'+filename+'"}',
                                 {'First.wav': self.new_sound, filename: data})
                self.assertIn('error', self.board.request('soundboard', 'import-bank', {}, pack))
                state = self.board.state()
                self.assertEqual(state['config']['plugins']['soundboard']['banks'], banks)
                self.assertEqual([item['id'] for item in state['plugins']['soundboard']['items']], ['Hit'])
