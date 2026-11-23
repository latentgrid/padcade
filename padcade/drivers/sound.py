"""The board's sound: clips mixed into one stream on the Pi's sound card (libasound, no audio library), the amplifier
kept awake, the volume. One mixed stream because a process per note crackles when four sound at once."""
import math
import subprocess
import threading
import time
import wave
from array import array
from ctypes import CDLL, PyDLL, POINTER, byref, c_char_p, c_double, c_int, c_long, c_size_t, c_uint, c_ulong, c_void_p
from collections import OrderedDict
from functools import cache
from itertools import zip_longest
from pathlib import Path

DEVICE, MIXER = 'default', ('--', 'sset', 'PCM')  # (the amplifier in the case, through /etc/asound.conf's mixing and its
                                                  # software PCM control, the amp has none; "--" lets a negative dB value through)
LEVELS = (-36, -24, -15, -8, 0)  # the volume steps, dB of that PCM control (0 dB is full scale)
AWAKE = 12 * 3600  # seconds the amplifier stays awake after the last clip (see Amp)
PCM_RATE, PCM_TARGET_FRAMES = 44100, 256
STOP_FRAMES = round(.006 * PCM_RATE)
LIMIT_HOLD = round(.015 * PCM_RATE)
LIMIT_RECOVERY = 1 / (.1 * PCM_RATE)


@cache
def alsa():
    lib = CDLL('libasound.so.2')
    for name, result, args in (
        ('snd_pcm_open', c_int, (POINTER(c_void_p), c_char_p, c_int, c_int)),
        ('snd_pcm_format_value', c_int, (c_char_p,)),
        ('snd_pcm_set_params', c_int, (c_void_p, c_int, c_int, c_uint, c_uint, c_int, c_uint)),
        ('snd_pcm_get_params', c_int, (c_void_p, POINTER(c_ulong), POINTER(c_ulong))),
        ('snd_pcm_wait', c_int, (c_void_p, c_int)),
        ('snd_pcm_drop', c_int, (c_void_p,)),
        ('snd_pcm_prepare', c_int, (c_void_p,)),
        ('snd_pcm_writei', c_long, (c_void_p, c_char_p, c_ulong)),
        ('snd_pcm_recover', c_int, (c_void_p, c_int, c_int)),
        ('snd_pcm_close', c_int, (c_void_p,)),
        ('snd_strerror', c_char_p, (c_int,)),
    ):
        call = getattr(lib, name)
        call.restype, call.argtypes = result, args
    return lib


def check(result):
    if result < 0:
        raise OSError(-result, alsa().snd_strerror(result).decode())


def open_pcm(device):
    pcm = c_void_p()
    check(alsa().snd_pcm_open(byref(pcm), device, 0, 0))  # blocking playback
    return pcm


def hold_amp_clock():
    # Opening dmix starts the free-running I2S clock. The hardware supplies its own silence.
    mixer = open_pcm(b'amp_mix')
    return lambda: alsa().snd_pcm_close(mixer)


class Amp:
    """Leave the case amplifier clocked between clips so its wake-up click occurs just once."""
    def __init__(self, awake=AWAKE, start=hold_amp_clock):
        self.awake, self.start = awake, start
        self.close = None
        self.retry = 0.
        self.lock = threading.Lock()

    def wake(self):
        with self.lock:
            now = time.monotonic()
            self.deadline = now + self.awake
            if self.close is None and now >= self.retry:
                try:
                    self.close = self.start()
                except OSError:
                    # Boards with another audio output have no amp_mix to hold open.
                    self.retry = now + 60
                    return
                self.schedule(self.awake)

    def schedule(self, remaining):
        timer = threading.Timer(remaining, self.expire)
        timer.daemon = True
        timer.start()

    def expire(self):
        with self.lock:
            remaining = self.deadline - time.monotonic()
            if remaining > 0:
                self.schedule(remaining)
            else:
                self.close()
                self.close = None


AMP = Amp()


class ALSAOutput:
    """Write the mixed blocks directly to the case's existing ALSA route."""

    RW_INTERLEAVED = 3

    def __enter__(self):
        self.alsa = lib = alsa()
        self.pcm = open_pcm(DEVICE.encode())
        try:
            # Four periods leave one extra write deadline for a busy Pi 3A+; taps are still mixed every period.
            buffer_us = round(4 * PCM_TARGET_FRAMES * 1_000_000 / PCM_RATE)
            check(lib.snd_pcm_set_params(self.pcm, lib.snd_pcm_format_value(b'S16_LE'), self.RW_INTERLEAVED,
                                         2, PCM_RATE, 0, buffer_us))
            buffer, period = c_ulong(), c_ulong()
            check(lib.snd_pcm_get_params(self.pcm, byref(buffer), byref(period)))
            self.frames, self.buffer_frames = period.value, buffer.value
        except OSError:
            lib.snd_pcm_close(self.pcm)
            raise
        return self

    def __exit__(self, *args):
        self.alsa.snd_pcm_close(self.pcm)

    def park(self):
        # Everything audible has reached the device. Keep the configured route ready with no periodic writes.
        check(self.alsa.snd_pcm_drop(self.pcm))
        check(self.alsa.snd_pcm_prepare(self.pcm))

    def wait(self):
        ready = self.alsa.snd_pcm_wait(self.pcm, 1000)
        if ready < 0:
            check(self.alsa.snd_pcm_recover(self.pcm, ready, 0))
        elif not ready:
            raise TimeoutError('ALSA playback did not become ready')

    def write(self, data):
        while data:
            frames = self.alsa.snd_pcm_writei(self.pcm, data, len(data) // 4)
            if frames < 0:
                check(self.alsa.snd_pcm_recover(self.pcm, frames, 0))
                continue
            if not frames:
                raise OSError('ALSA playback accepted no frames')
            data = data[frames * 4:]


# Installed on the Pi. Running directly from a checkout uses the same envelope in Python.
try:
    # These short arithmetic calls never block. Releasing the GIL per voice can stall a whole audio period.
    pcm = PyDLL(str(Path(__file__).with_name('pcm.so')))
    pcm.add_voice.argtypes = (c_void_p, c_void_p, c_uint, c_double, c_uint, c_uint)
    pcm.add_voice.restype = None
    pcm.limit_pcm.argtypes = (c_void_p, c_void_p, c_uint, POINTER(c_double), POINTER(c_uint), c_uint, c_double, c_double)
    pcm.limit_pcm.restype = None
    # Scanning a newly loaded sample can take longer than one audio period. Let the writer run.
    native_peaks = CDLL(pcm._name).remaining_peaks
    native_peaks.argtypes = (c_void_p, c_size_t, c_uint, c_void_p)
    native_peaks.restype = None
except (OSError, AttributeError):
    pcm = None


def limit_samples(sums, frames, gain, hold, target):
    """The checkout fallback for the native stereo limiter."""
    sums.extend([0] * (2 * frames - len(sums)))
    level, left = gain.value, hold.value
    if level == target == 1. and not left and min(sums) >= -32767 and max(sums) <= 32767:
        return array('h', sums)
    start, ceiling = level, target
    slope = min(0., (target - start) / frames)
    for i in range(0, len(sums), 2):
        peak = max(abs(sums[i]), abs(sums[i+1]))
        if peak > 32767:
            safe = 32767 / peak
            ceiling = min(ceiling, safe)
            slope = min(slope, (safe - start) / (i // 2 + 1))
    samples = array('h')
    for i in range(0, len(sums), 2):
        if slope < 0.:
            level = max(ceiling, start + (i // 2 + 1) * slope)
        elif left:
            left -= 1
        else:
            level = min(ceiling, level + LIMIT_RECOVERY)
        samples.extend((int(sums[i] * level), int(sums[i+1] * level)))
    gain.value, hold.value = level, LIMIT_HOLD if ceiling < target else left
    return samples


PEAK_STRIDE = 2 * round(.05 * PCM_RATE)  # interleaved stereo samples in 50 ms


def remaining_peaks(samples):
    peaks = array('H', [0]) * max(1, (len(samples) + PEAK_STRIDE - 1) // PEAK_STRIDE)
    if pcm:
        native_peaks(samples.buffer_info()[0], len(samples), PEAK_STRIDE, peaks.buffer_info()[0])
    else:
        view = memoryview(samples)
        peak = 0
        for i in reversed(range(len(peaks))):
            start = i * PEAK_STRIDE
            peak = max(peak, max(map(abs, view[start:start + PEAK_STRIDE]), default=0))
            peaks[i] = peak
    return peaks


class PCMVoice:
    def __init__(self, samples, gain, peaks=None):
        self.samples, self.gain = samples, gain
        self.peaks = peaks if peaks is not None else remaining_peaks(samples)
        self.offset = 0
        self.done = False
        self.stopped = False
        self.fade = None

    def remaining_peak(self):
        level = abs(self.gain)
        if self.fade:
            end, length = self.fade
            level *= ((end - self.offset) / (2 * length)) ** 3
        stretch = self.offset // PEAK_STRIDE  # (read once: the audio thread moves the offset on)
        return self.peaks[stretch] * level if stretch < len(self.peaks) else 0

    def poll(self):
        return 0 if self.stopped or self.done else None

    def terminate(self):
        self.stopped = True
        if not self.offset:
            self.done = True

    def release(self, seconds):
        if self.poll() is None and self.fade is None:
            length = max(1, round(seconds * PCM_RATE))
            self.fade = (self.offset + length * 2, length)

    def advance(self, frames):
        start = self.offset
        if self.done:
            return start, start, None
        fade = self.fade
        if self.stopped:
            length = min(STOP_FRAMES, (len(self.samples) - start) // 2)
            if length and (fade is None or fade[0] - start > 2 * length):
                # A stolen voice can already be fading. Start its short tail at the level just reached.
                if fade:
                    self.gain *= ((fade[0] - start) / (2 * fade[1])) ** 3
                self.fade = fade = (start + length * 2, length)
        # A release works from the position it last saw: its fade may already lie behind this voice, which then ends.
        limit = max(start, min(len(self.samples), fade[0])) if fade else len(self.samples)
        end = min(start + frames * 2, limit)
        self.offset = end
        if end >= limit:
            self.done = True
        return start, end, fade

    def mix_into(self, frames, total):
        start, end, fade = self.advance(frames)
        fade_end, length = fade or (start, 0)
        address = self.samples.buffer_info()[0] + start * self.samples.itemsize
        pcm.add_voice(total, address, (end - start) // 2, self.gain, (fade_end - start) // 2, length)

    def read(self, frames):
        start, end, fade = self.advance(frames)
        chunk = self.samples[start:end]
        if fade:
            fade_end, length = fade
            remaining = (fade_end - start) // 2
            for i in range(0, len(chunk), 2):
                gain = self.gain * ((remaining - i // 2) / length) ** 3
                chunk[i] = round(chunk[i] * gain)
                chunk[i+1] = round(chunk[i+1] * gain)
            return chunk
        return chunk if self.gain == 1 else (round(sample * self.gain) for sample in chunk)


def pcm_samples(clip):
    try:
        with wave.open(str(clip), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (2, 2, PCM_RATE):
                return array('h', wav.readframes(wav.getnframes()))
    except (EOFError, wave.Error):
        pass  # aplay still handles clips in other WAV formats
    return None


CACHE_BYTES = 48 * 1024 * 1024  # Fits the whole piano bank on the Pi.


class PCMPlayer:
    """Combine overlapping clips before sending them to ALSA."""

    def __init__(self, start=ALSAOutput):
        self.start = start
        self.lock = threading.Condition()
        self.bank_open = False
        self.voices = []
        self.running = False
        self.cache = OrderedDict()
        self.cached_bytes = 0
        self.next_bank = None
        self.cache_lock = threading.Lock()

    def samples(self, clip):
        stamp = clip.stat()
        key = (clip, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns)
        with self.cache_lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
        # A first strike must never wait on the cache lock while another WAV loads.
        samples = pcm_samples(clip)
        if samples is None:
            return None
        peaks = remaining_peaks(samples)
        size = len(samples) * samples.itemsize + len(peaks) * peaks.itemsize
        if size <= CACHE_BYTES:
            with self.cache_lock:
                if key not in self.cache:
                    self.cache[key] = samples, peaks
                    self.cached_bytes += size
                    while self.cached_bytes > CACHE_BYTES or len(self.cache) > 256:
                        _, oldest = self.cache.popitem(last=False)
                        self.cached_bytes -= sum(len(a) * a.itemsize for a in oldest)
        return samples, peaks

    def prepare(self, clips):
        paths = tuple(dict.fromkeys(Path(clip) for clip in clips))
        with self.lock:
            self.bank_open = bool(paths)
            if self.bank_open:
                self.start_writer()
            else:
                self.lock.notify()
        with self.cache_lock:
            running = self.next_bank is not None
            if not paths and not running:
                return
            self.next_bank = paths
        if not running:
            threading.Thread(target=self.prepare_banks, name='prepare-bank', daemon=True).start()

    def prepare_banks(self):
        while True:
            paths = self.next_bank
            remaining = CACHE_BYTES
            for path in paths:
                if paths is not self.next_bank:
                    break
                try:
                    size = path.stat().st_size
                    if size <= remaining:
                        self.samples(path)
                        remaining -= size
                except (OSError, ValueError, OverflowError, EOFError, wave.Error):
                    continue  # An upload or first install can change a bank while it loads.
            with self.cache_lock:
                if paths is self.next_bank:
                    self.next_bank = None
                    return

    def play(self, clip, gain=1.):
        decoded = self.samples(Path(clip))
        if decoded is None:
            return None
        samples, peaks = decoded
        voice = PCMVoice(samples, gain, peaks)
        with self.lock:
            self.voices.append(voice)
            self.start_writer()
        return voice

    def start_writer(self):
        if self.running:
            self.lock.notify()
        else:
            self.running = True
            threading.Thread(target=self.run, name='audio', daemon=True).start()

    def run(self):
        previous, target = [], 1.
        gain, hold = c_double(1.), c_uint(0)
        try:
            with self.start() as output:
                # Once a buffer of silence follows the last note it is safe to park or close.
                buffer_frames = getattr(output, 'buffer_frames', output.frames * 4)
                idle = buffer_frames
                silence = bytes(output.frames * 4)
                if pcm:
                    sums = array('q', [0]) * (output.frames * 2)
                    samples = array('h', [0]) * len(sums)
                    total, result = sums.buffer_info()[0], samples.buffer_info()[0]
                while True:
                    if idle >= buffer_frames:
                        with self.lock:
                            self.lock.wait_for(lambda: self.voices or not self.bank_open)
                            if not self.voices:
                                self.running = False
                                return
                    output.wait()
                    with self.lock:
                        self.voices = [voice for voice in self.voices if not voice.done]
                        voices = self.voices.copy()
                    if voices != previous:
                        # Set the chord level once. A later strike shares room with what is still ringing.
                        peak = math.sqrt(sum(voice.remaining_peak() ** 2 for voice in voices))
                        target = 32767 / peak if peak > 32767 else 1.
                        if idle:
                            gain.value = target
                        previous = voices
                    idle = 0 if voices else idle + output.frames
                    block = silence
                    if not voices:
                        gain.value, hold.value = 1., 0
                    elif pcm:
                        for voice in voices:
                            voice.mix_into(output.frames, total)
                        pcm.limit_pcm(total, result, len(samples), byref(gain), byref(hold), LIMIT_HOLD, LIMIT_RECOVERY, target)
                        block = samples.tobytes()
                    else:
                        chunks = [voice.read(output.frames) for voice in voices]
                        sums = list(map(sum, zip_longest(*chunks, fillvalue=0)))
                        samples = limit_samples(sums, output.frames, gain, hold, target)
                        block = samples.tobytes()
                    output.write(block)
                    if idle >= buffer_frames:
                        with self.lock:
                            if not self.bank_open and not self.voices:
                                self.running = False
                                return
                        output.park()
        except OSError as error:
            print(f'Soundboard playback: {error}', flush=True)
            with self.lock:
                for voice in self.voices:
                    voice.terminate()
                self.voices.clear()
                self.running = False


PCM = PCMPlayer()


def wake_amp():
    AMP.wake()


def aplay(clip, gain=1.):
    wake_amp()
    voice = PCM.play(clip, gain)
    if voice is not None:
        return voice
    command = ['aplay', '-q', '-D', DEVICE, str(clip)]
    if gain != 1:
        # A plugin can supply another WAV format; preserve its requested level on the fallback route.
        command = ['ffmpeg', '-nostdin', '-loglevel', 'error', '-i', str(clip), '-af', f'volume={gain}', '-f', 'alsa', DEVICE]
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def amixer(db):
    subprocess.Popen(['amixer', '-q', *MIXER, f'{db}dB'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
