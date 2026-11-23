import ctypes
import io
import subprocess
import threading
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import mock_open, patch

from padcade.drivers.battery import BatterySample, enable_charge_limit, power_off_later, read_battery, sample_battery, shut_down


class PiSugarInput(unittest.TestCase):
    def hardware(self, percent=60, flags=0x80, version=3, mode=15):
        def ioctl(fd, command, data):
            if command == 0x0703:
                self.assertEqual(data, 0x57)
            else:
                self.assertEqual(command, 0x0720)
                self.assertEqual(data.read_write, 1)  # Read-only SMBus byte-data transaction.
                self.assertEqual(data.size, 2)
                value = {0x00: version, 0x01: mode, 0x2a: percent, 0x02: flags}[data.command]
                ctypes.cast(data.data, ctypes.POINTER(ctypes.c_ubyte))[0] = value
            return 0
        return ioctl

    def test_another_i2c_device_cannot_supply_a_battery_level_or_receive_writes(self):
        for version, mode in ((1, 15), (3, 0)):
            with self.subTest(version=version, mode=mode), patch('builtins.open', mock_open()), \
                 patch('padcade.drivers.battery.fcntl.ioctl', self.hardware(0, 0, version, mode)):
                for operation in (sample_battery, enable_charge_limit, power_off_later):
                    with self.assertRaises(OSError):
                        operation()

    def test_reads_percentage_and_external_power_without_writing_settings(self):
        with patch('builtins.open', mock_open()), patch('padcade.drivers.battery.fcntl.ioctl', self.hardware()), \
             patch('padcade.drivers.battery.time.monotonic', return_value=12):
            sample = sample_battery()
        self.assertEqual(sample.percent, 60)
        self.assertTrue(sample.plugged)
        self.assertEqual(sample.at, 12)

    def test_rejects_bad_gauge_values_and_distinguishes_zero_from_unknown(self):
        with patch('builtins.open', mock_open()), patch('padcade.drivers.battery.fcntl.ioctl', self.hardware(255)):
            with self.assertRaises(ValueError):
                sample_battery()
        with patch('builtins.open', mock_open()), patch('padcade.drivers.battery.fcntl.ioctl', self.hardware(0, 0)):
            sample = sample_battery()
        self.assertEqual(sample.percent, 0)
        self.assertFalse(sample.plugged)

    def test_a_failed_read_keeps_the_last_reading_and_the_next_recovers_without_busy_polling(self):
        board = SimpleNamespace(battery_sample=None)
        stop, observed = threading.Event(), []
        readings = iter((BatterySample(1, 77, False), OSError('bus unavailable'), BatterySample(2, 20, True)))

        def read():
            result = next(readings)
            if isinstance(result, Exception):
                raise result
            return result

        def wait(seconds):
            self.assertGreater(seconds, 0)
            self.assertLessEqual(seconds, 2)  # Cable changes should appear promptly.
            observed.append(board.battery_sample)
            if len(observed) == 3:
                stop.set()

        with patch.object(stop, 'wait', side_effect=wait), redirect_stdout(io.StringIO()):
            read_battery(board, stop, read, protect=lambda: None)
        self.assertEqual([s.percent if s else None for s in observed], [77, 77, 20])
        self.assertIsNone(board.battery_sample)

    def test_protection_errors_do_not_block_status_and_are_retried_without_repeated_logs(self):
        board = SimpleNamespace(battery_sample=None)
        stop, observed, attempts = threading.Event(), [], []

        def protect():
            attempts.append(None)
            if len(attempts) in (1, 2):
                raise OSError('protection failed')

        def wait(seconds):
            observed.append(board.battery_sample.percent)
            if len(observed) == 5:
                stop.set()

        output = io.StringIO()
        with patch.object(stop, 'wait', side_effect=wait), redirect_stdout(output):
            read_battery(board, stop, lambda: BatterySample(1, 77, False), protect)
        self.assertEqual(observed, [77] * 5)
        self.assertEqual(len(attempts), 3)  # (asked again right away while it fails, then left alone for a while)
        self.assertEqual(output.getvalue().count('PiSugar charge limit unavailable'), 1)


class PiSugarChargeProtection(unittest.TestCase):
    def setUp(self):
        self.control = 0x53
        self.writes = []
        self.fail_write = None
        self.ignore_write = False
        self.fail_read = None
        self.reads = 0
        self.write_enabled = False
        self.ignore_relock = False

    def ioctl(self, fd, command, request):
        if command == 0x0703:
            self.assertEqual(request, 0x57)
            return 0
        self.assertEqual(command, 0x0720)
        self.assertEqual(request.size, 2)
        data = ctypes.cast(request.data, ctypes.POINTER(ctypes.c_ubyte))
        if request.read_write == 1:
            if request.command in (0, 1):
                data[0] = (3, 15)[request.command]
                return 0
            self.reads += 1
            if self.reads == self.fail_read:
                raise OSError('read failed')
            data[0] = {0x20: self.control, 0x0b: int(self.write_enabled)}[request.command]
        else:
            self.assertEqual(request.read_write, 0)
            write = (request.command, data[0])
            self.writes.append(write)
            if write == self.fail_write:
                raise OSError('write failed')
            if request.command == 0x20 and not self.ignore_write:
                self.assertTrue(self.write_enabled)
                self.control = data[0]
            elif request.command == 0x0b and not (data[0] == 0 and self.ignore_relock):
                self.write_enabled = data[0] == 0x29
        return 0

    def test_enables_hardware_limit_preserving_other_bits_and_skips_redundant_writes(self):
        with patch('builtins.open', mock_open()), patch('padcade.drivers.battery.fcntl.ioctl', self.ioctl):
            enable_charge_limit()
            enable_charge_limit()
        self.assertEqual(self.control, 0xd3)
        self.assertEqual(self.writes, [(0x0b, 0x29), (0x20, 0xd3), (0x0b, 0)])
        self.assertFalse(self.write_enabled)

    def test_failed_relock_is_retried_even_when_charge_protection_already_enabled(self):
        self.fail_write = (0x0b, 0)
        with patch('builtins.open', mock_open()), patch('padcade.drivers.battery.fcntl.ioctl', self.ioctl):
            with self.assertRaises(OSError):
                enable_charge_limit()
            self.assertTrue(self.write_enabled)
            self.fail_write = None
            enable_charge_limit()
        self.assertFalse(self.write_enabled)
        self.assertEqual(self.writes.count((0x20, 0xd3)), 1)

    def test_ignored_relock_cannot_be_reported_as_success(self):
        self.ignore_relock = True
        with patch('builtins.open', mock_open()), patch('padcade.drivers.battery.fcntl.ioctl', self.ioctl):
            with self.assertRaisesRegex(OSError, 'write protection'):
                enable_charge_limit()

    def test_write_failures_still_attempt_relocking(self):
        for failed in ((0x0b, 0x29), (0x20, 0xd3), (0x0b, 0)):
            with self.subTest(failed=failed):
                self.setUp()
                self.fail_write = failed
                with patch('builtins.open', mock_open()), patch('padcade.drivers.battery.fcntl.ioctl', self.ioctl):
                    with self.assertRaises(OSError):
                        enable_charge_limit()
                self.assertEqual(self.writes[-1], (0x0b, 0))

    def test_ignored_write_is_reported_after_relocking(self):
        self.ignore_write = True
        with patch('builtins.open', mock_open()), patch('padcade.drivers.battery.fcntl.ioctl', self.ioctl):
            with self.assertRaisesRegex(OSError, 'charge protection'):
                enable_charge_limit()
        self.assertEqual(self.writes[-1], (0x0b, 0))

    def test_failed_initial_read_never_writes_and_failed_readback_never_claims_success(self):
        for failed in (1, 2, 3):
            with self.subTest(failed=failed):
                self.setUp()
                self.fail_read = failed
                with patch('builtins.open', mock_open()), patch('padcade.drivers.battery.fcntl.ioctl', self.ioctl):
                    with self.assertRaises(OSError):
                        enable_charge_limit()
                if failed == 1:
                    self.assertEqual(self.writes, [])
                else:
                    self.assertEqual(self.writes[-1], (0x0b, 0))


class LowBatteryShutdown(unittest.TestCase):
    def run_samples(self, samples):
        board, stop, shutdowns, readings = SimpleNamespace(battery_sample=None), threading.Event(), [], list(samples)

        def read():
            sample = readings.pop(0)
            if not readings:
                stop.set()
            return sample
        with patch.object(stop, 'wait'), redirect_stdout(io.StringIO()):
            read_battery(board, stop, read, protect=lambda: None,
                         shutdown=lambda: shutdowns.append(True))
        return len(shutdowns)

    def test_shuts_down_once_after_two_low_readings_on_battery(self):
        self.assertEqual(self.run_samples([BatterySample(t, 7, False) for t in range(5)]), 1)

    def test_a_failed_shutdown_is_retried_while_the_battery_stays_low(self):
        stop, attempts = threading.Event(), []
        readings = iter(BatterySample(t, 7, False) for t in range(6))

        def read():
            sample = next(readings)
            if sample.at == 5:
                stop.set()
            return sample

        def shutdown():
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError('battery bus temporarily unavailable')

        with patch.object(stop, 'wait'), redirect_stdout(io.StringIO()):
            read_battery(SimpleNamespace(battery_sample=None), stop, read, lambda: None, shutdown)
        self.assertEqual(len(attempts), 2)

    def test_one_low_reading_or_low_while_charging_does_not_shut_down(self):
        self.assertEqual(self.run_samples([BatterySample(1, 7, False), BatterySample(2, 30, False), BatterySample(3, 7, False)]), 0)
        self.assertEqual(self.run_samples([BatterySample(t, 3, True) for t in range(5)]), 0)


class ShortReadFailures(unittest.TestCase):
    def test_one_failed_reading_keeps_the_last_one_and_three_in_a_row_drop_it(self):
        board, stop, shown = SimpleNamespace(battery_sample=None), threading.Event(), []
        good, lost = BatterySample(1, 60, True), OSError(121, 'Remote I/O error')
        readings = [good, lost, good, lost, lost, lost, good]

        def read():
            shown.append(board.battery_sample is not None)  # (what the board had to show before this reading)
            reading = readings.pop(0)
            if not readings:
                stop.set()
            if isinstance(reading, OSError):
                raise reading
            return reading
        with patch.object(stop, 'wait'), redirect_stdout(io.StringIO()):
            read_battery(board, stop, read, protect=lambda: None, shutdown=lambda: None)
        self.assertEqual(shown, [False, True, True, True, True, True, False])


class PowerOffLater(unittest.TestCase):
    def test_power_cut_is_armed_before_linux_shutdown_and_cancelled_if_refused(self):
        registers = {0: 3, 1: 15, 0x02: 0xe0, 0x0b: 0}
        def ioctl(fd, cmd, request):
            if cmd == 0x0703:
                return
            data = ctypes.cast(request.data, ctypes.POINTER(ctypes.c_ubyte))
            if request.read_write:
                data[0] = registers.get(request.command, 0)
            else:
                registers[request.command] = data[0] if request.command != 0x0b else int(data[0] == 0x29)
        def refuse(*args, **kwargs):
            self.assertFalse(registers[0x02] & 0x20)
            self.assertGreater(registers[0x09], 0)
            raise subprocess.CalledProcessError(1, 'poweroff')
        with patch('subprocess.run', side_effect=refuse), patch('builtins.open', mock_open()), \
             patch('padcade.drivers.battery.fcntl.ioctl', ioctl):
            with self.assertRaises(subprocess.CalledProcessError):
                shut_down()
        self.assertTrue(registers[0x02] & 0x20)
        self.assertEqual(registers[0x0b], 0)

    def test_sets_delay_and_auto_power_on_then_starts_the_countdown_and_relocks(self):
        registers, writes = {0: 3, 1: 15, 0x02: 0xec, 0x0b: 0}, []

        def ioctl(fd, command, request):
            if command == 0x0703:
                return 0
            data = ctypes.cast(request.data, ctypes.POINTER(ctypes.c_ubyte))
            if request.read_write == 1:
                data[0] = registers.get(request.command, 0)
            else:
                writes.append((request.command, data[0]))
                registers[request.command] = data[0] if request.command != 0x0b else int(data[0] == 0x29)
            return 0
        with patch('builtins.open', mock_open()), patch('padcade.drivers.battery.fcntl.ioctl', ioctl):
            power_off_later(30)
        self.assertEqual(writes, [(0x0b, 0x29), (0x09, 30), (0x02, 0xdc), (0x0b, 0)])
