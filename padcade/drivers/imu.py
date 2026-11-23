"""Latest calibrated readings from the BNO085 inside the Launchpad."""
import fcntl
import os
import select
import struct
import termios
import tty
from collections import Counter
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

Vector = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class MotionSample:
    """Sensor axes, with quaternions ordered x, y, z, w."""
    at: float  # monotonic time at receipt
    acceleration: Vector  # m/s², including gravity
    gyroscope: Vector  # rad/s
    magnetic_field: Vector  # µT
    orientation: Quaternion  # magnetometer-corrected heading
    game_orientation: Quaternion  # gravity-referenced tilt, relative heading


class Pickup:
    """A deliberate lift after resting, rather than a brief knock or sensor yaw drift."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.previous = self.rest = self.still_since = self.moving_since = None

    def update(self, sample):
        if sample is None:
            self.reset()
            return False
        if self.previous is not None:
            if sample.at <= self.previous:
                return False
            if sample.at - self.previous > .25:
                self.reset()
        self.previous = sample.at
        length = math.sqrt(sum(v * v for v in sample.game_orientation))
        x, y, z, w = (v / length for v in sample.game_orientation)
        gravity = (2 * (w*y - x*z), -2 * (w*x + y*z), 2 * (x*x + y*y) - 1)
        acceleration = abs(math.sqrt(sum(v * v for v in sample.acceleration)) - 9.81)
        if self.rest is None:
            if acceleration > .5 or sum(v * v for v in sample.gyroscope) > .15**2:
                self.still_since = None
            elif self.still_since is None:
                self.still_since = sample.at
            elif sample.at - self.still_since >= .5:
                self.rest = gravity
            return False
        moved = acceleration > 1.2 or sum(a * b for a, b in zip(gravity, self.rest)) < math.cos(math.radians(10))
        if not moved:
            self.moving_since = None
        elif self.moving_since is None:
            self.moving_since = sample.at
        elif sample.at - self.moving_since >= .15:
            self.rest = self.still_since = self.moving_since = None
            return True
        return False


class Sampler:
    """Publish a complete set once each of its reports has advanced."""

    def __init__(self, sensor):
        self.sensor = sensor
        self.previous = self.readings()

    def readings(self):
        sensor = self.sensor
        return (sensor.acceleration, sensor.gyro, sensor.magnetic, sensor.quaternion, sensor.game_quaternion)

    def read(self):
        values = self.readings()
        now = time.monotonic()
        if any(now - at > .25 for at in getattr(self.sensor, 'updated', {}).values()):
            return None
        # The sensor reader replaces the tuple on a new report, even when its numbers are unchanged.
        if any(value is previous for value, previous in zip(values, self.previous)):
            return None
        if any(value is None or len(value) != length or not all(map(math.isfinite, value))
               for value, length in zip(values, (3, 3, 3, 4, 4))):
            return None
        if any(not .75 <= sum(component * component for component in q) <= 1.25 for q in values[3:]):
            return None
        self.previous = values
        return MotionSample(now, *values)


# SH-2 report ID -> attribute, record bytes, components, fixed-point exponent.
REPORTS = {1: ('acceleration', 10, 3, 8), 2: ('gyro', 10, 3, 9),
           3: ('magnetic', 10, 3, 4), 5: ('quaternion', 14, 4, 14),
           8: ('game_quaternion', 12, 4, 14)}
METADATA = {0xfa: 5, 0xfb: 5, 0xef: 2}


class UARTSensor:
    """Bounded SHTP framing on the BNO085's two-wire UART, away from the MIDI loop."""
    def __init__(self, fd):
        self.fd = fd
        self.buffer = bytearray()
        self.synced = self.escaped = self.ready = False
        self.sequence, self.received = Counter(), {}
        self.updated = {}
        self.counts, self.errors = Counter(), Counter()
        self.product = None
        for name, *_ in REPORTS.values():
            setattr(self, name, None)

    def poll(self, wait=.025):
        if not select.select([self.fd], [], [], wait)[0]:
            return 0
        raw = os.read(self.fd, 4096)
        if not raw:
            raise OSError('BNO085 UART disconnected')
        budget = 0
        for byte in raw:
            if byte == 0x7e:
                if self.synced and self.buffer and not self.escaped:
                    budget = max(budget, self.process(bytes(self.buffer)))
                self.buffer.clear()
                self.synced, self.escaped = True, False
            elif self.synced:
                if self.escaped:
                    if byte not in (0x5d, 0x5e):
                        self.synced = False
                        self.errors['escape'] += 1
                    self.buffer.append(byte ^ 0x20)
                    self.escaped = False
                elif byte == 0x7d:
                    self.escaped = True
                else:
                    self.buffer.append(byte)
                if len(self.buffer) > 4096:
                    self.buffer.clear()
                    self.synced = False
                    self.errors['oversize'] += 1
        return budget

    def process(self, frame):
        if frame[0] == 0 and len(frame) == 3:
            return int.from_bytes(frame[1:], 'little')
        if frame[0] != 1 or len(frame) < 6:
            self.errors['framing'] += 1
            return 0
        length, channel, _ = struct.unpack_from('<HBB', frame, 1)
        # These five small reports fit one packet; fragmented messages are not input samples.
        if length != len(frame) - 1 or channel > 5:
            self.errors['header'] += 1
            return 0
        data = frame[5:]
        if channel == 1 and data == b'\x01' and self.ready:
            raise OSError('BNO085 restarted')
        if channel == 2 and data[0] == 0xf8 and len(data) >= 12:
            self.product = int.from_bytes(data[4:8], 'little')
        if channel not in (3, 4):
            return 0
        offset, updates = 0, []
        while offset < len(data):
            report = data[offset]
            size = METADATA.get(report, REPORTS.get(report, (None, 0))[1])
            if not size or offset + size > len(data):
                self.errors['report'] += 1
                return 0
            if report in REPORTS:
                name, _, axes, q = REPORTS[report]
                values = tuple(v / (1 << q) for v in struct.unpack_from(f'<{axes}h', data, offset + 4))
                updates.append((report, data[offset + 1], name, values))
            offset += size
        for report, sequence, name, values in updates:
            if self.received.get(report) == sequence:
                continue
            self.received[report] = sequence
            self.updated[report] = time.monotonic()
            setattr(self, name, values)
            self.counts[name] += 1
        return 0

    def write(self, data):
        framed = b'\x7e' + data.replace(b'\x7d', b'\x7d\x5d').replace(b'\x7e', b'\x7d\x5e') + b'\x7e'
        # The sensor requires spacing between host bytes even at 3 Mbaud.
        for byte in framed:
            if not select.select([], [self.fd], [], .3)[1]:
                raise TimeoutError('BNO085 UART blocked')
            os.write(self.fd, bytes((byte,)))
            termios.tcdrain(self.fd)
            time.sleep(.00025)

    def send(self, channel, payload):
        packet = struct.pack('<HBB', 4 + len(payload), channel, self.sequence[channel]) + payload
        for _ in range(3):
            self.write(b'\0')  # Ask how many SHTP bytes the sensor can accept.
            deadline = time.monotonic() + .3
            while time.monotonic() < deadline:
                if self.poll(.03) >= len(packet):
                    self.write(b'\x01' + packet)
                    self.sequence[channel] = (self.sequence[channel] + 1) % 256
                    return
        raise TimeoutError('BNO085 gave no UART write allowance')

    def configure(self, report, period):
        self.send(2, struct.pack('<BBBHIII', 0xfd, report, 0, 0, period, 0, 0))

    def collect(self, seconds):
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            self.poll(min(.025, remaining))


@contextmanager
def open_sensor():
    fd = os.open('/dev/serial0', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    sensor, enabled = None, []
    try:
        fcntl.ioctl(fd, termios.TIOCEXCL)
        tty.setraw(fd)
        state = termios.tcgetattr(fd)
        state[2] = (state[2] | termios.CLOCAL | termios.CREAD) & ~termios.CRTSCTS
        state[4] = state[5] = termios.B3000000
        termios.tcsetattr(fd, termios.TCSANOW, state)
        sensor = UARTSensor(fd)
        sensor.collect(.2)
        sensor.send(1, b'\x01')
        sensor.sequence.clear()
        sensor.collect(1)
        sensor.send(2, b'\xf9\0')
        sensor.collect(.5)
        if sensor.product is None:
            raise OSError('BNO085 product response missing')
        for report in REPORTS:
            sensor.configure(report, 50_000)
            enabled.append(report)
        sensor.ready = True
        yield sensor
    finally:
        if sensor is not None:
            sensor.ready = False
            for report in enabled:
                try:
                    sensor.configure(report, 0)
                except OSError:
                    break
        os.close(fd)


def read_motion(board, stop, connect=open_sensor):
    """Keep UART calls off the pad event loop and clear input when the sensor drops out."""
    failure = None
    while not stop.is_set():
        try:
            with connect() as sensor:
                reader = Sampler(sensor)
                last = time.monotonic()
                while not stop.is_set():
                    # With dark pads, collect a whole 20 Hz report cycle in one UART read.
                    if not board.power and stop.wait(.05):
                        break
                    sensor.poll(.1 if board.power else 0.)
                    if (sample := reader.read()) is not None:
                        if board.motion_sample is None:
                            print('BNO085 motion reports ready', flush=True)
                        board.motion_sample = sample
                        last, failure = sample.at, None
                    elif time.monotonic() - last > 2:
                        raise TimeoutError('no fresh BNO085 reports')
        except Exception as error:  # Driver and device failures must stay outside the pad loop.
            if repr(error) != failure:
                failure = repr(error)
                print(f'BNO085: {failure}', flush=True)
        finally:
            board.motion_sample = None
        stop.wait(5)


@contextmanager
def motion_reader(board):
    stop = threading.Event()
    worker = threading.Thread(target=read_motion, args=(board, stop), name='bno085', daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(2)
