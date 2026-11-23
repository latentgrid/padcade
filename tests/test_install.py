"""install.sh, run against a folder that stands in for the Pi's file system, with stand-ins for the system's commands."""
import os
import shutil
import subprocess
import tempfile
import tarfile
import unittest
from pathlib import Path

from padcade import SLUG

REPO = Path(__file__).parent.parent
COMMANDS = ('apt-get', 'systemctl', 'usermod', 'dtparam', 'modprobe', 'sync', 'hostname', 'getent')


@unittest.skipUnless(shutil.which('sh'), 'needs a shell')
class Install(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root, self.bin = Path(folder.name) / 'root', Path(folder.name) / 'bin'
        self.log = Path(folder.name) / 'log'
        self.bin.mkdir()
        (self.root / 'boot/firmware').mkdir(parents=True)
        (self.root / 'boot/firmware/config.txt').write_text('dtparam=audio=on\n[all]\n')
        (self.root / 'boot/firmware/cmdline.txt').write_text('console=serial0,115200 console=tty1 root=PARTUUID=1 rootwait\n')
        for name in COMMANDS:
            self.command(name, f'echo "{name} $*" >> "{self.log}"\n')
        self.command('dpkg', 'exit 1\n')  # (no package is installed yet)
        self.command('i2cget', 'exit 1\n')  # (nothing on the I2C bus)
        self.log.write_text('')

    def command(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)

    def install(self, *options, sudo_user=None):
        env = os.environ | {'INSTALL_ROOT': str(self.root), 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                            'SUDO_USER': sudo_user or os.environ.get('USER', 'root')}
        return subprocess.run(['sh', str(REPO / 'install.sh'), *options], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def boot(self):
        return (self.root / 'boot/firmware/config.txt').read_text()

    def test_a_reinstall_by_another_administrator_keeps_the_user_the_board_runs_as(self):
        runs_as = lambda: [line for line in (self.root / 'etc/systemd/system' / f'{SLUG}.service').read_text().splitlines()
                           if line.startswith('User=')]
        self.assertEqual(self.install(sudo_user='first').returncode, 0)
        self.assertEqual(runs_as(), ['User=first'])
        again = self.install(sudo_user='second')  # (its songs and its password are in the first one's folder)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(runs_as(), ['User=first'])
        self.assertEqual(self.install('--user=second', sudo_user='second').returncode, 0)
        self.assertEqual(runs_as(), ['User=second'])

    def test_it_puts_the_program_and_its_service_in_place_and_starts_it_after_the_packages(self):
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((self.root / 'opt' / SLUG / 'padcade/board.py').exists())
        target = self.root / 'opt' / SLUG
        for guide in ('docs/soundboard.md', 'docs/maintenance.md', 'case/README.md'):
            self.assertTrue((target / guide).is_file(), guide)
        self.assertEqual((target / 'LICENSE-sounds').read_bytes(), (REPO / 'LICENSE-sounds').read_bytes())
        unit = (self.root / 'etc/systemd/system' / f'{SLUG}.service').read_text()
        self.assertIn('-m padcade', unit)
        self.assertIn('--port 80', unit)
        log = self.log.read_text()
        self.assertLess(log.index('apt-get install'), log.index(f'systemctl restart {SLUG}'))
        self.assertIn('poweroff', (self.root / 'etc/sudoers.d' / SLUG).read_text())
        self.assertEqual(self.boot(), 'dtparam=audio=on\n[all]\n')  # (no parts said or found: the boot options untouched)

    def test_it_leaves_nothing_of_its_own_in_the_folder_it_was_unpacked_into(self):
        # (it runs as root: anything it left there could not be deleted by the person who downloaded it)
        unpacked = self.root.parent / 'padcade-main'
        for name in ('padcade', 'docs', 'case', 'install.sh', 'README.md', 'LICENSE', 'LICENSE-assets', 'LICENSE-sounds'):
            source = REPO / name
            if source.is_dir():
                shutil.copytree(source, unpacked / name, ignore=shutil.ignore_patterns('__pycache__', '*.so'))
            else:
                unpacked.mkdir(exist_ok=True)
                shutil.copy(source, unpacked / name)
        before = sorted(p.relative_to(unpacked) for p in unpacked.rglob('*'))
        env = os.environ | {'INSTALL_ROOT': str(self.root), 'PATH': f'{self.bin}:{os.environ["PATH"]}', 'SUDO_USER': os.environ.get('USER', 'root')}
        env.pop('PYTHONDONTWRITEBYTECODE', None)  # (as on a Pi, where nothing tells Python to keep its cache to itself)
        done = subprocess.run(['sh', str(unpacked / 'install.sh')], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(p.relative_to(unpacked) for p in unpacked.rglob('*')), before)

    def test_packages_that_cannot_be_installed_stop_it_before_anything_is_replaced(self):
        self.command('apt-get', 'exit 1\n')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse((self.root / 'opt' / SLUG).exists())
        self.assertNotIn('systemctl restart', self.log.read_text())

    def test_a_release_that_fails_to_start_restores_the_previous_installation(self):
        self.assertEqual(self.install('--motion').returncode, 0)
        original = self.root / 'opt' / SLUG / 'previous-release'
        original.write_text('known good')
        unit = self.root / 'etc/systemd/system' / f'{SLUG}.service'
        unit.write_text('[Service]\nType=simple\n')  # older releases did not send readiness
        self.command('systemctl', f'''if [ "$1" = restart ]; then
    [ -f "{original}" ] || exit 1
    touch "{self.log}.restored"
fi
''')
        done = self.install('--no-motion', '--speaker')
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue(original.exists())
        self.assertEqual(unit.read_text(), '[Service]\nType=simple\n')
        self.assertIn('dtoverlay=miniuart-bt', self.boot())
        self.assertTrue(Path(str(self.log) + '.restored').exists())

    def test_an_unrelated_device_at_the_battery_address_keeps_battery_disabled(self):
        self.command('i2cdetect', 'echo "50: -- -- -- -- -- -- -- 57"\n')
        self.command('i2cget', 'echo 0x00\n')
        self.assertEqual(self.install().returncode, 0)
        self.assertNotIn('i2c_arm=on', self.boot())
        self.assertFalse((self.root / f'etc/modules-load.d/{SLUG}.conf').exists())

    def test_only_the_parts_that_are_there_get_boot_options_and_a_second_run_keeps_them(self):
        self.command('i2cget', 'case "$4" in 0x00) echo 0x03;; 0x01) echo 0x0f;; esac\n')
        done = self.install('--speaker')
        self.assertEqual(done.returncode, 0, done.stderr)
        boot = self.boot()
        self.assertIn('dtparam=i2c_arm=on', boot)
        self.assertIn('dtoverlay=max98357a', boot)
        self.assertNotIn('enable_uart', boot)  # (no motion sensor)
        self.assertIn('restart the Pi', done.stdout)
        self.assertIn('MAX98357A', (self.root / 'etc/asound.conf').read_text())
        self.assertIn('console=serial0', (self.root / 'boot/firmware/cmdline.txt').read_text())
        again = self.install()  # (an update: nothing said about the parts)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.boot(), boot)
        self.assertNotIn('restart the Pi', again.stdout)

    def test_legacy_battery_setup_survives_an_unavailable_i2c_bus_during_upgrade(self):
        parts = self.root / f'etc/{SLUG}.parts'
        modules = parts.parent / f'modules-load.d/{SLUG}.conf'
        modules.parent.mkdir(parents=True)
        modules.write_text('i2c-dev\n')
        parts.write_text('motion=no\nspeaker=no\n')
        self.assertEqual(self.install().returncode, 0)
        self.assertIn('battery=yes', parts.read_text().splitlines())
        self.assertTrue(modules.is_file())

    def test_old_install_detects_the_fitted_battery_once_and_opt_out_survives_upgrades(self):
        parts = self.root / f'etc/{SLUG}.parts'
        parts.parent.mkdir()
        parts.write_text('motion=no\nspeaker=no\n')
        self.command('i2cget', f'echo probe >> "{self.log}"\ncase "$4" in 0x00) echo 0x03;; 0x01) echo 0x0f;; esac\n')
        self.assertEqual(self.install().returncode, 0)
        self.assertIn('battery=yes', parts.read_text().splitlines())
        self.log.write_text('')
        for options in (('--no-battery',), ('--motion',), ()):
            self.assertEqual(self.install(*options).returncode, 0)
            self.assertIn('battery=no', parts.read_text().splitlines())
        self.assertNotIn('probe', self.log.read_text())
        self.assertNotIn('dtparam=i2c_arm=on', self.boot())
        self.assertFalse((self.root / f'etc/modules-load.d/{SLUG}.conf').exists())

    def test_a_fitted_battery_can_be_configured_even_when_the_bus_is_unavailable_until_reboot(self):
        done = self.install('--battery')
        self.assertEqual(done.returncode, 0, done.stderr)
        for options in ((), ('--speaker',)):
            again = self.install(*options)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn('dtparam=i2c_arm=on', self.boot())
            self.assertIn('i2c-dev', (self.root / f'etc/modules-load.d/{SLUG}.conf').read_text().splitlines())

    def test_changing_just_the_speaker_keeps_motion_and_removes_its_owned_audio_setup(self):
        self.assertEqual(self.install('--speaker', '--motion').returncode, 0)
        self.assertEqual(self.install('--no-speaker').returncode, 0)
        self.assertIn('dtoverlay=miniuart-bt', self.boot())
        self.assertNotIn('dtoverlay=max98357a', self.boot())
        self.assertFalse((self.root / 'etc/asound.conf').exists())
        sound = self.root / 'etc/asound.conf'
        sound.write_text('# my own configuration for padcade\n')
        self.assertEqual(self.install('--no-motion').returncode, 0)
        self.assertEqual(sound.read_text(), '# my own configuration for padcade\n')

    def test_a_failed_motion_install_gives_back_the_running_serial_console(self):
        self.command('systemctl', f'''echo "$*" >> "{self.log}"
case "$1 $2" in
  "is-active --quiet") [ "$3" = "serial-getty@ttyAMA0.service" ] ;;
  "restart {SLUG}") exit 1 ;;
esac
''')
        self.assertNotEqual(self.install('--motion').returncode, 0)
        calls = self.log.read_text().splitlines()
        self.assertIn('start serial-getty@ttyAMA0.service', calls)
        self.assertNotIn('start serial-getty@ttyS0.service', calls)
        self.assertFalse(any(c.startswith('disable ') for c in calls))

    def test_the_motion_sensor_gets_the_uart_to_itself_and_can_be_taken_out_again(self):
        self.install('--motion')
        self.assertIn('enable_uart=1', self.boot())
        self.assertIn('dtoverlay=miniuart-bt', self.boot())
        self.assertNotIn('console=serial0', (self.root / 'boot/firmware/cmdline.txt').read_text())
        self.install('--no-motion')
        self.assertEqual(self.boot(), 'dtparam=audio=on\n[all]\n')

    def test_upgrade_repairs_a_legacy_motion_install_and_gives_the_console_back(self):
        (self.root / 'etc').mkdir()
        (self.root / f'etc/{SLUG}.parts').write_text('motion=yes\nspeaker=no\n')
        cmdline = self.root / 'boot/firmware/cmdline.txt'
        previous = 'rootwait console=serial0,115200n8\n'
        cmdline.write_text(previous)
        (cmdline.parent / f'cmdline.txt.before-{SLUG}').write_text(previous)
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(cmdline.read_text().split(), ['rootwait'])
        self.assertEqual(self.install('--no-motion').returncode, 0)
        self.assertEqual(cmdline.read_text(), previous)

    def test_motion_gives_back_a_trailing_serial_console_and_preserves_later_boot_edits(self):
        cmdline = self.root / 'boot/firmware/cmdline.txt'
        cmdline.write_text('rootwait console=tty1 console=serial0,115200n8\n')
        self.assertEqual(self.install('--motion').returncode, 0)
        self.assertEqual(cmdline.read_text().split(), ['rootwait', 'console=tty1'])
        cmdline.write_text('rootwait console=tty1 quiet\n')
        self.assertEqual(self.install('--no-motion').returncode, 0)
        self.assertIn('console=serial0,115200n8', cmdline.read_text().split())
        self.assertIn('quiet', cmdline.read_text().split())
        self.assertLess(cmdline.read_text().index('console=tty1'), cmdline.read_text().index('console=serial0'))

    def test_options_in_another_models_section_do_not_configure_this_pi(self):
        (self.root / 'boot/firmware/config.txt').write_text('[pi4]\ndtoverlay=max98357a,no-sdmode\n[all]\n')
        self.assertEqual(self.install('--speaker').returncode, 0)
        self.assertIn('dtoverlay=max98357a,no-sdmode', self.boot().split('[all]', 1)[1])

    def test_boot_options_the_owner_already_has_are_not_written_twice_and_their_sound_setup_is_kept(self):
        (self.root / 'boot/firmware/config.txt').write_text('[all]\ndtoverlay=max98357a,no-sdmode\n')
        (self.root / 'etc').mkdir()
        (self.root / 'etc/asound.conf').write_text('# mine\n')
        self.install('--speaker', '--motion')
        self.assertEqual(self.boot().count('dtoverlay=max98357a'), 1)
        self.assertEqual((self.root / 'etc/asound.conf').read_text(), '# mine\n')

    def test_the_boot_files_it_changes_are_kept_as_they_were_beside_them(self):
        self.install('--motion')
        self.install('--no-motion', '--speaker')  # (a second change: the first copies stay)
        self.assertEqual((self.root / f'boot/firmware/config.txt.before-{SLUG}').read_text(), 'dtparam=audio=on\n[all]\n')
        self.assertIn('console=serial0', (self.root / f'boot/firmware/cmdline.txt.before-{SLUG}').read_text())

    def test_it_will_not_set_the_board_up_to_run_as_root(self):
        env = os.environ | {'INSTALL_ROOT': str(self.root), 'PATH': f'{self.bin}:{os.environ["PATH"]}', 'SUDO_USER': 'root'}
        done = subprocess.run(['sh', str(REPO / 'install.sh')], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('--user', done.stdout)
        self.assertFalse((self.root / 'opt' / SLUG).exists())

    def update(self, repo='example/release', version='1.0.0'):
        """What `--update` does in an unpacked release that names `repo` as the place its releases are."""
        release = self.root.parent / 'current'
        (release / 'padcade').mkdir(parents=True)
        (release / 'padcade/__init__.py').write_text(f'SLUG = "padcade"\nVERSION = "{version}"\nREPO = "{repo}"\n')
        shutil.copy2(REPO / 'install.sh', release / 'install.sh')
        shutil.copy2(REPO / 'padcade/release.py', release / 'padcade/release.py')
        self.command('systemd-run', 'while [ "$1" != "--" ]; do shift; done\nshift\nTRANSIENT=yes exec "$@"\n')
        env = os.environ | {'INSTALL_ROOT': str(self.root), 'SUDO_USER': 'pads', 'PATH': f'{self.bin}:{os.environ["PATH"]}'}
        return subprocess.run([str(release / 'install.sh'), '--update'], env=env, capture_output=True, text=True)

    def test_web_update_installs_outside_the_board_service_it_restarts(self):
        upgrade = self.root.parent / 'install.sh'
        upgrade.write_text('#!/bin/sh\nprintf "%s" "$TRANSIENT" > "$INSTALL_ROOT/installed"\n')
        upgrade.chmod(0o755)
        archive = self.root.parent / 'release.tgz'
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(upgrade, arcname='release/install.sh')
        self.command('curl', f'case "$*" in *api.github.com*) echo \'{{"tag_name":"v2.0.0"}}\' ;; *) cp "{archive}" "$4" ;; esac\n')
        done = self.update()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.root / 'installed').read_text(), 'yes')

    def test_latest_release_cannot_downgrade_a_newer_install(self):
        self.command('curl', 'case "$*" in *api.github.com*) echo \'{"tag_name":"v0.9.9"}\' ;; *) exit 23 ;; esac\n')
        done = self.update(version='0.10.0')
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertFalse((self.root / 'installed').exists())

    def test_an_update_needs_to_know_where_the_releases_are(self):
        done = self.update(repo='')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('REPO', done.stdout)

    def test_an_update_says_so_when_no_release_can_be_found(self):
        self.command('curl', 'exit 22\n')  # (no internet, or nothing released yet)
        done = self.update()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('No release', done.stdout)
        self.assertNotIn('Traceback', done.stderr)
