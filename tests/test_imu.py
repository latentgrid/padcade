import io
import math
import socket
import struct
import threading
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch



def wire_packet(channel, payload, sequence=0):
    packet = b'\x01' + struct.pack('<HBB', len(payload) + 4, channel, sequence) + payload
    return b'\x7e' + packet.replace(b'\x7d', b'\x7d\x5d').replace(b'\x7e', b'\x7d\x5e') + b'\x7e'


def reports(sequence=0):
    data = bytearray(b'\xfb\0\0\0\0')
    for report, values, padding in (
        (1, (125, 126, 2511), b''), (2, (0, 512, -512), b''),
        (3, (160, 320, -480), b''), (5, (0, 0, 0, 16384), b'\0\0'),
        (8, (0, 0, 0, 16384), b''),
    ):
        data.extend(bytes((report, sequence, 3, 0)))
        data.extend(struct.pack(f'<{len(values)}h', *values) + padding)
    return bytes(data)


class PickupMotion(unittest.TestCase):
    def sample(self, at, roll=0, yaw=0, acceleration=9.81, gyro=0):
        from padcade.drivers.imu import MotionSample

        r, z = math.radians(roll) / 2, math.radians(yaw) / 2
        q = (math.sin(r) * math.cos(z), math.sin(r) * math.sin(z),
             math.cos(r) * math.sin(z), math.cos(r) * math.cos(z))
        return MotionSample(at, (0., 0., acceleration), (gyro, 0., 0.), (0., 0., 0.), q, q)

    def test_lifting_after_rest_triggers_once_in_either_sensor_mounting(self):
        from padcade.drivers.imu import Pickup

        for rest in (0, 180):
            with self.subTest(rest=rest):
                pickup = Pickup()
                for i in range(13):
                    self.assertFalse(pickup.update(self.sample(i / 20, roll=rest)))
                triggers = [pickup.update(self.sample(i / 20, roll=rest + 20))
                            for i in range(13, 61)]
                self.assertEqual(sum(triggers), 1)
                self.assertTrue(any(triggers[:5]))

    def test_sustained_straight_lift_also_wakes_but_not_while_already_moving(self):
        from padcade.drivers.imu import Pickup

        pickup = Pickup()
        for i in range(31):
            self.assertFalse(pickup.update(self.sample(i / 20, acceleration=11.5, gyro=.3)))
        for i in range(31, 45):
            self.assertFalse(pickup.update(self.sample(i / 20)))
        triggers = [pickup.update(self.sample(i / 20, acceleration=11.5)) for i in range(45, 65)]
        self.assertEqual(sum(triggers), 1)

    def test_missing_or_late_reports_cannot_finish_an_old_pickup(self):
        from padcade.drivers.imu import Pickup

        for missing in (False, True):
            with self.subTest(missing=missing):
                pickup = Pickup()
                for i in range(13):
                    pickup.update(self.sample(i / 20))
                self.assertFalse(pickup.update(self.sample(.65, roll=20)))
                if missing:
                    self.assertFalse(pickup.update(None))
                for i in range(20, 41):
                    self.assertFalse(pickup.update(self.sample(i / 20, roll=20)))

    def test_bumps_jitter_and_yaw_do_not_wake_or_block_a_later_pickup(self):
        from padcade.drivers.imu import Pickup

        pickup = Pickup()
        for i in range(13):
            pickup.update(self.sample(i / 20, roll=180))
        for i in range(13, 61):
            bump = i % 10 in (0, 1)
            self.assertFalse(pickup.update(self.sample(i / 20, roll=200 if bump else 180 + i % 3,
                                                      yaw=i * 6, acceleration=13 if bump else 9.9)))
        triggers = [pickup.update(self.sample(i / 20, roll=160)) for i in range(61, 67)]
        self.assertEqual(sum(triggers), 1)

    def test_repeated_or_out_of_order_reports_do_not_count_as_sustained_motion(self):
        from padcade.drivers.imu import Pickup

        pickup = Pickup()
        for i in range(13):
            pickup.update(self.sample(i / 20))
        candidate = self.sample(.65, roll=20)
        for _ in range(30):
            self.assertFalse(pickup.update(candidate))
        self.assertFalse(pickup.update(self.sample(.6, roll=20)))
        self.assertFalse(pickup.update(self.sample(.7)))
        self.assertFalse(pickup.update(self.sample(.75, roll=20)))
        self.assertFalse(pickup.update(self.sample(.8, roll=20)))
        self.assertTrue(pickup.update(self.sample(.95, roll=20)))


class UARTInput(unittest.TestCase):
    def setUp(self):
        from padcade.drivers.imu import UARTSensor, Sampler

        self.device, self.sensor_socket = socket.socketpair()
        self.addCleanup(self.device.close)
        self.addCleanup(self.sensor_socket.close)
        self.sensor = UARTSensor(self.device.fileno())
        self.sampler = Sampler(self.sensor)

    def receive(self, data):
        self.sensor_socket.sendall(data)
        self.sensor.poll(0)

    def test_fragmented_escaped_reports_produce_all_five_readings_once(self):
        packet = wire_packet(3, reports())
        for byte in packet[:-1]:
            self.receive(bytes((byte,)))
            self.assertIsNone(self.sampler.read())
        self.receive(packet[-1:])
        sample = self.sampler.read()
        self.assertEqual(sample.acceleration, (125 / 256, 126 / 256, 2511 / 256))
        self.assertEqual(sample.gyroscope, (0, 1, -1))
        self.assertEqual(sample.magnetic_field, (10, 20, -30))
        self.assertEqual(sample.game_orientation, (0, 0, 0, 1))
        self.receive(packet)  # Replayed reports must not refresh the timestamp.
        self.assertIsNone(self.sampler.read())
        self.receive(wire_packet(3, reports(1)))
        self.assertIsNotNone(self.sampler.read())

    def test_truncated_or_overlong_frames_do_not_partially_publish(self):
        self.receive(wire_packet(3, reports()[:-1]))
        self.assertIsNone(self.sensor.acceleration)
        self.receive(b'\x7e' + b'x' * 5000 + b'\x7e')
        self.receive(wire_packet(3, reports(2)))
        self.assertIsNotNone(self.sampler.read())

    def test_runtime_reset_and_disconnect_surface_to_reader(self):
        self.sensor.ready = True
        with self.assertRaises(OSError):
            self.receive(wire_packet(1, b'\x01'))
        self.sensor_socket.shutdown(socket.SHUT_WR)
        with self.assertRaises(OSError):
            self.sensor.poll(0)

    def test_a_late_report_cannot_make_other_old_readings_fresh(self):
        with patch('padcade.drivers.imu.time.monotonic', return_value=10):
            self.receive(wire_packet(3, reports()[:-12]))
        with patch('padcade.drivers.imu.time.monotonic', return_value=11):
            self.receive(wire_packet(3, reports()[-12:]))
            self.assertIsNone(self.sampler.read())

    def test_configuration_waits_for_allowance_and_uses_sh2_period(self):
        self.sensor_socket.settimeout(1)
        errors = []

        def configure():
            try:
                self.sensor.configure(8, 50_000)
            except Exception as error:
                errors.append(error)

        with patch('padcade.drivers.imu.termios.tcdrain'), patch('padcade.drivers.imu.time.sleep'):
            worker = threading.Thread(target=configure)
            worker.start()
            request = b''
            while len(request) < 3:
                request += self.sensor_socket.recv(3 - len(request))
            self.assertEqual(request, b'\x7e\0\x7e')
            self.sensor_socket.sendall(b'\x7e\0\x40\0\x7e')
            expected = wire_packet(2, struct.pack('<BBBHIII', 0xfd, 8, 0, 0, 50_000, 0, 0))
            received = b''
            while len(received) < len(expected):
                received += self.sensor_socket.recv(len(expected) - len(received))
            worker.join(1)
        self.assertEqual(errors, [])
        self.assertEqual(received, expected)


class MotionInput(unittest.TestCase):
    def test_sampler_waits_for_new_reports_including_when_stationary(self):
        from padcade.drivers.imu import Sampler

        sensor = SimpleNamespace(acceleration=None, gyro=None, magnetic=None,
                                 quaternion=None, game_quaternion=None)
        reader = Sampler(sensor)
        self.assertIsNone(reader.read())
        readings = {
            'acceleration': (0., 0., 9.81),
            'gyro': (0., 0., 0.),
            'magnetic': (17., 4., 36.),
            'quaternion': (0., 0., 0., 1.),
            'game_quaternion': (0., 0., 0., 1.),
        }
        for name, values in readings.items():
            setattr(sensor, name, values)
        sample = reader.read()
        self.assertEqual(sample.acceleration, readings['acceleration'])
        self.assertEqual(sample.gyroscope, readings['gyro'])
        self.assertEqual(sample.magnetic_field, readings['magnetic'])
        self.assertEqual(sample.orientation, readings['quaternion'])
        self.assertEqual(sample.game_orientation, readings['game_quaternion'])
        self.assertIsNone(reader.read())
        for name, values in readings.items():
            setattr(sensor, name, tuple(list(values)))
        self.assertIsNotNone(reader.read())  # A fresh report can contain identical numbers.

    def test_sampler_withholds_invalid_orientation(self):
        from padcade.drivers.imu import Sampler

        sensor = SimpleNamespace(acceleration=None, gyro=None, magnetic=None,
                                 quaternion=None, game_quaternion=None)
        reader = Sampler(sensor)
        sensor.acceleration = (0., 0., 9.81)
        sensor.gyro = (0., 0., 0.)
        sensor.magnetic = (17., 4., 36.)
        sensor.quaternion = (0., 0., 0., 0.)
        sensor.game_quaternion = (0., 0., 0., 1.)
        self.assertIsNone(reader.read())
        sensor.quaternion = (0., 0., 0., 1.)
        self.assertIsNotNone(reader.read())

    def test_reader_clears_input_on_a_bus_failure(self):
        from padcade.drivers.imu import read_motion

        board = SimpleNamespace(motion_sample=SimpleNamespace(at=10.0))
        stop = threading.Event()

        def disconnected():
            stop.set()
            raise OSError('I2C disconnected')

        with redirect_stdout(io.StringIO()) as log:
            read_motion(board, stop, disconnected)
        self.assertIn('I2C disconnected', log.getvalue())
        self.assertIsNone(board.motion_sample)
