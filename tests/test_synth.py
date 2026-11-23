"""A partial factory install sounds the same as a fresh one."""
import unittest

from padcade import synth
from tests.kit import SHIPPED


class FactoryClips(unittest.TestCase):
    def test_partial_install_renders_only_the_requested_factory_instruments(self):
        recordings = SHIPPED / 'soundboard/sounds'
        wanted = {'Shaker', 'Soft keys Eb4', 'Airhorn', 'Tambourine', 'Warm pad Bb4', 'Chord Cm',
                  'Echo hit', 'Piano Db2', 'Piano B5', 'Warm pad A4', 'Chord Bdim', 'Crash', 'Short guiro', 'Open conga',
                  'Round bass C2', 'Plucked bass C2', 'Punch kick', 'Electric clap', 'Electric open hat', 'Snare flam',
                  'Jam clap', 'Jam crash', 'Jam pluck Eb5', 'Jam bass C3', 'Pluck Bb5', 'Bass F3', 'Hat', 'Tom',
                  'Jam pluck Gb4', 'Jam bass Db3', 'Studio bass Ab2', 'Chord Db3 minor',
                  'Chord B4 minor', 'Chord C5 major', 'Chord B3 half diminished', 'Jam pluck D6', 'Jam bass B2', 'Studio bass D4', 'Chord G7',
                  'Classic kick', 'Electric kick C1', 'Electric kick G1', 'Electric kick C2', 'Electric kick G2'}
        upgraded = {name: (rate, pcm) for name, rate, pcm in synth.background_factory_clips(recordings, wanted)}
        self.assertEqual(wanted, upgraded.keys())
        for rate, pcm in upgraded.values():
            self.assertGreater(rate, 0)
            self.assertTrue(any(pcm))
