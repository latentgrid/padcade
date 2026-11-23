"""The board on its hardware: the Launchpad's presses in, its frames out (at least once a second, so the pads come
back from anything that blanked them), the battery board and the motion sensor read in threads of their own."""
import asyncio
import json
import os
import select
import socket
import sys
import threading
from contextlib import ExitStack, contextmanager

from . import SLUG
from .drivers import mqtt_device, sound
from .drivers.battery import enable_charge_limit, read_battery, sample_battery
from .drivers.imu import motion_reader
from .drivers.launchpad import SIDE_OFF, Reader, grid, open_launchpad, set_up


class Remote:
    """The board as the MQTT thread sees it: what it reads it reads as it is, what it changes is done in the loop."""
    def __init__(self, board, runner, stopped):
        self._board, self._runner, self._stopped = board, runner, stopped

    def post(self, action):
        self._runner.post(lambda now: None if self._stopped.is_set() else action())

    def __getattr__(self, name):
        return getattr(self._board, name)

    def set_power(self, power):
        self.post(lambda: self._board.set_power(power))

    def open(self, page):
        self.post(lambda: self._board.open(page))

    def configure(self, settings):
        self.post(lambda: self._board.configure(settings))

    def set_volume(self, level):
        self.post(lambda: self._board.set_volume(level))

    def mqtt_status(self, message):
        def update():
            if self._board.mqtt_connection != message:
                self._board.mqtt_connection = message
                self._board.revision += 1
        self.post(update)


@contextmanager
def home_assistant_device(board, runner):
    """With a broker in config.toml ([mqtt] host, port, username, password) the board is one device in Home
    Assistant: display, page, brightness, volume, battery. What was last set there is kept for the next start."""
    credentials, kept = board.config.get('mqtt'), board.home / 'state.json'
    board.active_mqtt = credentials.copy() if isinstance(credentials, dict) and credentials.get('host') else None
    board.mqtt_connection = 'Connecting…' if board.active_mqtt else None
    if board.active_mqtt is None:
        yield
        return
    try:  # (the page, the volume and whether the pads were lit, as they were; the brightness is config.toml's)
        for name, value in json.loads(kept.read_text()).items():
            if name != 'brightness':
                mqtt_device.apply_command(board, name, value)
    except (OSError, ValueError, AttributeError):
        pass

    stopped = threading.Event()
    remote = Remote(board, runner, stopped)

    def run():
        try:
            asyncio.run(mqtt_device.run_mqtt(remote, board.active_mqtt, kept, stopped))
        except ImportError:
            remote.mqtt_status('MQTT support is missing. Rerun the installer.')
            print('The MQTT device needs the paho-mqtt package (the install script installs it)', flush=True)
        except Exception as error:  # (the board carries on without it)
            remote.mqtt_status('MQTT could not start. Check the broker settings and restart Padcade.')
            print(f'MQTT device: {type(error).__name__}', flush=True)
    worker = threading.Thread(target=run, name='mqtt', daemon=True)
    worker.start()

    def disconnect():
        stopped.set()
        worker.join(3)
        if worker.is_alive():
            raise TimeoutError('The broker is still disconnecting. Try again in a moment.')
        board.active_mqtt = None
        if board.mqtt_connection is not None:
            board.mqtt_connection = None
            board.revision += 1

    board.disconnect_mqtt = disconnect
    try:
        yield
    finally:
        disconnect()
        board.disconnect_mqtt = None


def fitted(part):
    """Whether the installer wrote this part down as fitted (speaker, motion, battery)."""
    try:
        with open(f'/etc/{SLUG}.parts') as parts:
            return f'{part}=yes' in parts.read().splitlines()
    except FileNotFoundError:
        return False


@contextmanager
def motion(board):
    """The motion sensor read in a thread of its own, where one was installed. Elsewhere the serial port is left
    alone: it may be a console, or something else's."""
    if not fitted('motion'):
        yield
        return
    with motion_reader(board):
        yield


@contextmanager
def battery(board):
    """The battery board read in a thread of its own (the driver's loop). It is kept at its charge limit of about
    80 % unless config.toml says 100: then the limit is no longer set again (the battery board forgets it when it
    is switched off and on)."""
    if not fitted('battery'):
        yield
        return
    stop = threading.Event()
    protect = lambda: enable_charge_limit() if board.config['charge_limit'] < 100 else None
    worker = threading.Thread(target=read_battery, args=(board, stop, sample_battery, protect), name='pisugar', daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(2)


class Speaker:
    """The amplifier in the case, through the mixer."""
    def play(self, clip, gain=1.):
        return sound.aplay(clip, gain)

    def volume(self, level):
        sound.amixer(sound.LEVELS[level - 1])

    def prepare(self, clips):
        sound.PCM.prepare(clips)

    def wake(self):
        sound.wake_amp()


def notify_ready():
    address = os.environ.get('NOTIFY_SOCKET')
    if address:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
            channel.sendto(b'READY=1', '\0' + address[1:] if address.startswith('@') else address)


def run(board, runner, connect=open_launchpad, parts=(battery, motion)):
    """Runs the board until the Launchpad is unplugged (the service starts it again and waits for USB). The battery's
    charge limit and the management page do not wait for the Launchpad: they work while nothing is plugged in."""
    sys.setswitchinterval(.001)  # (lets the audio writer have Python back before its 5.8 ms period is over)
    gone = threading.Event()

    def launchpad():
        while not gone.is_set():
            try:
                control, midi = connect()
                break
            except OSError as error:
                print(f'Launchpad USB unavailable: {error}', flush=True)
                gone.wait(1)
        else:
            return
        try:
            set_up(midi)
            print('Sent Standalone and Programmer commands to Launchpad', flush=True)
            board.parts['Launchpad Pro'] = 'found on USB'
            previous = message = last = None
            pending, sent = b'', 0.

            def show(frame, now):
                nonlocal previous, message, last, pending, sent
                if gone.is_set():
                    return False
                if frame != previous:
                    message, previous = bytes(grid(frame) + SIDE_OFF), frame
                if not pending and (message != last or now - sent > 1):
                    pending, last, sent = message, message, now
                if pending:
                    try:
                        pending = pending[os.write(midi, pending):]
                    except BlockingIOError:
                        pass  # (the Launchpad is behind: the next frame)
                    except OSError:
                        return False

            runner.show, reader = show, Reader()
            while not gone.is_set():
                try:
                    if not select.select([midi], [], [], .2)[0]:
                        continue
                    data = os.read(midi, 256)
                except BlockingIOError:
                    continue
                except OSError:
                    data = b''
                if not data:
                    break
                for kind, x, y, value in reader.feed(data):
                    if kind == 'press':
                        runner.post(lambda now, x=x, y=y, value=value: board.press(x, y, value, now))
                    elif kind == 'release':
                        runner.post(lambda now, x=x, y=y: board.release(x, y, now))
        finally:
            gone.set()
            runner.show = lambda frame, now: False
            os.close(midi)
            os.close(control)

    with ExitStack() as session:
        for part in parts:
            session.enter_context(part(board))
        session.enter_context(home_assistant_device(board, runner))
        worker = threading.Thread(target=launchpad, name='launchpad', daemon=True)
        worker.start()
        session.callback(worker.join, 2)
        session.callback(gone.set)
        runner.run(ready=notify_ready)
    print('Launchpad disconnected; the service will wait for USB', flush=True)
