"""The board also answers on a secure address, with a certificate it made itself: a browser only lends its
microphone to a page opened that way."""
import json
import ssl
import unittest
import urllib.error
import urllib.request

from padcade import config
from padcade.https import certificate
from padcade.web import BrowserSound, Runner, serve
from tests.kit import Home, ship

NAVIGATION = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}


class Secure(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'soundboard')
        self.home.sound = BrowserSound()
        self.runner = Runner(self.home.board()).start()
        self.addCleanup(self.runner.stop)
        self.files = certificate(self.home.path)
        self.server = serve(self.runner, '127.0.0.1', 0, secure_port=0, certificate=self.files)
        for server in (self.server, self.server.secure):
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.port = self.server.secure.server_address[1]
        # A browser that has been told to trust this board's own certificate, and checks the name in it.
        self.trusting = ssl.create_default_context(cafile=str(self.files[0]))

    def ask(self, path, data=None, headers={}):
        request = urllib.request.Request(f'https://localhost:{self.port}{path}', data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5, context=self.trusting) as answer:
                return answer.status, answer.headers['Content-Type'], answer.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.headers['Content-Type'], error.read()

    def test_the_board_answers_securely_under_its_own_name_and_says_where(self):
        status, _, body = self.ask('/api/state')
        self.assertEqual((status, json.loads(body)['secure_port']), (200, self.port))
        plain = urllib.request.urlopen('http://127.0.0.1:%d/api/state' % self.server.server_address[1], timeout=5)
        with plain:
            self.assertEqual(json.load(plain)['secure_port'], self.port)  # (the plain page learns where to send someone)

    def test_management_sign_in_uses_https_while_the_instrument_stays_open(self):
        config.set_password(self.home.path, 'manager', admin_only=True)
        self.assertFalse(json.loads(self.ask('/api/state')[2])['locked'])
        self.assertEqual(self.ask('/api/restart', b'{}')[0], 401)
        request = urllib.request.Request(f'https://localhost:{self.port}/api/login', data=b'{"password":"manager"}')
        with urllib.request.urlopen(request, context=self.trusting, timeout=5) as reply:
            cookie = reply.headers['Set-Cookie']
        self.assertIn('Secure', cookie.split('; '))
        headers = {'Cookie': cookie.split(';')[0]}
        self.assertEqual(self.ask('/api/restart', b'{}', headers)[0], 200)
        # Changing to the public HTTP board opens the ordinary instrument.
        plain = 'http://127.0.0.1:%d/api/state' % self.server.server_port
        with urllib.request.urlopen(urllib.request.Request(plain, headers=headers), timeout=5) as reply:
            self.assertFalse(json.load(reply)['is_admin'])

    def test_the_certificate_is_kept_and_its_key_is_the_owners_alone(self):
        cert, key = self.files
        self.assertEqual(certificate(self.home.path), (cert, key))
        self.assertEqual(key.stat().st_mode & 0o077, 0)

    def test_a_link_from_the_plain_page_opens_the_board_here_even_before_signing_in(self):
        config.set_password(self.home.path, 'a password')
        status, kind, body = self.ask('/', headers=NAVIGATION)
        self.assertEqual((status, kind.split(';')[0]), (200, 'text/html'))
        self.assertIn(b'<title>', body)
        # ...but nothing is taken from someone who has not signed in.
        status, _, body = self.ask('/api/plugin/soundboard/upload?name=Mine.wav', data=b'RIFF')
        self.assertEqual((status, json.loads(body).get('login')), (401, True))


if __name__ == '__main__':
    unittest.main()
