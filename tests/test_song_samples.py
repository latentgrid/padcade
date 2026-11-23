"""Recording a pad on another page takes an immutable copy before returning to the player."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from padcade import song_samples


class SampleCapture(unittest.TestCase):
    def test_next_pad_can_play_while_the_first_sample_is_being_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = Path(tmp)
            store = song_samples.SampleStore(clips)
            source = clips / 'outside.wav'
            source.write_bytes(b'first version')
            writing, resume = threading.Event(), threading.Event()
            publish = song_samples.write_sample

            def slow_disk(*args):
                writing.set()
                if not resume.wait(2):
                    raise OSError('Disk did not resume')
                publish(*args)

            with patch.object(song_samples, 'write_sample', slow_disk):
                try:
                    name = store.keep(source)
                    self.assertTrue(writing.wait(2))
                    source.write_bytes(b'second version')
                    other = store.keep(source)
                    source.unlink()
                finally:
                    resume.set()
                store.flush()
            self.assertEqual((clips / (name + '.wav')).read_bytes(), b'first version')
            self.assertEqual((clips / (other + '.wav')).read_bytes(), b'second version')
