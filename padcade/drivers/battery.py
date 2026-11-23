"""PiSugar 3 status and hardware charge protection, independent of the pad/audio event loop."""
import fcntl
import subprocess
import time
from contextlib import contextmanager
from ctypes import Structure, addressof, c_uint8, c_uint32, c_void_p
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BatterySample:
    at: float
    percent: int
    plugged: bool


class SMBusRequest(Structure):
    # Linux i2c-dev ABI; ctypes keeps the pointer correctly aligned on the 32-bit Pi.
    _fields_ = [('read_write', c_uint8), ('command', c_uint8), ('size', c_uint32), ('data', c_void_p)]


def _byte(bus, register, value=None):
    data = (c_uint8 * 34)()
    data[0] = 0 if value is None else value
    request = SMBusRequest(1 if value is None else 0, register, 2, addressof(data))
    fcntl.ioctl(bus.fileno(), 0x0720, request)
    return data[0]


@contextmanager
def _pisugar():
    with open('/dev/i2c-1', 'r+b', buffering=0) as bus:
        fcntl.ioctl(bus.fileno(), 0x0703, 0x57)
        # PiSugar's driver checks the hardware version and application mode; other accessories share this address.
        if _byte(bus, 0x00) != 3 or _byte(bus, 0x01) != 0x0f:
            raise OSError('No PiSugar 3 in application mode')
        yield bus


def _lock_writes(bus):
    _byte(bus, 0x0b, 0)
    if _byte(bus, 0x0b):
        raise OSError('PiSugar write protection did not relock')


def enable_charge_limit():
    """Keep PiSugar's approximately 80% hardware charge protection enabled."""
    with _pisugar() as bus:
        control = _byte(bus, 0x20)
        if control & 0x80:
            if _byte(bus, 0x0b):
                _lock_writes(bus)
            return
        try:
            _byte(bus, 0x0b, 0x29)
            _byte(bus, 0x20, control | 0x80)
        finally:
            _lock_writes(bus)
        if not _byte(bus, 0x20) & 0x80:
            raise OSError('PiSugar charge protection did not enable')


MISSES = 3  # failed readings in a row (2 s apart) before the battery counts as unknown
PROTECT_EVERY = 30  # readings between checks of the charge limit (a minute: it only resets when the PiSugar loses power)
LOW = 8  # percent on battery at which the Pi shuts down cleanly (charge left so PiSugar can restart it at once)


def power_off_later(seconds=120):
    """Schedule a power cut with charger wake enabled, or cancel the countdown with None."""
    with _pisugar() as bus:
        try:
            _byte(bus, 0x0b, 0x29)
            control = _byte(bus, 0x02)
            if seconds is None:
                _byte(bus, 0x02, control | 0x20)  # Keep the delayed output switch on.
            else:
                _byte(bus, 0x09, seconds)
                _byte(bus, 0x02, control & ~0x20 | 0x10)  # Start countdown and enable charger wake.
        finally:
            _lock_writes(bus)


def shut_down():
    # systemd may stop this process as soon as it accepts the shutdown.
    try:
        power_off_later()
    except OSError as error:
        print(f'PiSugar power cut unavailable: {error}', flush=True)
    try:
        subprocess.run(['sudo', '-n', 'systemctl', 'poweroff'], check=True)
    except (OSError, subprocess.CalledProcessError):
        power_off_later(None)
        raise


def sample_battery():
    with _pisugar() as bus:
        percent, plugged = _byte(bus, 0x2a), bool(_byte(bus, 0x02) & 0x80)
    if not 0 <= percent <= 100:
        raise ValueError(f'invalid PiSugar percentage {percent}')
    return BatterySample(time.monotonic(), percent, plugged)


def read_battery(board, stop, read=sample_battery, protect=enable_charge_limit, shutdown=shut_down):
    failure = protection_failure = None
    low = 0  # (readings in a row on battery at LOW or under: two, so one odd gauge reading doesn't shut it down)
    missed = turn = 0  # (failed readings in a row: the bus to the PiSugar drops one now and then)
    while not stop.is_set():
        if turn % PROTECT_EVERY == 0 or protection_failure:
            try:
                protect()  # PiSugar resets this bit after a power cycle.
                protection_failure = None
            except (OSError, ValueError) as error:
                if repr(error) != protection_failure:
                    protection_failure = repr(error)
                    print(f'PiSugar charge limit unavailable: {protection_failure}', flush=True)
        turn += 1
        try:
            sample = read()
            if board.battery_sample is None:
                print(f'PiSugar: {sample.percent}% battery, external power {sample.plugged}', flush=True)
            board.battery_sample, failure, missed = sample, None, 0
            low = low + 1 if not sample.plugged and sample.percent <= LOW else 0
            if low >= 2:
                try:
                    shutdown()
                    break
                except (OSError, subprocess.CalledProcessError) as error:
                    print(f'PiSugar: shutdown failed, will retry while on low battery: {error}', flush=True)
        except (OSError, ValueError) as error:
            missed += 1
            if missed >= MISSES:  # (until then the last reading stays on the pads and in Home Assistant)
                board.battery_sample = None
                if repr(error) != failure:
                    failure = repr(error)
                    print(f'PiSugar unavailable: {failure}', flush=True)
        stop.wait(2)
    board.battery_sample = None
