"""The management page's side of the board: what a browser can ask of it."""
import io
import json
import socket
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch

from padcade import NAME, config
from padcade.web import BrowserSound, Runner, serve
from tests.kit import PAGE, Home, note, ship


class Served(unittest.TestCase):
    """A board with its web server, and a browser's way of asking it."""
    def setUp(self):
        self.home = Home(self)
        self.home.plugin('red', button='top1')
        self.home.plugin('blue', button='top2', colour=(0, 0, 200))
        ship(self.home, 'soundboard')
        note(self.home.path / 'data/soundboard/clips/a.wav')
        self.home.sound = BrowserSound()
        self.runner = Runner(self.home.board()).start()
        self.addCleanup(self.runner.stop)
        self.server = serve(self.runner, '127.0.0.1', 0)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]

    def ask(self, path, data=None, method=None, headers={}, raw=False):
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data).encode()
        if path == '/api/config' and hasattr(self, 'version'):
            headers = {'If-Match': '"' + self.version + '"'} | headers
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as answer:
                body = answer.read()
                if raw:
                    return answer.headers['Content-Type'], body
                result = json.loads(body)
                if isinstance(result, dict) and 'config_version' in result:
                    self.version = result['config_version']
                return result
        except urllib.error.HTTPError as error:
            with error:
                return {'status': error.code} | json.loads(error.read())

    def enter(self, path, data, headers=None):
        request = urllib.request.Request(self.url + path, data=json.dumps(data).encode(), method='POST', headers=headers or {})
        with urllib.request.urlopen(request, timeout=5) as answer:
            return {'Cookie': answer.headers['Set-Cookie'].split(';')[0]}

    def live(self, wanted=lambda news: True):
        with urllib.request.urlopen(self.url + '/api/live', timeout=5) as stream:
            for line in stream:
                if line.startswith(b'data: ') and wanted(news := json.loads(line[6:])):
                    return news


class Web(Served):
    def test_an_operation_that_has_started_reports_its_result_even_when_slow(self):
        def finish(now):
            time.sleep(.06)
            return 'applied'

        self.assertEqual(self.runner.do(finish, timeout=.02), 'applied')

    def test_live_refresh_can_skip_other_pages_catalogs_until_the_page_changes(self):
        self.ask('/api/do', {'open': 'red'})
        full = self.ask('/api/state')
        self.assertTrue(full['plugins']['soundboard']['items'])
        lean = self.ask('/api/state?catalog_page=red')
        self.assertEqual(lean['plugins']['soundboard']['items'], [])
        self.ask('/api/do', {'open': 'soundboard'})
        switched = self.ask('/api/state?catalog_page=red')
        self.assertEqual(switched['plugins']['soundboard']['items'], full['plugins']['soundboard']['items'])

    def test_refresh_reuses_settings_until_an_edit(self):
        state = self.ask('/api/state')
        path = '/api/state?config_version=' + state['config_version']
        self.assertNotIn('config', self.ask(path))
        state['config']['brightness'] = 73
        self.ask('/api/config', state['config'], method='PUT')
        self.assertEqual(self.ask(path)['config']['brightness'], 73)

    def test_refresh_reuses_the_clip_list_until_the_library_changes(self):
        self.ask('/api/do', {'open': 'soundboard'})
        path = '/api/state?catalog_page=soundboard'
        view = self.ask(path)['plugins']['soundboard']
        path += '&catalog_version=' + view['state']['catalog_version']
        self.assertNotIn('items', self.ask(path)['plugins']['soundboard'])
        note(self.home.path / 'data/soundboard/clips/New clip.wav')
        view = self.ask(path)['plugins']['soundboard']
        self.assertIn('New clip', [clip['id'] for clip in view['items']])

    def lan(self):
        """A board on the network, asked as from another device in the home: under its name, not as this computer."""
        self.runner.simulator = False
        server = serve(self.runner, '0.0.0.0', 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f'http://127.0.0.1:{server.server_port}'
        return {'Host': socket.gethostname() + '.local'}

    def test_a_new_board_is_managed_from_the_home_network_until_its_owner_sets_a_password_there(self):
        lan = self.lan()
        state = self.ask('/api/state', headers=lan)
        self.assertTrue(state['is_admin'])
        self.assertEqual(self.ask('/api/do', {'open': 'blue'}, headers=lan)['page'], 'blue')
        saved = self.ask('/api/config', state['config'] | {'mqtt': {'host': 'test.invalid'}}, method='PUT', headers=lan)
        self.assertEqual(saved['config']['mqtt']['host'], 'test.invalid')
        self.assertNotIn('login', self.ask('/api/plugins', b'not a zip', headers=lan))
        owner = self.enter('/api/password', {'password': 'set on the page'}, lan)
        self.assertEqual(self.ask('/api/state', headers=lan)['status'], 401)
        self.assertTrue(self.ask('/api/state', headers=lan | owner)['locked'])
        again = self.enter('/api/login', {'password': 'set on the page'}, lan)  # (signing in needs no secure address at home)
        self.assertTrue(self.ask('/api/state', headers=lan | again)['is_admin'])

    def test_an_owner_password_can_leave_playing_open_and_is_removed_from_the_page_again(self):
        lan = self.lan()
        owner = self.enter('/api/password', {'password': 'mine'}, lan)
        old_tab = self.ask('/api/password', {'password': ''}, headers=lan | owner)
        self.assertIn('refresh', old_tab.get('error', '').lower())
        self.assertTrue(self.ask('/api/state', headers=lan | owner)['locked'])
        for _ in range(2):  # (open for playing, the password stays: also when the button is pressed twice)
            owner = self.enter('/api/password', {'password': '', 'open': True}, lan | owner)
        state = self.ask('/api/state', headers=lan)
        self.assertFalse(state['locked'] or state['is_admin'])
        self.assertTrue(state['password'])
        self.assertTrue(self.ask('/api/plugins', b'', headers=lan)['management'])
        self.assertTrue(self.ask('/api/password', {'password': 'new'}, headers=lan)['management'])
        change = state['config'] | {'mqtt': {'host': 'test.invalid'}}
        self.assertTrue(self.ask('/api/config', change, method='PUT', headers=lan)['management'])
        self.assertEqual(self.ask('/api/config', state['config'] | {'brightness': 55}, method='PUT', headers=lan)['config']['brightness'], 55)
        self.assertEqual(self.ask('/api/do', {'open': 'blue'}, headers=lan)['page'], 'blue')
        owner = self.enter('/api/password', {'password': 'changed'}, lan | owner)
        self.assertFalse(self.ask('/api/state', headers=lan)['locked'])  # (a new password guards what the old one did)
        self.enter('/api/password', {'password': '', 'remove': True}, lan | owner)
        state = self.ask('/api/state', headers=lan)
        self.assertTrue(state['is_admin'])
        self.assertFalse(state['password'])

    def test_saving_a_broker_on_a_running_board_reports_that_a_restart_is_needed(self):
        from padcade.device import home_assistant_device
        with home_assistant_device(self.runner.board, self.runner):
            settings = self.ask('/api/state')['config']
            saved = self.ask('/api/config', settings | {'mqtt': {'host': 'broker.test'}}, method='PUT')
            self.assertIn('Restart', saved['mqtt_status'])
            self.assertEqual(self.ask('/api/state')['mqtt_status'], saved['mqtt_status'])

    def test_unreadable_owner_credentials_offer_local_recovery(self):
        owner_file = self.home.path / 'owner.toml'
        owner_file.mkdir()  # Unreadable as a credential file, including on CI running as root.
        answer = self.ask('/api/state')
        self.assertTrue(answer['setup'])
        self.assertIn('error', answer)
        owner_file.rmdir()
        config.set_password(self.home.path, 'recovered locally')
        cookie = self.enter('/api/login', {'password': 'recovered locally'})
        self.assertTrue(self.ask('/api/state', headers=cookie)['locked'])

    def test_a_simulator_configured_for_a_proxy_requires_local_pairing(self):
        server = serve(self.runner, '127.0.0.1', 0, require_owner=True)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f'http://127.0.0.1:{server.server_port}'
        self.assertTrue(self.ask('/api/state')['setup'])
        config.set_password(self.home.path, 'set locally')
        self.assertTrue(self.ask('/api/login', {'password': 'set locally'})['https_needed'])
        proxy = {'X-Forwarded-Proto': 'https'}
        owner = self.enter('/api/login', {'password': 'set locally'}, proxy)
        self.assertTrue(self.ask('/api/state', headers=proxy | owner)['requires_owner'])

    def test_upgrading_a_locked_board_preserves_the_owners_login(self):
        settings = self.ask('/api/state')['config']
        config.set_password(self.home.path, 'from before rollback')
        settings['login'] = 'pbkdf2$6c65676163792066697874757265$c48453788c08269aac9ab063907d6ad4cc602902e39048ab66dbf3e085a7dee3'
        config.save(self.home.path / 'config.toml', settings)
        upgraded = self.home.board()
        # The old release reads this public field if service startup fails and the installer rolls it back.
        self.assertEqual(config.load(self.home.path / 'config.toml').get('login'), settings['login'])
        again = Runner(upgraded).start()
        self.addCleanup(again.stop)
        server = serve(again, '127.0.0.1', 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = 'http://127.0.0.1:%d' % server.server_port
        self.assertEqual(self.ask('/api/state')['status'], 401)
        cookie = self.enter('/api/login', {'password': 'old password'})
        self.assertTrue(self.ask('/api/state', headers=cookie)['locked'])
        self.assertNotIn('login', config.load(self.home.path / 'config.toml'))

    def test_a_plugin_may_bring_frontend_assets_alongside_its_panel(self):
        (self.home.shipped / 'red' / 'panel.js').write_text('export default kit => ({})')
        state = self.ask('/api/state')
        self.assertTrue(state['plugins']['red']['panel'])
        self.assertIsNone(state['plugins']['blue']['panel'])
        kind, body = self.ask('/plugins/red/panel.js', raw=True)
        self.assertEqual((kind.split(';')[0], body), ('text/javascript', b'export default kit => ({})'))
        for name, mime in [('panel.css', 'text/css'), ('helpers.js', 'text/javascript')]:
            (self.home.shipped / 'red' / name).write_text('/* panel asset */')
            kind, body = self.ask('/plugins/red/' + name, raw=True)
            self.assertEqual((kind.split(';')[0], body), (mime, b'/* panel asset */'))
        self.assertEqual(self.ask('/plugins/blue/panel.js')['status'], 404)
        self.assertEqual(self.ask('/plugins/red/plugin.py')['status'], 404)

    def test_config_edit_based_on_an_old_version_keeps_the_newer_change(self):
        self.assertEqual(self.ask('/api/config', {}, method='PUT').get('status'), 428)
        original = self.ask('/api/state')
        headers = {'If-Match': '"' + original['config_version'] + '"'}
        self.ask('/api/config', original['config'] | {'sleep': 10}, method='PUT', headers=headers)
        rejected = self.ask('/api/config', original['config'] | {'brightness': 50}, method='PUT', headers=headers)
        self.assertEqual(rejected.get('status'), 412)
        self.assertEqual(self.ask('/api/state')['config']['sleep'], 10)

    def test_read_requests_leave_saved_clips_and_calibration_alone(self):
        self.assertEqual(self.ask('/api/plugin/soundboard/delete?id=a').get('status'), 405)
        self.assertEqual(self.ask('/api/plugin/soundboard/clip?id=a', raw=True)[0], 'audio/wav')
        self.assertEqual(self.ask('/api/flat').get('status'), 405)

    def test_password_forms_have_a_smaller_request_limit_than_media_uploads(self):
        self.assertEqual(self.ask('/api/login', {'password': 'a' * 10000}).get('status'), 413)

    def test_the_page_is_served_and_says_what_the_board_is_called(self):
        kind, page = self.ask('/', raw=True)
        self.assertIn('text/html', kind)
        self.assertIn(f'<title>{NAME}</title>'.encode(), page)
        self.assertEqual(self.ask('/api/state')['name'], NAME)
        self.assertTrue(self.ask('/api/state')['simulator'])

    def test_presses_on_the_page_are_presses_on_the_board_and_the_live_picture_follows(self):
        self.assertEqual(self.ask('/api/state')['page'], 'red')
        before = self.live()['frame']
        self.assertEqual(len(before), 600)  # (100 colours)
        self.ask('/api/press', {'x': 2, 'y': 9})
        self.assertEqual(self.ask('/api/state')['page'], 'blue')
        self.assertNotEqual(self.live()['frame'], before)
        self.ask('/api/press', {'x': 3, 'y': 3})
        self.ask('/api/press', {'x': 3, 'y': 3, 'down': False})
        self.assertEqual(self.ask('/api/state')['plugins']['blue']['state']['seen'], [['press', 2, 2], ['release', 2, 2]])

    def test_a_factory_reset_puts_everything_back_and_keeps_the_owners_files_unless_told_to_delete_them(self):
        own = self.home.plugin('mine', own=True, button='top5')
        self.runner.do(lambda now: self.runner.board.reload())
        cookie = self.enter('/api/password', {'password': 'secret'})
        self.assertEqual(self.ask('/api/state')['status'], 401)
        state = self.ask('/api/config', self.ask('/api/state', headers=cookie)['config'] | {'brightness': 40, 'buttons': {'left1': 'power'}},
                         method='PUT', headers=cookie)
        self.assertEqual(state['config']['brightness'], 40)
        state = self.ask('/api/reset', {}, headers=cookie)
        self.assertEqual((state['locked'], state['config']['brightness'] == 40, state['config']['buttons'].get('top8')), (False, False, 'power'))
        self.assertTrue(own.exists() and (self.home.path / 'data/soundboard/clips/a.wav').exists())  # (what the owner added stays)
        state = self.ask('/api/reset?files=delete', {})
        self.assertFalse(own.exists() or (self.home.path / 'data/soundboard/clips/a.wav').exists())
        self.assertNotIn('mine', state['plugins'])
        # Stop the fresh Soundboard while its temporary home still exists.
        state['config']['plugins']['soundboard']['enabled'] = False
        self.ask('/api/config', state['config'], method='PUT')

    def test_settings_saved_on_the_page_are_in_the_config_file(self):
        settings = self.ask('/api/state')['config']
        settings['buttons'] = {'left1': 'open:blue'}
        state = self.ask('/api/config', settings, method='PUT')
        self.assertEqual(state['config']['buttons'], {'left1': 'open:blue'})
        self.assertEqual(config.load(self.home.path / 'config.toml')['buttons'], {'left1': 'open:blue'})

    def test_a_secret_setting_reaches_its_plugin_but_never_comes_back_to_the_page(self):
        self.home.plugin('keeper', button='top5', settings={'token': {'label': 'Token', 'type': 'secret'}}, code='''
            class Plugin:
                def __init__(self, board):
                    self.board = board
                def frame(self, now):
                    return [(1, 1, 1)] * 64
                def state(self):
                    return {'length': len(self.board.settings['token'] or '')}
        ''')
        self.ask('/api/plugins', b'', method='POST')  # (not a zip: refused; the folders are read again below)
        self.runner.do(lambda now: self.runner.board.reload())
        settings = self.ask('/api/state')['config']
        settings['plugins']['keeper']['settings'] = {'token': 'abcdef'}
        state = self.ask('/api/config', settings, method='PUT')
        self.assertEqual(state['plugins']['keeper']['state'], {'length': 6})
        self.assertNotIn('abcdef', json.dumps(state))
        self.assertEqual(state['plugins']['keeper']['secrets'], ['token'])
        state = self.ask('/api/config', state['config'] | {'brightness': 40}, method='PUT')  # (saved again without it: kept)
        self.assertEqual(state['plugins']['keeper']['state'], {'length': 6})
        state['config']['plugins']['keeper']['settings'] = {'token': ''}  # (emptied on the page: gone)
        self.assertEqual(self.ask('/api/config', state['config'], method='PUT')['plugins']['keeper']['state'], {'length': 0})

    def test_settings_from_a_missing_manifest_are_private_and_survive_a_layout_save(self):
        settings = self.ask('/api/state')['config']
        settings['plugins']['retired'] = {'settings': {'secret': 'private value'}}
        settings['plugins']['red']['settings'] = {'retired_option': 'previous secret'}
        self.runner.do(lambda now: self.runner.board.configure(settings))
        shown = self.ask('/api/state')['config']
        self.assertNotIn('retired', shown['plugins'])
        self.assertNotIn('previous secret', json.dumps(shown))
        self.ask('/api/config', shown | {'brightness': 64}, method='PUT')
        stored = config.load(self.home.path / 'config.toml')
        self.assertEqual(stored['plugins']['retired']['settings']['secret'], 'private value')
        self.assertEqual(stored['plugins']['red']['settings']['retired_option'], 'previous secret')

    def test_changing_a_server_requires_a_new_credential(self):
        ship(self.home, 'home_assistant')
        self.runner.do(lambda now: self.runner.board.reload())
        state = self.ask('/api/state')['config']
        state['mqtt'] = {'host': 'first.test', 'username': 'board', 'password': 'one'}
        state['plugins']['home_assistant']['settings'] = {'address': 'http://first.test', 'token': 'two'}
        saved = self.ask('/api/config', state, method='PUT')
        self.assertTrue(saved['broker_password'] and saved['plugins']['home_assistant']['secrets'])
        changed = saved['config']
        changed['mqtt']['host'] = 'second.test'
        changed['plugins']['home_assistant']['settings']['address'] = 'http://second.test'
        saved = self.ask('/api/config', changed, method='PUT')
        self.assertFalse(saved['broker_password'])
        self.assertFalse(saved['plugins']['home_assistant']['secrets'])

    def test_the_page_can_open_a_page_and_trigger_its_actions(self):
        self.assertEqual(self.ask('/api/do', {'open': 'soundboard'})['page'], 'soundboard')
        self.assertEqual(self.ask('/api/do', {'action': 'bank:1'})['plugins']['soundboard']['state']['bank'], 1)

    def test_settings_only_offers_a_release_newer_than_the_installed_version(self):
        with patch('padcade.web.VERSION', '0.10.0-rc.2'):
            for tag, expected in (('0.9.9', False), ('0.10.0-rc.1', False), ('0.10.0-rc.10', True),
                                  ('0.10.0', True), ('0.10.0-rc.2+build', False), (None, False)):
                with self.subTest(tag=tag), patch('padcade.web.latest_release', return_value=tag):
                    self.assertEqual(self.ask('/api/update')['available'], expected)

    def test_a_simulator_cannot_be_restarted_or_updated_and_says_so(self):
        self.assertIn('error', self.ask('/api/restart', {}))
        self.assertIn('error', self.ask('/api/update', {}))

    def test_another_site_cannot_change_the_board(self):
        answer = self.ask('/api/press', {'x': 2, 'y': 9}, headers={'Origin': 'http://elsewhere.example'})
        self.assertEqual(answer['status'], 403)
        self.assertEqual(self.ask('/api/state')['page'], 'red')
        answer = self.ask('/api/plugin/soundboard/delete?id=a', headers={'Sec-Fetch-Site': 'cross-site'})  # (an <img> there)
        self.assertEqual(answer['status'], 403)
        self.assertEqual([clip['id'] for clip in self.ask('/api/state')['plugins']['soundboard']['items']], ['a'])

    def test_a_request_the_board_cannot_read_gets_an_answer_and_the_board_carries_on(self):
        self.assertEqual(self.ask('/api/press', b'nonsense')['status'], 400)
        self.assertEqual(self.ask('/api/nothing')['status'], 404)
        self.assertEqual(self.ask('/api/state')['page'], 'red')

    def test_a_clip_is_uploaded_and_listened_to_through_the_page(self):
        wav = note(self.home.path / 'x.wav').read_bytes()
        self.assertEqual(self.ask('/api/plugin/soundboard/upload?name=Horn.wav', wav), {'id': 'Horn'})
        self.assertIn('Horn', [clip['id'] for clip in self.ask('/api/state')['plugins']['soundboard']['items']])
        kind, data = self.ask('/api/plugin/soundboard/clip?id=Horn', raw=True)
        self.assertEqual((kind, data[:4]), ('audio/wav', b'RIFF'))

    def test_without_a_speaker_the_page_is_given_the_sounds_to_play(self):
        settings = self.ask('/api/do', {'open': 'soundboard'})['config']
        settings['plugins']['soundboard']['banks'] = [{'name': 'One', 'pads': {'1,1': {'thing': 'a'}}}]
        self.ask('/api/config', settings, method='PUT')
        self.ask('/api/press', {'x': 1, 'y': 1})
        news = self.live(lambda news: news['sounds'])
        kind, data = self.ask('/api/sound/%d' % news['sounds'][-1]['play'], raw=True)
        self.assertEqual((kind, data[:4]), ('audio/wav', b'RIFF'))

    def test_live_frames_deliver_each_audio_event_once_per_connection(self):
        clip = note(self.home.path / 'drum.wav')
        with urllib.request.urlopen(self.url + '/api/live', timeout=5) as stream:
            frames = (json.loads(line[6:]) for line in stream if line.startswith(b'data: '))
            next(frames)
            sounds = (frame['sounds'] for frame in frames if frame['sounds'])
            for _ in range(2):
                voice = self.runner.do(lambda _: self.home.sound.play(clip))
                self.assertEqual([event['play'] for event in next(sounds)], [voice.number])

    def test_simulator_can_damp_one_voice_and_then_silence_it_during_the_tail(self):
        clip = note(self.home.path / 'long.wav', frames=44100)
        first, second = self.home.sound.play(clip), self.home.sound.play(clip)
        first.release(.75)
        first.release(2)
        first.terminate()
        events = self.live(lambda news: any('stop' in e for e in news['sounds']))['sounds']
        starts = [e['play'] for e in events if 'play' in e]
        stops = [e for e in events if 'stop' in e]
        self.assertEqual([e['stop'] for e in stops], [starts[0], starts[0]])
        self.assertEqual([e.get('release', 0) for e in stops], [.75, 0])
        self.assertLessEqual(0, stops[0]['held'])
        self.assertIsNotNone(first.poll())
        self.assertIsNone(second.poll())

    def plugin_zip(self, files):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as z:
            for name, text in files.items():
                z.writestr(name, text)
        return archive.getvalue()

    def test_a_plugin_is_added_from_a_zip_as_the_owners_and_removed_again(self):
        about = 'name = "Green"\nkind = "page"\napi = 1\nbutton = "top5"\n'
        self.ask('/api/plugins', self.plugin_zip({'green/plugin.toml': about, 'green/plugin.py': 'COLOUR = (0, 9, 0)\n' + PAGE,
                                                  '../outside.py': 'x', 'green/../../escape.py': 'x'}))
        state = self.ask('/api/state')
        self.assertTrue(state['plugins']['green']['own'])
        self.assertIsNone(state['plugins']['green']['error'])
        self.assertFalse((self.home.path / 'outside.py').exists() or (self.home.path / 'escape.py').exists())
        self.ask('/api/press', {'x': 5, 'y': 9})
        self.assertEqual(self.ask('/api/state')['page'], 'green')
        self.ask('/api/plugins/green', method='DELETE')
        state = self.ask('/api/state')
        self.assertNotIn('green', state['plugins'])
        self.assertEqual(state['page'], 'red')
        self.assertIn('error', self.ask('/api/plugins/red', method='DELETE'))  # (a shipped one stays)

    def test_a_zip_with_an_unusable_name_or_broken_contents_leaves_the_owners_plugins_alone(self):
        about = 'name = "Green"\nkind = "page"\napi = 1\n'
        self.ask('/api/plugins', self.plugin_zip({'green/plugin.toml': about, 'green/plugin.py': 'COLOUR = (0, 9, 0)\n' + PAGE}))
        mine = self.home.path / 'plugins/green/plugin.py'
        self.assertIn('error', self.ask('/api/plugins', self.plugin_zip({'!!!/plugin.toml': about.replace('Green', '!!!'), '!!!/plugin.py': 'x'})))
        self.assertTrue(mine.exists())
        for replacement in ({}, {'green/plugin.py': 'class Plugin:\n    def __init__(self, board): raise ValueError("bad setup")'}):
            with self.subTest(files=list(replacement)):
                result = self.ask('/api/plugins', self.plugin_zip({'green/plugin.toml': about} | replacement))
                self.assertIn('error', result)
                self.assertIn('green', self.ask('/api/state')['pages'])
        broken = self.plugin_zip({'green/plugin.toml': about, 'green/foo': 'a file', 'green/foo/bar': 'inside a file'})
        self.assertIn('error', self.ask('/api/plugins', broken))
        self.assertTrue(mine.exists())
        self.assertIsNone(self.ask('/api/state')['plugins']['green']['error'])

    def test_a_page_reached_under_a_name_the_board_does_not_know_is_refused(self):
        self.assertEqual(self.ask('/api/state', headers={'Host': 'rebound.example:8080'})['status'], 403)
        self.assertEqual(self.ask('/', headers={'Host': 'rebound.example:8080'})['status'], 403)
        settings = self.ask('/api/state')['config']
        self.ask('/api/config', settings | {'names': ['rebound.example']}, method='PUT')
        kind, page = self.ask('/', headers={'Host': 'rebound.example:8080'}, raw=True)
        self.assertIn('text/html', kind)
        self.assertIn(NAME.encode(), page)

    def test_an_upload_that_lies_about_its_size_is_refused(self):
        self.assertEqual(self.ask('/api/press', b'{}', headers={'Content-Length': '-1'})['status'], 400)

    def test_a_zip_that_is_not_a_plugin_is_refused_with_a_reason(self):
        self.assertIn('error', self.ask('/api/plugins', b'not a zip'))
        self.assertIn('error', self.ask('/api/plugins', self.plugin_zip({'readme.txt': 'hello'})))


class Login(Served):
    """A password for the page, if the owner wants one."""
    def setUp(self):
        super().setUp()
        self.assertFalse(self.ask('/api/state')['locked'])
        self.cookie = self.enter('/api/password', {'password': 'open sesame'})

    def test_with_a_password_the_board_only_answers_who_knows_it(self):
        self.assertEqual(self.ask('/api/state')['status'], 401)
        self.assertEqual(self.ask('/api/press', {'x': 2, 'y': 9})['status'], 401)
        self.assertEqual(self.ask('/api/login', {'password': 'wrong'})['status'], 401)
        kind, page = self.ask('/', raw=True)  # (the page itself comes: it asks for the password)
        self.assertIn('text/html', kind)
        mine = self.enter('/api/login', {'password': 'open sesame'})
        state = self.ask('/api/state', headers=mine)
        self.assertTrue(state['locked'])
        self.assertEqual(state['page'], 'red')

    def test_rotating_a_password_ends_a_viewers_live_stream(self):
        request = urllib.request.Request(self.url + '/api/live', headers=self.cookie)
        with urllib.request.urlopen(request, timeout=3) as stream:
            self.assertTrue(stream.readline().startswith(b'data: '))
            new_cookie = self.enter('/api/password', {'password': 'another password'}, self.cookie)
            self.ask('/api/do', {'open': 'blue'}, headers=new_cookie)
            for line in stream:
                if line.startswith(b'data: '):
                    self.fail('The previous viewer received another frame after rotation')

    def test_the_password_itself_is_neither_in_the_file_nor_sent_to_the_page(self):
        self.assertNotIn('open sesame', (self.home.path / 'config.toml').read_text())
        self.assertNotIn('login', self.ask('/api/state', headers=self.cookie)['config'])

    def test_saving_settings_on_the_page_keeps_the_password_and_removing_it_opens_the_board(self):
        settings = self.ask('/api/state', headers=self.cookie)['config']
        self.ask('/api/config', settings | {'brightness': 50}, method='PUT', headers=self.cookie)
        self.assertEqual(self.ask('/api/state')['status'], 401)
        self.ask('/api/password', {'password': '', 'remove': True}, headers=self.cookie)
        self.assertEqual(self.ask('/api/state')['config']['brightness'], 50)

    def test_the_password_survives_damaged_settings_and_old_logins_do_not(self):
        (self.home.path / "config.toml").write_text("bad = [")
        again = Runner(self.home.board()).start()
        self.addCleanup(again.stop)
        server = serve(again, '127.0.0.1', 0)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.url = 'http://127.0.0.1:%d' % server.server_address[1]
        self.assertEqual(self.ask('/api/state', headers=self.cookie)['status'], 401)
        self.assertEqual(self.ask('/api/state', headers=self.enter('/api/login', {'password': 'open sesame'}))['page'], 'red')
