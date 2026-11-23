"""The board running, and its management page: one loop that owns the board (50 frames a second; everything that
touches the board is done there, in turn) and a small web server beside it (standard library only). The page's live
grid is the board's own frames, so without a Launchpad the page is the simulator."""
import io
import ipaddress
import json
import queue
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from collections import deque
from concurrent.futures import Future
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from . import NAME, REPO, SLUG, VERSION, config
from .drivers.sound import LEVELS
from .release import newer_release

FPS = 50
PAGE = Path(__file__).with_name('page.html')
LARGEST = 60 * 2**20  # bytes of an upload
OWN = {'localhost', socket.gethostname().lower(), socket.gethostname().lower() + '.local', socket.getfqdn().lower()}  # the board's names


def latest_release(found={}):
    """The newest release's version (asked of GitHub at most once an hour), or None if that is unknown."""
    if not REPO:
        return None
    if time.monotonic() - found.get('at', -1e9) > 3600:
        try:
            with urllib.request.urlopen(f'https://api.github.com/repos/{REPO}/releases/latest', timeout=5) as answer:
                tag = json.loads(answer.read())['tag_name']
                found['version'] = tag if isinstance(tag, str) else None
        except (OSError, ValueError, KeyError):
            found['version'] = None
        found['at'] = time.monotonic()
    return found['version']


class BrowserSound:
    """The simulator's speaker is the browser: what plays is queued for the page, and a voice lasts as long as its
    file does."""
    class Voice:
        def __init__(self, sound, number, seconds):
            self.sound, self.number, self.started = sound, number, time.monotonic()
            self.ends, self.code, self.releasing = self.started + seconds, None, False

        def poll(self):
            return self.code if self.code is not None or time.monotonic() < self.ends else 0

        def release(self, seconds):
            if self.poll() is None and not self.releasing:
                now = time.monotonic()
                self.releasing = True
                self.ends = min(self.ends, now + seconds)
                self.sound.events.append({'n': self.sound.count(), 'stop': self.number,
                                          'at': now, 'release': seconds, 'held': now - self.started})

        def terminate(self):
            if self.poll() is None:
                self.code = -15
                self.sound.events.append({'n': self.sound.count(), 'stop': self.number, 'at': time.monotonic()})

    def __init__(self):
        self.events, self.files, self.n = [], {}, 0  # (the last sounds for the page; number -> file)

    def count(self):
        self.n += 1
        return self.n

    def play(self, clip, gain=1.):
        with wave.open(str(clip), 'rb') as wav:
            seconds = wav.getnframes() / wav.getframerate()
        number = self.count()
        self.files[number] = Path(clip)
        voice = self.Voice(self, number, seconds)
        self.events.append({'n': number, 'play': number, 'gain': gain, 'at': voice.started,
                            'seconds': seconds, 'key': f'{clip}:{Path(clip).stat().st_mtime_ns}'})
        del self.events[:-40]
        for old in [n for n in self.files if n < number - 40]:
            del self.files[old]
        return voice

    def volume(self, level):
        pass  # (the page reads the board's volume)

    def wake(self):
        pass


class Runner:
    """The loop. Other threads (the web server, the Launchpad's reader) hand it what they want done with `do`."""
    def __init__(self, board, show=None, simulator=True):
        self.board, self.show, self.simulator = board, show, simulator
        self.jobs, self.news = queue.Queue(), threading.Condition()
        self.frame, self.serial, self.running = [(0, 0, 0)] * 100, 0, True
        self.mark = (None, None, 0)

    def do(self, job, timeout=10):
        """Runs job(now) in the loop and gives what it returns."""
        done = Future()
        self.jobs.put((job, done, time.monotonic()))
        try:
            return done.result(timeout)
        except TimeoutError:
            if done.cancel():
                raise
            # A started reset (or write) has already taken effect. Return its actual outcome.
            return done.result()

    def post(self, job):
        """The same without waiting for it."""
        self.jobs.put((job, Future(), time.monotonic()))

    def run(self, ready=None):
        board, asked = self.board, []
        first_frame, frame_at, audio_at = True, 0., None
        while self.running:
            try:
                while True:
                    asked.append(self.jobs.get_nowait())
            except queue.Empty:
                pass
            for index, (job, done, arrived) in enumerate(asked):
                if index:
                    # A preceding job may have started playback or changed its tempo.
                    audio_at = board.audio_deadline()
                # Input arriving before a downbeat can still arm its count-in. Between jobs,
                # service elapsed beats before another request gets the board.
                before = time.monotonic() if index else arrived
                due = audio_at is not None and audio_at < before
                if due:
                    audio_at = board.audio_deadline(time.monotonic())
                if self.simulator and (index or due):
                    self.publish(self.frame, sounds_only=True)
                if not done.set_running_or_notify_cancel():
                    continue
                try:
                    done.set_result(job(time.monotonic()))
                except Exception as error:
                    done.set_exception(error)
            now = time.monotonic()
            if audio_at is not None and now >= audio_at:
                audio_at = board.audio_deadline(now)  # Submit due notes before drawing LEDs.
            if now >= frame_at or asked:
                board.tick(now)
                frame = board.frame(now)
                if self.show and self.show(frame, now) is False:
                    break  # (the Launchpad is gone)
                if first_frame:
                    if ready:
                        ready()
                    board.finish_owner_migration()
                    first_frame = False
                # Dark pads only need the pickup sensor. Input still wakes the queue immediately.
                frame_at = now + (1 / FPS if board.power else .05)
                audio_at = board.audio_deadline()
            self.publish(frame)
            try:
                wake_at = min(frame_at, audio_at) if audio_at is not None else frame_at
                asked = [self.jobs.get(timeout=max(0., wake_at - time.monotonic()))]
            except queue.Empty:
                asked = []

    def publish(self, frame, sounds_only=False):
        events = getattr(self.board.sound, 'events', ())
        last_sound = events[-1]['n'] if events else 0
        if sounds_only and last_sound == self.mark[2]:
            return
        mark = (frame, self.board.revision, last_sound)
        if mark != self.mark:
            with self.news:
                self.frame, self.mark, self.serial = frame, mark, self.serial + 1
                self.news.notify_all()

    def start(self):
        threading.Thread(target=self.run, name='board', daemon=True).start()
        return self

    def stop(self):
        self.running = False

    def live(self, seen, last_sound):
        """What the page shows live, once it is newer than `seen` (or after a while anyway): the surface as 100
        colours, and the sounds a simulator's page plays."""
        with self.news:
            self.news.wait_for(lambda: self.serial != seen, timeout=10)
            board = self.board
            serial, frame, revision, volume = self.serial, self.frame, board.revision, board.volume
            sounds = [e for e in getattr(board.sound, 'events', ()) if e['n'] > last_sound]
        # The board can publish the next frame while this browser encodes its copy.
        return serial, {
            'frame': bytes(min(255, max(0, round(c))) for rgb in frame for c in rgb).hex(),
            'revision': revision, 'gain': 10 ** (LEVELS[volume - 1] / 20), 'sounds': sounds,
        }


KINDS = {'.js': 'text/javascript', '.css': 'text/css'}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    disable_nagle_algorithm = True  # Flush small pad replies without waiting for a delayed TCP acknowledgement.

    def log_message(self, *args):
        pass

    def send(self, body, kind='application/json', status=200, cookie=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, default=str).encode()
        self.send_response(status)
        if cookie:
            self.send_header('Set-Cookie', f'session={cookie}; HttpOnly; SameSite=Strict; Path=/' + ('; Secure' if self.tls() else ''))
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def state(self, catalog_page=None, config_version=None, catalog_version=None):
        runner = self.server.runner
        def snapshot(now):
            state = runner.board.state(catalog_page, config_version=config_version,
                                       catalog_version=catalog_version, share_config=True)
            state['sound_seq'] = getattr(runner.board.sound, 'n', 0)
            return state
        return runner.do(snapshot) | {'simulator': runner.simulator, 'requires_owner': self.requires_owner(),
                                      'secure_port': self.server.secure_port, 'is_admin': self.admin}

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def foreign(self):
        """A request another website's page made the browser send, or one for a name the board does not go by (a
        site that points its own name at the board)."""
        host = (self.headers.get('Host') or '').rsplit(':', 1)[0].strip('[]').lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if host not in OWN | {str(name).lower() for name in self.server.runner.board.config.get('names', [])}:
                return f'The board does not go by the name {host}. Open it by its address, or add the name to config.toml: names = ["{host}"]'
        # A link from the other board may open the public page, just like a bookmark.
        if (self.command == 'GET' and self.path == '/' and self.headers.get('Sec-Fetch-Mode') == 'navigate'
                and self.headers.get('Sec-Fetch-Dest') == 'document'):
            return
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host') or \
                self.headers.get('Sec-Fetch-Site', 'none') not in ('same-origin', 'none'):
            return 'Only the board\'s own page may ask it things.'

    def requires_owner(self):
        if self.server.requires_owner or any(name.lower() == 'forwarded' or name.lower().startswith('x-forwarded-')
                                             for name in self.headers):
            return True
        if not ipaddress.ip_address(self.server.server_address[0]).is_loopback:
            return not ipaddress.ip_address(self.client_address[0]).is_private
        # A proxy can preserve the public Host even when it opens a loopback connection.
        hostname = urlsplit('//' + (self.headers.get('Host') or '')).hostname
        if hostname == 'localhost':
            return False
        try:
            return not ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            return True

    def tls(self):
        return isinstance(self.connection, ssl.SSLSocket) or (
            self.server.requires_owner and ipaddress.ip_address(self.client_address[0]).is_loopback
            and self.headers.get('X-Forwarded-Proto') == 'https')

    def login_needed(self, management=False):
        path = '/' + '/'.join(filter(None, urlsplit(self.path).path.split('/')))
        if self.command == 'GET' and path == '/':
            return
        self.verifier, locked = config.credentials(self.server.runner.board.home)
        # At home (this computer or the local network, not through a proxy) a board without a password is its
        # visitors' to manage, and a password may be typed over plain HTTP. From anywhere else it takes HTTPS.
        home = not self.requires_owner()
        tls = self.tls()
        cookie = SimpleCookie(self.headers.get('Cookie', '')).get('session')
        self.admin = (self.verifier is None and home
                      or bool(self.verifier and (tls or home) and cookie and
                              self.server.sessions.get((tls, cookie.value)) == self.verifier))
        if self.verifier == '':
            return {'login': True, 'setup': True, 'error': 'Owner credentials cannot be read. Set a password locally on the board.'}
        management = management or path == '/api/admin' or (self.command != 'GET' and (
            path in ('/api/password', '/api/reset', '/api/restart', '/api/update') or path.split('/')[:3] == ['', 'api', 'plugins']))
        if path == '/api/login' and self.verifier and (tls or home):
            return
        if not (management or locked or self.requires_owner() or path == '/api/login') or self.admin:
            return
        return {'login': True, 'setup': self.verifier is None, 'management': management and not locked and not self.requires_owner(),
                'secure_port': self.server.secure_port, 'https_needed': not (tls or home)}

    def do_GET(self):
        if refused := self.foreign():
            return self.send({'error': refused}, status=403)
        if needed := self.login_needed():
            return self.send(needed, status=401)
        self.route('GET', b'')

    def do_POST(self):
        if refused := self.foreign():
            self.close_connection = True
            return self.send({'error': refused}, status=403)
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', ())) > 1:
            self.close_connection = True
            return self.send({'error': 'Use a single fixed request length.'}, status=400)
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        parts = tuple(filter(None, urlsplit(self.path).path.split('/')))
        limit = LARGEST
        if parts in (('api', 'password'), ('api', 'login')):
            limit = 4096
        elif self.command == 'POST' and parts == ('api', 'plugin', 'soundboard', 'import-song'):
            from .song_import import MAX_ARCHIVE
            limit = MAX_ARCHIVE
        if not 0 <= length <= limit:
            self.close_connection = True
            return self.send({'error': 'That file is too large.' if length > 0 else 'The request does not say how long it is.'},
                             status=413 if length > 0 else 400)
        if needed := self.login_needed():
            self.close_connection = True
            return self.send(needed, status=401)
        large = length > 2**20
        if large and not self.server.upload.acquire(blocking=False):
            self.close_connection = True
            return self.send({'error': 'Another upload is in progress. Try again shortly.'}, status=429)
        try:
            body = io.BytesIO()
            deadline = time.monotonic() + (120 if large else 15)
            while body.tell() < length:
                chunk = self.rfile.read1(min(65536, length - body.tell()))
                if not chunk or time.monotonic() > deadline:
                    self.close_connection = True
                    return self.send({'error': 'The upload did not finish in time.'}, status=408)
                body.write(chunk)
            if needed := self.login_needed():
                return self.send(needed, status=401)
            self.route(self.command, body.getvalue())
        finally:
            if large:
                self.server.upload.release()

    do_PUT = do_DELETE = do_POST

    def route(self, method, body):
        runner, board = self.server.runner, self.server.runner.board
        url = urlsplit(self.path)
        parts, params = [p for p in url.path.split('/') if p], dict(parse_qsl(url.query))
        try:
            if method == 'GET' and not parts:
                return self.send(PAGE.read_bytes().replace(b'<title>Board</title>', f'<title>{NAME}</title>'.encode()), 'text/html; charset=utf-8')
            if method == 'GET' and parts == ['limiter.js']:
                return self.send(PAGE.with_name('limiter.js').read_bytes(), 'text/javascript; charset=utf-8')
            if method == 'GET' and len(parts) == 3 and parts[0] == 'plugins' and not parts[2].startswith('.') and Path(parts[2]).suffix in KINDS:  # (a plugin's own part of this page)
                panel = board.plugins[parts[1]].folder / parts[2] if parts[1] in board.plugins else None
                if panel and not panel.is_symlink() and panel.is_file():
                    return self.send(panel.read_bytes(), KINDS[panel.suffix] + '; charset=utf-8')
            if parts[:1] != ['api']:
                return self.send({'error': 'Not here.'}, status=404)
            what = parts[1:]
            if method == 'GET' and what in (['flat'], ['press'], ['do'], ['config'], ['login'], ['password'], ['restart'], ['reset'], ['plugins']):
                return self.send({'error': 'This operation requires a write request.'}, status=405)
            if what == ['admin'] and method == 'GET':
                return self.send({})
            if what == ['state']:
                return self.send(self.state(params.get('catalog_page'), params.get('config_version'), params.get('catalog_version')))
            if what == ['live']:
                return self.stream()
            if what == ['login'] and method == 'POST':
                return self.login(body)
            if what == ['password'] and method == 'POST':
                ask = json.loads(body)
                if not isinstance(ask, dict) or not isinstance(ask.get('password'), str):
                    return self.send({'error': 'Enter a password.'}, status=400)
                password, playing = ask['password'], ask.get('open') is True
                if self.requires_owner() and not password:
                    return self.send({'error': 'This connection requires an owner password.'}, status=400)
                if not password and not playing and ask.get('remove') is not True:
                    return self.send({'error': 'Refresh the page to choose what the password protects.'}, status=400)
                verifier = runner.do(lambda now: board.set_password(password, playing))
                with self.server.session_lock:
                    self.server.sessions.clear()
                return self.start_session(verifier, self.state())
            if what == ['flat']:
                return self.send(runner.do(lambda now: board.level(now)) or self.state())
            if what == ['restart'] and method == 'POST':
                if runner.simulator:
                    return self.send({'error': 'A simulator is restarted where it was started.'})
                subprocess.Popen(['sudo', '-n', 'systemctl', 'restart', SLUG])
                return self.send({})
            if what == ['update']:
                if method == 'GET':
                    latest = latest_release()
                    return self.send({'installed': VERSION, 'latest': latest.removeprefix('v') if latest else None,
                                      'available': newer_release(VERSION, latest)})
                if runner.simulator or not REPO:
                    return self.send({'error': 'There is nothing to update here: get the new release where you got this one.'})
                subprocess.Popen(['sudo', '-n', f'/opt/{SLUG}/install.sh', '--update'])  # (it restarts the board when it is done)
                return self.send({})
            if what == ['reset'] and method == 'POST':
                try:
                    runner.do(lambda now: board.reset(params.get('files') == 'delete'))
                except OSError as error:
                    return self.send({'error': f'The reset could not finish: {error}'}, status=503)
                with self.server.session_lock:
                    self.server.sessions.clear()
                self.login_needed()
                return self.send(self.state())
            if what == ['press']:
                ask = json.loads(body)
                x, y = int(ask['x']), int(ask['y'])
                velocity = ask.get('velocity', 100)
                if type(velocity) is not int or not 1 <= velocity <= 127:
                    return self.send({'error': 'Pad velocity must be an integer from 1 to 127.'}, status=400)
                runner.do(lambda now: board.press(x, y, velocity, now) if ask.get('down', True) else board.release(x, y, now))
                return self.send({})
            if what == ['do']:
                ask = json.loads(body)
                runner.do(lambda now: board.open(ask['open']) if 'open' in ask else board.set_power(ask['power']) if 'power' in ask
                          else board.act(ask['action'], now))
                return self.send(self.state())
            if what == ['config'] and method == 'PUT':
                settings = json.loads(body)
                expected = self.headers.get('If-Match')
                if expected is None:
                    return self.send({'error': 'Read the current settings and include their config_version in If-Match.'}, status=428)

                def apply(now):
                    if expected != f'"{board.config_version}"':
                        return None
                    if not isinstance(settings, dict):
                        raise ValueError('Expected a settings table.')
                    if board.management_settings_changed(settings) and (denied := self.login_needed(management=True)):
                        return denied
                    board.from_page(settings)
                    return board.state() | {'simulator': runner.simulator, 'requires_owner': self.requires_owner(),
                                            'is_admin': self.admin, 'secure_port': self.server.secure_port}

                state = runner.do(apply)
                if state is None:
                    return self.send({'error': 'Settings changed elsewhere. Check the latest settings and try again.'}, status=412)
                if state.get('login'):
                    return self.send(state, status=401)
                return self.send(state)
            if what[:1] == ['plugin'] and len(what) == 3:
                run = board.running.get(what[1])
                if method not in ('GET', 'POST') or method == 'GET' and what[2] not in getattr(run and run.instance, 'read_requests', ()):
                    return self.send({'error': 'Use POST for this plugin operation.'}, status=405)
                if run and what[2] in getattr(run.instance, 'board_requests', ()):
                    def request_on_board(now):
                        board.now = now
                        return board.request(what[1], what[2], params, body)
                    answer = runner.do(request_on_board)
                else:
                    answer = board.request(what[1], what[2], params, body,
                                           on_board=lambda finish: runner.do(lambda now: finish()))
                if isinstance(answer, tuple):
                    return self.send(answer[1], answer[0])
                return self.send(answer if answer is not None else {'error': 'That plugin is not running.'})
            if what == ['plugins'] and method == 'POST':
                return self.send(runner.do(lambda now: board.install(body)) or self.state())
            if what[:1] == ['plugins'] and len(what) == 2 and method == 'DELETE':
                return self.send(runner.do(lambda now: board.remove(what[1], params.get('data') == 'delete')) or self.state())
            if what[:1] == ['sound'] and len(what) == 2:
                clip = getattr(board.sound, 'files', {}).get(int(what[1]))
                return self.send(clip.read_bytes(), 'audio/wav') if clip else self.send({'error': 'Gone.'}, status=404)
            self.send({'error': 'Not here.'}, status=404)
        except (KeyError, ValueError, TypeError) as error:
            self.send({'error': f'The board did not understand that ({type(error).__name__}: {error}).'}, status=400)

    def login(self, body):
        if not self.server.login_gate.acquire(blocking=False):
            return self.send({'error': 'Another sign-in is in progress. Try again shortly.'}, status=429)
        try:
            now = time.monotonic()
            recent = self.server.failures
            while recent and recent[0][0] <= now - 60:
                recent.popleft()
            peer = self.client_address[0]
            if len(recent) >= 20 or sum(ip == peer for _, ip in recent) >= 5:
                return self.send({'error': 'Too many sign-in attempts. Try again in one minute.'}, status=429)
            ask = json.loads(body)
            if not isinstance(ask, dict) or not isinstance(ask.get('password'), str):
                return self.send({'error': 'Enter a password.'}, status=400)
            verifier = config.owner(self.server.runner.board.home)
            if not config.matches(verifier, ask['password']):
                recent.append((now, peer))
                return self.send({'error': 'That is not the password.'}, status=401)
            self.start_session(verifier)
        finally:
            self.server.login_gate.release()

    def start_session(self, verifier, state=None):
        sessions = self.server.sessions
        with self.server.session_lock:
            if len(sessions) >= 128:
                sessions.pop(next(iter(sessions)))
            token = secrets.token_urlsafe()
            sessions[self.tls(), token] = verifier
        self.send(state or {}, cookie=token)

    def stream(self):
        """Limit LED-only updates to 25 per second. A sound wakes the stream immediately."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        runner = self.server.runner
        seen, last_sound = None, 0
        while runner.running:
            seen, news = runner.live(seen, last_sound)
            if self.verifier != config.owner(self.server.runner.board.home):
                return
            if news['sounds']:
                news['sound_time'] = time.monotonic()
            try:
                self.wfile.write(b'data: ' + json.dumps(news).encode() + b'\n\n')
                self.wfile.flush()
            except OSError:  # The browser is gone, whichever way the network puts it (a phone out of reach is "no route").
                return
            if news['sounds']:
                last_sound = news['sounds'][-1]['n']
            with runner.news:
                runner.news.wait_for(lambda: getattr(runner.board.sound, 'n', 0) != last_sound, timeout=.04)


class Server(ThreadingHTTPServer):
    def __init__(self, *args):
        self.workers = threading.BoundedSemaphore(24)
        super().__init__(*args)

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        request.settimeout(15)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.workers.release()


class SecureServer(Server):
    def handle_error(self, request, client_address):
        # A browser that does not trust the board's own certificate yet hangs up in the middle of the greeting.
        if not isinstance(sys.exc_info()[1], OSError):
            super().handle_error(request, client_address)


def serve(runner, host, port, *, require_owner=False, secure_port=None, certificate=None):
    """The web server, in threads of its own; returns it (its `server_address` says where it listens). With a
    certificate and its key, the same board also answers on `secure_port`, as `.secure`."""
    server = Server((host, port), Handler)
    servers, server.secure = [server], None
    if certificate and secure_port is not None:
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*certificate)
            server.secure = SecureServer((host, secure_port), Handler)
            # (the greeting is left to each visitor's own thread: one that stalls holds nobody else up)
            server.secure.socket = context.wrap_socket(server.secure.socket, server_side=True, do_handshake_on_connect=False)
            servers.append(server.secure)
        except OSError:  # (the port is taken, or the certificate cannot be read: the board runs without its secure address)
            server.secure = None
    sessions = {}  # (who has logged in, until a restart)
    upload, login_gate, failures, session_lock = threading.BoundedSemaphore(1), threading.Lock(), deque(), threading.Lock()
    for one in servers:
        one.upload, one.login_gate, one.failures, one.session_lock = upload, login_gate, failures, session_lock
        one.requires_owner, one.secure_port = require_owner, server.secure and server.secure.server_address[1]
        one.daemon_threads, one.runner, one.sessions = True, runner, sessions
        threading.Thread(target=one.serve_forever, name='web', daemon=True).start()
    return server
