"""Starting the simulator with a new home directory or one from an earlier version."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from padcade.__main__ import main


class Simulator(unittest.TestCase):
    def start(self, home, commands=()):
        process = subprocess.Popen([sys.executable, '-m', 'padcade', '--simulator', '--port', '0'],
                                   env=os.environ | {'HOME': str(home), 'XDG_DATA_HOME': str(home / 'app-data')},
                                   stdout=subprocess.PIPE, text=True)
        try:
            address = process.stdout.readline().strip().split()[-1]
            for command in commands:
                req = urllib.request.Request(address + 'api/do', data=json.dumps(command).encode(),
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    self.assertEqual(response.status, 200)
            with urllib.request.urlopen(address + 'api/state', timeout=5) as response:
                return json.load(response)
        finally:
            process.terminate()
            process.wait(5)
            process.stdout.close()

    def test_pairing_under_sudo_explains_which_account_to_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (patch.object(sys, 'argv', ['padcade', '--home', tmp, '--set-password']),
                  patch.dict(os.environ, {'SUDO_USER': 'listener'}), patch('os.geteuid', return_value=0),
                  patch('sys.stderr') as error):
                with self.assertRaises(SystemExit):
                    main()
            self.assertIn('service user', ''.join(call.args[0] for call in error.write.call_args_list))
            self.assertFalse((Path(tmp) / 'owner.toml').exists())

    def test_service_restart_keeps_the_take_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / 'app-data/padcade/data/soundboard/clips').mkdir(parents=True)
            open_board = {'open': 'soundboard'}
            first = self.start(home, [open_board, {'action':'performance'}])
            self.assertTrue(first['plugins']['soundboard']['state']['studio']['performance']['recording'])
            reopened = self.start(home, [open_board])
            saved = reopened['plugins']['soundboard']['state']['studio']['performance']['items']
            self.assertEqual([item['title'] for item in saved], ['Take 1'])

    def test_a_new_install_keeps_its_data_outside_the_source_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / 'padcade').mkdir()
            self.start(home)
            self.assertTrue((home / 'app-data/padcade/config.toml').is_file())
            self.assertFalse((home / 'padcade/config.toml').exists())

    def test_the_existing_default_home_keeps_its_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = home / 'padcade'
            old.mkdir()
            (home / 'app-data/padcade/plugins').mkdir(parents=True)
            (old / 'config.toml').write_text('brightness = 46\n')
            self.assertEqual(self.start(home)['config']['brightness'], 46)
