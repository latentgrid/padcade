"""The board on its hardware, with a socket standing in for the Launchpad's USB port."""
import contextlib
import io
import json
import os
import socket
import sys
import threading
import time
import unittest
import wave
from types import ModuleType, SimpleNamespace
from unittest.mock import mock_open, patch

from padcade import device
from padcade.web import Runner
from tests.kit import Home, note, ship

GRID = bytes.fromhex('f0 00 20 29 02 10 0f 00')


class SoundboardInput(unittest.TestCase):
    def test_first_strike_on_a_prepared_bank_plays_while_the_next_sample_is_loading(self):
        home = Home(self)
        ship(home, 'soundboard')
        folder = home.path / 'data/soundboard/clips'
        kick = note(folder / 'Kick.wav')
        snare = note(folder / 'Snare.wav')
        held, release, heard = threading.Event(), threading.Event(), threading.Event()

        class Output:
            frames = 128
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def park(self):
                pass
            def wait(self):
                pass
            def write(self, block):
                if any(block):
                    heard.set()

        open_wav = wave.open
        opens = []
        def load(path, mode):
            opens.append(path)
            if path == str(snare):
                held.set()
                release.wait(3)
            return open_wav(path, mode)

        home.sound = device.Speaker()
        player = device.sound.PCMPlayer(start=Output)
        with (patch.object(device.sound, 'PCM', player), patch.object(device.sound, 'wake_amp'),
              patch.object(device.sound, 'amixer'), patch.object(wave, 'open', side_effect=load)):
            try:
                board = home.board()
                board.open('soundboard')
                self.assertTrue(held.wait(2))
                board.press(1, 1, 120, 1.)
                self.assertTrue(heard.wait(1))
                self.assertEqual(opens.count(str(kick)), 1)
            finally:
                player.prepare(())
                release.set()


class USB(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        self.home.plugin('red', button='top1')
        board = self.home.board()
        port, self.launchpad = socket.socketpair()
        self.launchpad.settimeout(3)
        self.midi, self.control = port.detach(), os.open(os.devnull, os.O_RDONLY)
        os.set_blocking(self.midi, False)
        self.started = []
        self.runner = Runner(board, simulator=False)
        part = lambda board: self.started.append('part') or contextlib.nullcontext()
        connect = lambda: self.started.append('launchpad') or (self.control, self.midi)
        self.thread = threading.Thread(target=device.run, args=(board, self.runner, connect, (part,)), daemon=True)
        self.thread.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.runner.stop()
        self.thread.join(3)
        self.launchpad.close()

    def until_picture(self, lit=None):
        """What the Launchpad is sent up to its next whole picture (one that is lit or dark, if asked)."""
        received = bytearray()
        while True:
            received.extend(self.launchpad.recv(4096))
            while GRID in received and len(received) >= received.index(GRID) + 309:
                start = received.index(GRID)
                picture = received[start + 8:start + 308]
                if lit is None or any(picture) == lit:
                    return bytes(received[:start])
                del received[:start + 309]

    def test_a_new_usb_connection_selects_standalone_then_programmer(self):
        self.assertEqual(self.until_picture(), bytes.fromhex('f0 00 20 29 02 10 21 01 f7 f0 00 20 29 02 10 2c 03 f7'))

    def test_buttons_still_update_the_display_after_a_static_frame(self):
        self.until_picture(lit=True)
        self.launchpad.sendall(bytes((0xb0, 98, 127)))  # (power)
        self.until_picture(lit=False)
        self.launchpad.sendall(bytes((0xb0, 91, 127)))
        self.until_picture(lit=True)

    def test_a_short_usb_write_completes_the_picture_before_starting_another(self):
        self.until_picture(lit=True)
        write = os.write
        def short_write(fd, data):
            return write(fd, data[:64] if fd == self.midi else data)
        with patch.object(os, 'write', short_write):
            self.launchpad.sendall(bytes((0xb0, 98, 127)))  # switch the lights off
            received = bytearray()
            expected = GRID + bytes(300) + b'\xf7'
            while GRID not in received or len(received) < received.index(GRID) + len(expected):
                received.extend(self.launchpad.recv(4096))
            start = received.index(GRID)
            self.assertEqual(received[start:start + len(expected)], expected)

    def test_losing_the_launchpad_ends_the_run_so_the_service_can_reopen_it(self):
        self.until_picture()
        self.launchpad.shutdown(socket.SHUT_WR)
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())
        for fd in (self.midi, self.control):
            with self.assertRaises(OSError):
                os.fstat(fd)


class WithoutTheLaunchpad(unittest.TestCase):
    def test_systemd_gets_ready_after_startup_even_without_a_usb_board(self):
        home = Home(self)
        home.plugin('red')
        board = home.board()
        runner, missing = Runner(board, simulator=False), threading.Event()
        def connect():
            missing.wait()
            raise FileNotFoundError('no USB board')

        address = str(home.path / 'notify')
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener, patch.dict(os.environ, NOTIFY_SOCKET=address):
            listener.bind(address)
            listener.settimeout(1)
            thread = threading.Thread(target=device.run, args=(board, runner, connect, ()), daemon=True)
            thread.start()
            try:
                self.assertEqual(listener.recv(64), b'READY=1')
                self.assertTrue(runner.do(lambda now: board.state()['pages']))
            finally:
                runner.stop()
                missing.set()
                thread.join(3)

    def test_a_failed_usb_open_retries_and_accepts_a_reconnected_device(self):
        home = Home(self)
        home.plugin('red')
        board = home.board()
        runner = Runner(board, simulator=False)
        port, pad = socket.socketpair()
        self.addCleanup(pad.close)
        self.addCleanup(port.close)
        pad.settimeout(4)
        attempts = []

        def connect():
            attempts.append(True)
            if len(attempts) == 1:
                raise FileNotFoundError('unplugged during discovery')
            return os.open(os.devnull, os.O_RDONLY), os.dup(port.fileno())

        thread = threading.Thread(target=device.run, args=(board, runner, connect, ()), daemon=True)
        thread.start()
        try:
            self.assertTrue(pad.recv(4096))
        finally:
            runner.stop()
            thread.join(3)

    def test_the_battery_is_looked_after_and_the_page_answers_while_nothing_is_plugged_in(self):
        home = Home(self)
        home.plugin('red', button='top1')
        board, started, never = home.board(), [], threading.Event()
        runner = Runner(board, simulator=False)
        part = lambda board: started.append('battery') or contextlib.nullcontext()
        thread = threading.Thread(target=device.run, args=(board, runner, never.wait, (part,)), daemon=True)
        thread.start()
        self.addCleanup(runner.stop)
        self.assertEqual(runner.do(lambda now: board.state())['page'], 'red')
        self.assertEqual(started, ['battery'])


class Client:
    """A stand-in for paho's MQTT client: what it is told, and its callbacks for the test to call as a broker would."""
    made = []

    def __init__(self, *args, **options):
        self.client_id = options['client_id']
        self.told, self.published = {}, []
        Client.made.append(self)

    def username_pw_set(self, username, password):
        self.told['login'] = (username, password)

    def connect_async(self, host, port, keepalive):
        self.told['broker'] = (host, port)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))
        return SimpleNamespace(wait_for_publish=lambda timeout: None)

    def is_connected(self):
        return False

    def disconnect(self):
        self.told['disconnected'] = True

    will_set = reconnect_delay_set = subscribe = loop_start = loop_stop = lambda self, *args, **options: None


class HomeAssistantDevice(unittest.TestCase):
    def test_with_a_broker_in_the_config_the_board_is_a_device_there_and_obeys_it(self):
        legacy = self.check_device(1, legacy=True)
        first = self.check_device(2)
        second = self.check_device(2)
        self.assertTrue(all(a != b for a, b in zip(first, second)))
        self.assertTrue(all(a != b for a, b in zip(legacy, first)))

    def check_device(self, version, legacy=False):
        Client.made.clear()
        home = Home(self)
        home.plugin('red', button='top1')
        home.plugin('blue', button='top2')
        if legacy:
            home.path.mkdir(exist_ok=True)
            (home.path / 'config.toml').write_text('[buttons]\n[mqtt]\nhost="broker.test"\n')
        board = home.board()
        board.configure(board.config | {'mqtt': {'host': 'broker.test', 'username': 'pads', 'password': 'p4ss-for-broker'}})
        expected_id = board.config['mqtt_id']
        board = home.board()  # MQTT starts on the next boot.
        self.assertEqual(board.config['mqtt_id'], expected_id)
        runner, never = Runner(board, simulator=False), threading.Event()
        paho = {'paho': ModuleType('paho'), 'paho.mqtt': ModuleType('paho.mqtt'),
                'paho.mqtt.client': SimpleNamespace(Client=Client, CallbackAPIVersion=SimpleNamespace(VERSION2=2))}
        if version == 1:
            del paho['paho.mqtt.client'].CallbackAPIVersion
        paho['paho.mqtt'].client = paho['paho.mqtt.client']
        with patch.dict(sys.modules, paho):
            thread = threading.Thread(target=device.run, args=(board, runner, never.wait, ()), daemon=True)
            thread.start()
            self.addCleanup(runner.stop)
            deadline = time.monotonic() + 3
            while not (Client.made and 'broker' in Client.made[-1].told) and time.monotonic() < deadline:
                time.sleep(.01)
            client = Client.made[-1]
            self.assertEqual(client.told, {'login': ('pads', 'p4ss-for-broker'), 'broker': ('broker.test', 1883)})
            def expect_status(expected):
                while True:
                    status = runner.do(lambda now: board.state()['mqtt_status'])
                    if expected in status or time.monotonic() >= deadline:
                        self.assertIn(expected, status)
                        return
                    time.sleep(.01)

            expect_status('Connecting')
            client.on_connect_fail(client, None)
            expect_status('Broker unreachable')
            reason = 5 if version == 1 else SimpleNamespace(is_failure=True, value=135)
            client.on_connect(client, None, {}, reason)
            client.on_disconnect(client, None, 5)
            expect_status('Credentials rejected')
            if version == 1:
                client.on_connect(client, None, {}, 0)
            else:
                client.on_connect(client, None, {}, SimpleNamespace(is_failure=False), None)
            topic, payload = client.published[0]
            announced = json.loads(payload)
            profile = announced['components']['profile']
            identity = (client.client_id, topic, announced['device']['identifiers'][0], profile['unique_id'],
                        profile['command_topic'], announced['state_topic'], announced['availability'][0]['topic'])
            if legacy:
                self.assertEqual(identity, ('launchpad-pro-pi', 'homeassistant/device/launchpad/config',
                    'launchpad_pro_pi', 'launchpad_profile', 'launchpad/set/profile', 'launchpad/state', 'launchpad/availability'))
            client.on_message(client, None, SimpleNamespace(retain=False, payload=b'Blue', topic=profile['command_topic']))
            while runner.do(lambda now: board.page) != 'blue' and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(runner.do(lambda now: board.page), 'blue')
            expect_status('Connected')
            while not any('"profile": "Blue"' in payload for topic, payload in client.published) and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(any(topic == announced['state_topic'] and '"profile": "Blue"' in payload for topic, payload in client.published))
            client.on_disconnect(client, None, 1)
            expect_status('Disconnected')
            runner.do(lambda now: board.reset(files=True))
            self.assertTrue(client.told.get('disconnected'), 'Factory reset disconnects the former broker')
        self.assertNotIn('p4ss-for-broker', json.dumps(board.state()))  # (the page is never sent the password)
        return identity


class Motion(unittest.TestCase):
    def test_the_serial_port_is_read_only_where_a_motion_sensor_was_installed(self):
        board = Home(self).board()
        for record, reads in (('motion=yes\n', True), ('battery=yes\nmotion=no\n', False), (None, False)):
            with self.subTest(record=record), patch('builtins.open', mock_open(read_data=record or '')) as parts, \
                 patch.object(device, 'motion_reader') as reader:
                if record is None:
                    parts.side_effect = FileNotFoundError
                with device.motion(board):
                    pass
                self.assertEqual(reader.called, reads)


class ChargeLimit(unittest.TestCase):
    def test_an_unconfigured_battery_never_polls_the_bus(self):
        home = Home(self)
        board = home.board()
        for record in ('battery=no\n', 'speaker=yes\nmotion=no\n', None):
            with self.subTest(record=record), patch('builtins.open', mock_open(read_data=record or '')) as parts, \
                 patch.object(device, 'sample_battery', side_effect=OSError) as read, patch.object(device, 'enable_charge_limit') as protect:
                if record is None:
                    parts.side_effect = FileNotFoundError
                with device.battery(board):
                    pass
                read.assert_not_called()
                protect.assert_not_called()

    def test_the_battery_is_held_at_its_limit_unless_the_owner_wants_it_full(self):
        home = Home(self)
        home.plugin('red', button='top1')
        board = home.board()
        for limit, wanted in ((80, True), (100, False)):
            board.configure(board.config | {'charge_limit': limit})
            with patch('builtins.open', mock_open(read_data='battery=yes\n')), patch.object(device, 'enable_charge_limit') as protect, \
                 patch.object(device, 'sample_battery', side_effect=OSError('no battery board')), \
                    contextlib.redirect_stdout(io.StringIO()):
                with device.battery(board):
                    deadline = time.monotonic() + 2
                    while not protect.called and wanted and time.monotonic() < deadline:
                        time.sleep(.01)
                    time.sleep(.05)
            self.assertEqual(protect.called, wanted, limit)
