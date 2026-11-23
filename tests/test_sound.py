"""The sound driver: overlapping clips as one stream, the amplifier kept awake."""
import io
import math
import struct
import tempfile
import threading
import time
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import Mock, patch

from padcade import synth
from padcade.drivers import sound
from padcade.drivers.sound import Amp


class AmpAwake(unittest.TestCase):

    def test_notes_on_an_output_without_a_case_amplifier_do_not_repeatedly_probe_it(self):
        probe = Mock(side_effect=FileNotFoundError('no case amplifier'))
        with patch.object(sound, 'AMP', Amp(start=probe)), patch.object(sound, 'PCM') as player:
            for _ in range(3):
                self.assertIs(sound.aplay(Path('tone.wav')), player.play.return_value)
        self.assertEqual(probe.call_count, 1)

    def test_a_clip_keeps_the_amplifier_awake_until_it_has_been_quiet_for_the_whole_time(self):
        closed = [threading.Event(), threading.Event()]
        start = Mock(side_effect=[event.set for event in closed])
        amp = Amp(awake=.15, start=start)
        amp.wake()
        time.sleep(.1)
        amp.wake()
        self.assertFalse(closed[0].wait(.1))
        self.assertTrue(closed[0].wait(2))
        amp.wake()
        self.assertTrue(closed[1].wait(2))
        self.assertEqual(start.call_count, 2)


class AudioPlayback(unittest.TestCase):
    def test_preparing_a_bank_keeps_the_output_ready_between_notes_then_closes_it_when_leaving(self):
        opened, parked, closed = (threading.Event() for _ in range(3))
        played = []
        class Output:
            frames = 64
            buffer_frames = 128
            def __enter__(self):
                opened.set()
                return self
            def __exit__(self, *args):
                closed.set()
            def wait(self):
                pass
            def write(self, block):
                if any(block):
                    played.append(block)
            def park(self):
                parked.set()

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tap.wav'
            with wave.open(str(path), 'wb') as wav:
                wav.setparams((2, 2, sound.PCM_RATE, 0, 'NONE', ''))
                wav.writeframes(struct.pack('<hh', 4400, -4400) * Output.frames)
            player = sound.PCMPlayer(start=Output)
            player.prepare([path])
            try:
                self.assertTrue(opened.wait(1))
                for _ in range(2):
                    player.play(path)
                    self.assertTrue(parked.wait(1))
                    parked.clear()
                    self.assertFalse(closed.is_set())
            finally:
                player.prepare([])
            self.assertTrue(closed.wait(1))
            self.assertEqual(played, [struct.pack('<hh', 4400, -4400) * Output.frames] * 2)

    def test_stopping_an_audible_voice_finishes_its_current_level_without_a_step(self):
        ready, finished = threading.Event(), threading.Event()
        blocks = []

        class Output:
            frames = 64

            def __enter__(self):
                return self

            def __exit__(self, *args):
                finished.set()

            def wait(self):
                if not ready.wait(2):
                    raise OSError('test did not start playback')

            def write(self, block):
                samples = array('h')
                samples.frombytes(block)
                blocks.append(samples[::2])
                if len(blocks) == 1:
                    voice.release(.2)
                elif len(blocks) == 12:
                    voice.terminate()

        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / 'held.wav'
            with wave.open(str(clip), 'wb') as wav:
                wav.setparams((2, 2, sound.PCM_RATE, 0, 'NONE', ''))
                wav.writeframes(array('h', [16000, 16000] * sound.PCM_RATE).tobytes())
            voice = sound.PCMPlayer(start=Output).play(clip)
            ready.set()
            self.assertTrue(finished.wait(2))
        before = blocks[11][-1]
        tail = [sample for block in blocks[12:] for sample in block]
        self.assertGreater(before, 0)
        self.assertGreater(tail[0], 0)
        # A forced stop during a release must neither jump back to full gain nor drop straight to silence.
        self.assertLessEqual(abs(tail[0] - before), abs(before - blocks[11][-2]) + 1)
        self.assertTrue(all(a >= b for a, b in zip(tail, tail[1:])))
        self.assertLess(tail.index(0), round(.2 * sound.PCM_RATE) - 11 * Output.frames)

    def test_a_voice_played_to_its_end_has_nothing_left_to_be_heard(self):
        samples = array('h', [16000, -16000] * sound.PEAK_STRIDE)  # (ends exactly where a stretch of its loudness table does)
        voice = sound.PCMVoice(samples, 1.)
        self.assertGreater(voice.remaining_peak(), 0)
        voice.read(len(samples) // 2)
        self.assertEqual(voice.remaining_peak(), 0)

    def test_releasing_one_voice_fades_its_next_frames_while_a_restrike_stays_at_full_level(self):
        samples = array('h', [16000, -16000] * 48)
        held, restrike = (sound.PCMVoice(samples, .5) for _ in range(2))
        self.assertEqual(list(held.read(1)), [8000, -8000])
        held.release(16 / sound.PCM_RATE)
        tail = list(held.read(8))
        held.release(16 / sound.PCM_RATE)
        tail.extend(held.read(16))
        self.assertEqual(len(tail), 2 * 16)
        self.assertEqual(tail[0], 8000)
        self.assertLess(tail[-2], tail[len(tail)//2])
        self.assertLess(tail[len(tail)//2], tail[0])
        self.assertTrue(all(a >= b >= 0 for a, b in zip(tail[::2], tail[2::2])))
        self.assertEqual(tail[1::2], [-left for left in tail[::2]])
        self.assertIsNotNone(held.poll())
        self.assertEqual(list(restrike.read(8)), [8000, -8000] * 8)
        restrike.release(16 / sound.PCM_RATE)
        restrike.terminate()
        self.assertIsNotNone(restrike.poll())

    def test_repeated_clips_reuse_samples_but_replaced_files_play_the_new_audio(self):
        ready, release, finished = threading.Event(), threading.Event(), threading.Event()
        blocks = []

        class Output:
            frames = 128
            def __enter__(self):
                return self
            def __exit__(self, *args):
                finished.set()
            def wait(self):
                ready.set()
                if not release.wait(2):
                    raise OSError('test did not release playback')
            def write(self, block):
                blocks.append(block)

        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / 'note.wav'
            with wave.open(str(clip), 'wb') as wav:
                wav.setparams((2, 2, 44100, 0, 'NONE', ''))
                wav.writeframes(struct.pack('<hh', 1000, -2500) * 128)
            player = sound.PCMPlayer(start=Output)
            try:
                with patch('padcade.drivers.sound.wave.open', wraps=wave.open) as opened:
                    player.play(clip)
                    self.assertTrue(ready.wait(2))
                    for i in range(12):  # More instruments than simultaneous voices.
                        other = clip.with_name(f'key-{i}.wav')
                        other.write_bytes(clip.read_bytes())
                        player.play(other).terminate()
                    player.play(clip)
                    self.assertEqual(opened.call_count, 13)
                with wave.open(str(clip), 'wb') as wav:
                    wav.setparams((2, 2, 44100, 0, 'NONE', ''))
                    wav.writeframes(struct.pack('<hh', 3000, 1500) * 256)
                player.play(clip)
            finally:
                release.set()
                self.assertTrue(finished.wait(2))
            self.assertEqual(blocks[0], struct.pack('<hh', 5000, -3500) * 128)
            self.assertEqual(blocks[1], struct.pack('<hh', 3000, 1500) * 128)

    def test_layering_the_bass_row_does_not_make_it_quieter_than_one_note(self):
        start, done = threading.Event(), threading.Event()
        samples = array('h')
        class Output:
            frames = sound.PCM_TARGET_FRAMES

            def __enter__(self):
                if not start.wait(2):
                    raise OSError('test did not release playback')
                return self

            def __exit__(self, *args):
                done.set()

            def wait(self):
                pass

            def write(self, block):
                samples.frombytes(block)

        notes = []
        with tempfile.TemporaryDirectory() as folder:
            player = sound.PCMPlayer(start=Output)
            for pitch in ('C2', 'D2', 'E2', 'F2', 'G2', 'A2', 'B2', 'C3'):
                _, _, pcm = next(synth.factory_clips(Path(folder), ['Studio bass ' + pitch]))
                tone = array('h', pcm)[::2]
                notes.append(tone)
                clip = Path(folder) / f'{pitch}.wav'
                with wave.open(str(clip), 'wb') as wav:
                    wav.setparams((2, 2, sound.PCM_RATE, 0, 'NONE', ''))
                    wav.writeframes(array('h', (value for x in tone for value in (x, -x))).tobytes())
                player.play(clip)
            start.set()
            self.assertTrue(done.wait(2))
        reference = list(map(sum, zip(*notes)))
        left = samples[:len(reference)*2:2]
        self.assertEqual(samples[1:len(reference)*2:2], array('h', (-x for x in left)))
        self.assertTrue(all(x * y >= 0 for x, y in zip(left, reference)), 'loud peaks must never wrap')
        self.assertGreaterEqual(sum(x*x for x in left), sum(x*x for x in notes[0]))

    def test_a_new_note_reaches_its_level_while_old_samples_have_only_silence_left(self):
        start, done = threading.Event(), threading.Event()
        audio = array('h')
        rate = sound.PCM_RATE
        with tempfile.TemporaryDirectory() as folder:
            chord = Path(folder) / 'chord.wav'
            melody = Path(folder) / 'melody.wav'
            for path, track in ((chord, [24000] * (rate // 8) + [0] * rate),
                                (melody, [14000] * (rate // 2))):
                with wave.open(str(path), 'wb') as wav:
                    wav.setparams((2, 2, rate, 0, 'NONE', ''))
                    wav.writeframes(array('h', (x for value in track for x in (value, value))).tobytes())

            class Output:
                frames = sound.PCM_TARGET_FRAMES

                def __enter__(self):
                    if not start.wait(2):
                        raise OSError('test did not release playback')
                    return self

                def __exit__(self, *args):
                    done.set()

                def wait(self):
                    pass

                def write(self, block):
                    before = len(audio)
                    audio.frombytes(block)
                    if before < rate // 2 <= len(audio):
                        player.play(melody)

            player = sound.PCMPlayer(start=Output)
            for _ in range(7):
                player.play(chord)
            start.set()
            self.assertTrue(done.wait(2))
        # The chord WAVs are still open for the whole melody, but contribute no sound here.
        self.assertEqual(max(audio[rate:rate + rate // 8]), 14000)

    def test_a_note_pressed_while_output_is_busy_joins_the_next_stereo_block(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        blocks, sizes = [], []

        class Output:
            frames = 128
            samples = io.BytesIO()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                blocks.append(self.samples.getvalue())
                finished.set()

            def wait(self):
                started.set()
                if not release.wait(2):
                    raise OSError('test did not release playback')

            def write(self, block):
                sizes.append(len(block))
                self.samples.write(block)

        with tempfile.TemporaryDirectory() as folder:
            clips = [Path(folder) / f'{k}.wav' for k in range(2)]
            for clip, frame in zip(clips, [(1000, -2500), (3000, 1500)]):
                with wave.open(str(clip), 'wb') as wav:
                    wav.setparams((2, 2, 44100, 0, 'NONE', ''))
                    wav.writeframes(struct.pack('<hh', *frame) * 1024)

            player = sound.PCMPlayer(start=Output)
            first = player.play(clips[0])
            self.assertTrue(started.wait(2))
            second = player.play(clips[1], gain=.5)
            release.set()
            self.assertTrue(finished.wait(2))
            self.assertEqual(len(blocks), 1)
            self.assertEqual(set(sizes), {512})
            self.assertEqual(blocks[0][:4096], struct.pack('<hh', 2500, -1750) * 1024)
            self.assertIsNotNone(first.poll())
            self.assertIsNotNone(second.poll())
