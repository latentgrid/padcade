"""The board's logic, away from the hardware: what its presses mean, what it shows, what a plugin may do to it."""
import math
import unittest
from types import SimpleNamespace

from padcade import config
from tests.kit import BLACK, Home, level, pads

RED, BLUE = (200, 0, 0), (0, 0, 200)


class Pages(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        self.home.plugin('red', button='top1', actions={'beep': 'Beep'}, defaults={'buttons': {'bottom2': 'beep'}})
        self.home.plugin('blue', button='top2', colour=BLUE)
        self.board = self.home.board()

    def seen(self, page):
        return self.board.state()['plugins'][page]['state']['seen']

    def test_factory_reset_clears_runtime_state_and_recovery_files(self):
        new = self.board.state()
        self.board.set_power(False)
        self.board.set_volume(1)
        self.board.open('blue')
        leftovers = ('config.broken', 'state.json', 'config.tmp', 'state.tmp')
        for name in leftovers:
            (self.home.path / name).write_text('previous owner')
        self.board.reset(files=True)
        reset = self.board.state()
        self.assertEqual([reset[k] for k in ('power', 'volume', 'page')], [new[k] for k in ('power', 'volume', 'page')])
        self.assertFalse(any((self.home.path / name).exists() for name in leftovers))

    def test_incomplete_factory_reset_keeps_the_board_usable_and_paired(self):
        self.board.set_password('owner')
        self.board.open('red')
        # This is not a removable plugin directory.
        (self.home.path / '.plugin-rollback').write_text('in use')
        with self.assertRaises(OSError):
            self.board.reset(files=True)
        self.board.open('red')
        self.board.press(1, 1, 127, 1)
        self.assertEqual(self.seen('red'), [['press', 0, 0]])
        self.assertTrue(self.board.state()['locked'])

    def test_the_pads_show_the_open_page_and_its_round_button_opens_another(self):
        b = self.board
        self.assertGreater(pads(b, 0.)[0][0], pads(b, 0.)[0][2])  # (red, the first page)
        b.press(2, 9, 127, 1.)
        self.assertGreater(pads(b, 1.)[0][2], pads(b, 1.)[0][0])
        frame = b.frame(1.)
        self.assertGreater(level(frame[92]), level(frame[91]))  # (the open page's button brighter than the other)

    def test_pad_presses_and_releases_reach_the_open_page_only(self):
        b = self.board
        b.press(1, 1, 127, 0.)
        b.release(1, 1, .1)
        b.press(8, 8, 100, .2)
        self.assertEqual(self.seen('red'), [['press', 0, 0], ['release', 0, 0], ['press', 7, 7]])
        self.assertEqual(self.seen('blue'), [])

    def test_a_round_button_with_one_of_the_pages_actions_triggers_it_there(self):
        b = self.board
        b.press(2, 0, 127, 0.)
        self.assertEqual(self.seen('red'), [['action', 'beep']])
        b.press(2, 9, 127, 1.)  # (blue has nothing on that button)
        b.press(2, 0, 127, 2.)
        self.assertEqual(self.seen('red'), [['action', 'beep']])
        self.assertEqual(self.seen('blue'), [])

    def test_letting_go_of_an_actions_round_button_is_told_to_the_page_too(self):
        b = self.board
        b.press(2, 0, 127, 0.)
        b.release(2, 0, .5)
        b.release(5, 0, .6)  # (a button with nothing on it)
        self.assertEqual(self.seen('red'), [['action', 'beep'], ['released', 'beep']])

    def test_an_action_started_on_the_management_page_is_pressed_and_let_go_at_once(self):
        self.board.act('beep', 1.)
        self.assertEqual(self.seen('red'), [['action', 'beep'], ['released', 'beep']])

    def test_off_is_every_light_dark_and_any_press_only_wakes_it(self):
        b = self.board
        b.press(8, 9, 127, 0.)
        self.assertEqual(b.frame(1.), [BLACK] * 100)
        b.press(2, 9, 127, 2.)  # (blue's button: only wakes)
        self.assertGreater(pads(b, 2.)[0][0], 0)
        self.assertEqual(b.state()['page'], 'red')

    def test_the_round_buttons_show_for_a_while_after_a_press_then_only_power_and_show(self):
        b = self.board
        b.press(1, 9, 127, 0.)
        lit = lambda now: {i for i, c in enumerate(b.frame(now)) if level(c) and not (1 <= i % 10 <= 8 and 1 <= i // 10 <= 8)}
        self.assertEqual(lit(1.), {91, 92, 97, 98, 2})
        self.assertEqual(lit(3600.), {97, 98})
        b.press(7, 9, 127, 3601.)  # (show)
        self.assertEqual(lit(3602.), {91, 92, 97, 98, 2})
        b.press(7, 9, 127, 3603.)  # (shown already: hidden)
        self.assertEqual(lit(3603.5), {97, 98})
        b.press(4, 4, 127, 3604.)  # (a pad is the page's: the buttons stay dark)
        self.assertEqual(lit(3604.5), {97, 98})

    def test_a_pages_permanent_controls_stay_steady_while_the_profile_buttons_fade(self):
        self.home.plugin('steady', buttons_stay_lit=True, actions={'beep': 'Beep'},
                         defaults={'buttons': {'bottom2': 'beep'}})
        b = self.home.board()
        b.open('steady')
        frame = b.frame(0.)
        steady = frame[2]
        self.assertNotEqual(steady, BLACK)
        for quarter in range(1, 241):
            frame = b.frame(quarter / 4)
            self.assertEqual(frame[2], steady)
            if frame[91] == BLACK:
                break
        self.assertEqual(frame[91], BLACK)

    def test_any_function_can_sit_on_any_round_button(self):
        b = self.board
        settings = b.config
        settings['buttons'] = {'left1': 'power', 'right8': 'open:blue', 'bottom1': 'open:red'}
        settings['plugins']['red']['buttons'] = {'top1': 'beep'}
        b.configure(settings)
        b.press(9, 1, 127, 0.)
        self.assertEqual(b.state()['page'], 'blue')
        b.press(1, 0, 127, 1.)
        b.press(1, 9, 127, 2.)
        self.assertEqual(self.seen('red'), [['action', 'beep']])
        b.press(0, 8, 127, 3.)
        self.assertEqual(b.frame(4.), [BLACK] * 100)

    def test_the_brightness_setting_dims_the_pads(self):
        b = self.board
        bright = level(pads(b, 0.)[0])
        b.configure(b.config | {'brightness': 20})
        self.assertLess(level(pads(b, 0.)[0]), bright)

    def test_the_pads_go_dark_when_left_alone_for_the_time_set_and_a_press_wakes_them(self):
        b = self.board
        b.tick(0.)
        b.tick(9999 * 60.)
        self.assertGreater(level(pads(b, 9999 * 60.)[0]), 0)  # (as it comes, the board never sleeps)
        b.press(4, 4, 127, 9999 * 60.)
        b.configure(b.config | {'sleep': 10})
        start = 9999 * 60.
        b.tick(start + 9 * 60.)
        self.assertGreater(level(pads(b, start + 9 * 60.)[0]), 0)
        b.tick(start + 11 * 60.)
        self.assertEqual(b.frame(start + 11 * 60.), [BLACK] * 100)
        b.press(4, 4, 127, start + 12 * 60.)
        self.assertGreater(level(pads(b, start + 12 * 60.)[0]), 0)

    def test_what_is_set_is_in_the_config_file_and_a_new_board_starts_from_it(self):
        b = self.board
        saved = config.load(self.home.path / 'config.toml')
        self.assertEqual(saved['buttons'], {'top1': 'open:red', 'top2': 'open:blue', 'top7': 'show', 'top8': 'power'})
        self.assertEqual(saved['plugins']['red']['buttons'], {'bottom2': 'beep'})
        b.configure(b.config | {'buttons': {'top5': 'open:blue', 'top8': 'power'}})
        again = self.home.board()
        again.press(5, 9, 127, 0.)
        self.assertEqual(again.state()['page'], 'blue')
        again.press(1, 9, 127, 1.)  # (red has no button any more)
        self.assertEqual(again.state()['page'], 'blue')

    def test_settings_changed_in_place_are_saved_too(self):
        b = self.board
        settings = b.config
        settings['brightness'] = 37
        b.configure(settings)
        self.assertEqual(config.load(self.home.path / 'config.toml')['brightness'], 37)

    def test_the_config_file_is_for_its_owner_alone_since_it_may_hold_a_token(self):
        file = self.home.path / 'config.toml'
        file.chmod(0o644)  # (as an older version wrote it, or as it was copied there)
        board = self.home.board()
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        file.chmod(0o644)
        board.configure(board.config | {'brightness': 37})
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_the_page_is_told_of_an_action_whose_round_button_does_something_else_on_every_page(self):
        b = self.board
        self.assertEqual(b.state()['covered'], [])
        b.configure(b.config | {'buttons': b.config['buttons'] | {'bottom2': 'open:blue'}})
        self.assertEqual(b.state()['covered'], [{'button': 'bottom2', 'does': 'open:blue', 'plugin': 'red', 'action': 'beep'}])
        b.press(2, 0, 127, 1.)  # (and that is what happens: blue opens, red's Beep is not reached)
        self.assertEqual((b.state()['page'], self.seen('red')), ('blue', []))

    def test_starting_the_board_leaves_a_complete_config_file_as_the_owner_wrote_it(self):
        file = self.home.path / 'config.toml'
        file.write_text(file.read_text() + '# my own note\n')
        self.home.board()
        self.assertIn('# my own note', file.read_text())

    def test_settings_without_their_buttons_put_every_button_back_to_its_default(self):
        b = self.board
        settings = b.config
        settings['buttons'] = {'left1': 'open:blue'}
        settings['plugins']['red']['buttons'] = {}
        b.configure(settings)
        del settings['buttons'], settings['plugins']['red']['buttons']
        b.configure(settings)
        self.assertEqual(b.config['buttons'], {'top1': 'open:red', 'top2': 'open:blue', 'top7': 'show', 'top8': 'power'})
        self.assertEqual(b.config['plugins']['red']['buttons'], {'bottom2': 'beep'})

    def test_a_config_file_with_the_wrong_kind_of_values_does_not_stop_the_board(self):
        (self.home.path / 'config.toml').write_text('sleep = "ten"\nbrightness = 900\nbuttons = 5\n[plugins]\nred = 3\n')
        b = self.home.board()
        b.tick(30 * 60.)
        b.press(1, 9, 127, 30 * 60.)
        self.assertGreater(level(pads(b, 30 * 60.)[0]), 0)
        self.assertEqual(b.state()['page'], 'red')

    def test_a_page_that_is_switched_off_is_told_to_stop(self):
        self.home.plugin('worker', button='top3', code='''
            class Plugin:
                def __init__(self, board):
                    self.board = board
                def frame(self, now):
                    return [(1, 1, 1)] * 64
                def stop(self):
                    (self.board.data / 'stopped').write_text('yes')
        ''')
        b = self.home.board()
        settings = b.config
        settings['plugins']['worker']['enabled'] = False
        b.configure(settings)
        self.assertTrue((self.home.path / 'data/worker/stopped').exists())

    def test_a_page_switched_off_cannot_be_opened_and_on_again_it_can(self):
        b = self.board
        settings = b.config
        settings['plugins']['blue']['enabled'] = False
        b.configure(settings)
        b.press(2, 9, 127, 0.)
        self.assertEqual(b.state()['page'], 'red')
        settings['plugins']['blue']['enabled'] = True
        b.configure(settings)
        b.press(2, 9, 127, 1.)
        self.assertEqual(b.state()['page'], 'blue')


class Faults(unittest.TestCase):
    """A plugin that misbehaves is stopped and says why; the rest of the board keeps running."""
    def setUp(self):
        self.home = Home(self)
        self.home.plugin('good', button='top1')

    def stopped(self, board, name):
        return board.state()['plugins'][name]['error']

    def test_a_plugin_that_raises_is_stopped_with_the_line_and_the_board_shows_another_page(self):
        self.home.plugin('bad', button='top2', code='''
            class Plugin:
                def __init__(self, board): pass
                def frame(self, now):
                    return [(0, 0, 9)] * 64
                def press(self, x, y, velocity, now):
                    return 1 / 0
        ''')
        b = self.home.board()
        b.press(2, 9, 127, 0.)
        b.press(3, 3, 127, 1.)
        self.assertIn('press() raised ZeroDivisionError', self.stopped(b, 'bad'))
        self.assertIn('line 8', self.stopped(b, 'bad'))
        self.assertEqual(b.state()['page'], 'good')
        self.assertGreater(level(pads(b, 2.)[0]), 0)
        self.assertIsNone(self.stopped(b, 'good'))

    def test_a_plugin_that_never_comes_back_is_stopped(self):
        self.home.plugin('stuck', button='top2', code='''
            class Plugin:
                def __init__(self, board): pass
                def frame(self, now):
                    while True:
                        pass
        ''')
        b = self.home.board(patience=.1)
        b.press(2, 9, 127, 0.)
        b.frame(0.)
        self.assertIn('frame() took too long', self.stopped(b, 'stuck'))
        self.assertGreater(level(pads(b, 1.)[0]), 0)

    def test_a_plugin_for_a_newer_board_is_refused_and_says_so(self):
        self.home.plugin('future', button='top2', api=99)
        b = self.home.board()
        self.assertIn('made for plugin interface 99', self.stopped(b, 'future'))
        b.press(2, 9, 127, 0.)
        self.assertEqual(b.state()['page'], 'good')

    def test_a_plugin_that_does_not_give_64_colours_is_stopped(self):
        self.home.plugin('short', button='top2', code='''
            class Plugin:
                def __init__(self, board): pass
                def frame(self, now):
                    return [(1, 2, 3)]
        ''')
        b = self.home.board()
        b.press(2, 9, 127, 0.)
        b.frame(0.)
        self.assertIn('64 colours', self.stopped(b, 'short'))

    def test_other_ways_a_plugin_can_misbehave_stop_it_and_not_the_board(self):
        codes = {
            'exits': 'import sys\nclass Plugin:\n    def __init__(self, board): pass\n    def frame(self, now): sys.exit(3)\n',
            'hangs_at_once': 'while True: pass\n',
            'bad_lights': 'class Plugin:\n    def __init__(self, board): pass\n    def frame(self, now): return [(9, 9, 9)] * 64\n'
                          '    def lights(self, now): return [1, 2]\n    def actions(self): return 7\n    def things(self): return 7\n',
            'outer': 'class Plugin:\n    def __init__(self, board): self.inner = board.plugins("scene")[0].start()\n'
                     '    def frame(self, now):\n        self.inner.frame(now)\n        while True: pass\n',
        }
        self.home.plugin('inner', kind='scene')
        self.home.plugin('wrong_about', defaults='not a table')
        for n, (name, code) in enumerate(codes.items()):
            self.home.plugin(name, code=code, button=f'top{n + 2}', actions={'a': 'A'}, defaults={'buttons': {'bottom1': 'a'}})
        b = self.home.board(patience=.1)
        for n in range(len(codes)):
            b.press(n + 2, 9, 127, float(n))
            b.frame(n + .5)
            b.state()
        for name in ('exits', 'hangs_at_once', 'outer', 'wrong_about'):
            self.assertIsNotNone(self.stopped(b, name), name)
        self.assertIsNone(self.stopped(b, 'bad_lights'))  # (what it says about its buttons is ignored; its pads still show)
        b.press(1, 9, 127, 9.)
        self.assertGreater(level(pads(b, 9.)[0]), 0)

    def test_colours_that_are_not_numbers_stop_the_plugin_and_odd_numbers_are_brought_into_range(self):
        frames = {'words': "[('r', 'g', 'b')] * 64", 'wild': "[(float('nan'), float('inf'), -5)] * 64"}
        for n, (name, picture) in enumerate(frames.items()):
            self.home.plugin(name, button=f'top{n + 2}', code=f'''
                class Plugin:
                    def __init__(self, board): pass
                    def frame(self, now):
                        return {picture}
            ''')
        b = self.home.board()
        b.press(2, 9, 127, 0.)
        b.frame(0.)
        self.assertIn('frame()', self.stopped(b, 'words'))
        b.press(3, 9, 127, 1.)
        self.assertTrue(all(0 <= c <= 255 for colour in b.frame(1.) for c in colour))
        self.assertIsNone(self.stopped(b, 'wild'))

    def test_the_only_page_failing_later_leaves_a_dark_board_that_still_runs(self):
        folder = self.home.plugin('good', code='''
            class Plugin:
                def __init__(self, board): pass
                def frame(self, now):
                    return [(9, 9, 9)] * 64 if now < 100 else 1 / 0
        ''', button='top1')
        b = self.home.board()
        b.frame(0.)
        self.assertEqual(level(pads(b, 200.)[0]), 0)
        self.assertEqual(level(pads(b, 201.)[0]), 0)
        self.assertIsNone(b.state()['page'])

    def test_a_plugin_that_is_switched_off_is_not_even_read(self):
        folder = self.home.plugin('loud', code='raise RuntimeError("read")\n', button='top2')
        (self.home.path).mkdir(parents=True, exist_ok=True)
        (self.home.path / 'config.toml').write_text('[plugins.loud]\nenabled = false\n')
        b = self.home.board()
        self.assertIsNone(self.stopped(b, 'loud'))
        settings = b.config
        settings['plugins']['loud']['enabled'] = True
        b.configure(settings)
        self.assertIn('RuntimeError', self.stopped(b, 'loud'))

    def test_the_owners_plugins_are_found_in_their_own_folder_and_marked(self):
        self.home.plugin('mine', own=True, button='top3')
        self.home.plugin('good', own=True)  # (a shipped one has that name)
        plugins = self.home.board().state()['plugins']
        self.assertTrue(plugins['mine']['own'])
        self.assertFalse(plugins['good']['own'])
        self.assertIn('already called good', plugins['good (yours)']['error'])

    def test_removing_an_own_plugin_named_like_a_shipped_one_leaves_the_shipped_ones_files(self):
        self.home.plugin('good', own=True)
        b = self.home.board()
        kept = self.home.path / 'data/good/kept.txt'
        kept.parent.mkdir(parents=True)
        kept.write_text('the shipped plugin saved this')
        b.remove('good (yours)', data=True)
        self.assertTrue(kept.exists())
        self.assertNotIn('good (yours)', b.state()['plugins'])

    def test_a_stopped_plugin_switched_off_and_on_is_tried_again(self):
        folder = self.home.plugin('bad', button='top2', code='def (')
        b = self.home.board()
        self.assertIn('SyntaxError', self.stopped(b, 'bad'))
        (folder / 'plugin.py').write_text((self.home.shipped / 'good/plugin.py').read_text())
        settings = b.config
        for enabled in (False, True):
            settings['plugins']['bad']['enabled'] = enabled
            b.configure(settings)
        self.assertIsNone(self.stopped(b, 'bad'))
        b.press(2, 9, 127, 0.)
        self.assertEqual(b.state()['page'], 'bad')


class Battery(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        self.home.plugin('good', button='top1')
        self.board = self.home.board()

    def test_low_battery_warning_stays_visible_after_the_buttons_fade(self):
        b = self.board
        b.press(1, 9, 127, 0.)
        b.battery_sample = SimpleNamespace(at=3600., percent=20, plugged=False)
        colour = b.frame(3600.)[98]
        self.assertGreater(colour[0], colour[1])
        self.assertEqual(colour[1:], (0, 0))

    def test_power_status_distinguishes_external_power_and_stale_readings(self):
        b = self.board
        b.battery_sample = SimpleNamespace(at=20., percent=60, plugged=True)
        plugged = b.frame(20.)[98]
        b.battery_sample = SimpleNamespace(at=20., percent=60, plugged=False)
        self.assertNotEqual(b.frame(20.)[98], plugged)
        stale = b.frame(120.)[98]
        self.assertEqual(len(set(stale)), 1)  # (grey: the reading is old)
        self.assertEqual(b.state()['battery'], None)


class PickupWake(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        self.home.plugin('red', button='top1')
        self.home.plugin('blue', button='top2', colour=BLUE)
        self.board = self.home.board()

    def samples(self, start, count, tilt=0):
        from padcade.drivers.imu import MotionSample
        angle = math.radians(tilt) / 2
        q = (0., math.sin(angle), 0., math.cos(angle))
        for i in range(count):
            now = start + i * .05
            self.board.motion_sample = MotionSample(now, (0., 0., 9.81), (0., 0., 0.), (0., 0., 0.), q, q)
            self.board.tick(now)

    def test_pickup_wakes_the_last_page(self):
        b = self.board
        b.press(2, 9, 127, 0.)
        b.press(8, 9, 127, 1.)  # Display off
        b.tick(1.)
        self.samples(3.1, 15)
        self.samples(3.85, 5, tilt=20)
        self.assertGreater(level(pads(b, 4.2)[0]), 0)
        self.assertEqual(b.state()['page'], 'blue')

    def test_only_a_pickup_after_one_hour_gets_a_brief_welcome(self):
        b = self.board
        b.press(2, 9, 127, 0.)
        self.samples(0., 15)
        normal = pads(b, .7)
        self.samples(.75, 5, tilt=20)
        self.assertEqual(pads(b, 1.), normal)
        self.samples(3601., 15)
        self.assertEqual(pads(b, 3601.7), normal)
        self.samples(3601.75, 5, tilt=20)
        self.assertNotEqual(pads(b, 3602.), normal)
        self.assertEqual(pads(b, 3602.7), normal)

    def test_a_button_interrupts_the_welcome_and_performs_its_action_immediately(self):
        b = self.board
        b.tick(0.)
        self.samples(3600., 15)
        self.samples(3600.75, 5, tilt=20)
        b.press(2, 9, 127, 3601.)
        reference = self.home.board()
        reference.press(2, 9, 127, 3601.)
        self.assertEqual(b.frame(3601.), reference.frame(3601.))

    def test_manual_off_cannot_wake_immediately(self):
        b = self.board
        self.samples(0., 15)
        b.press(8, 9, 127, 1.)
        self.samples(1., 10, tilt=20)
        self.assertEqual(b.frame(1.5), [BLACK] * 100)
        self.samples(3.1, 15, tilt=20)
        self.samples(3.85, 5, tilt=40)
        self.assertGreater(level(pads(b, 4.2)[0]), 0)

    def test_the_board_lying_flat_is_remembered_as_level_for_the_tilt_games(self):
        b = self.board
        self.assertIn('error', b.level(5.))  # (no reading: nothing to remember)
        self.samples(10., 3, tilt=20)
        b.level(10.1)
        x, y, z = config.load(self.home.path / 'config.toml')['tabletop_gravity']
        self.assertAlmostEqual(x * x + y * y + z * z, 1, places=5)
        self.assertGreater(abs(x) + abs(y), .2)  # (tilted as it lay: not straight down)

    def test_settings_tracks_sensor_connection_between_frames_and_after_disconnect(self):
        b = self.board
        before = b.state()
        self.samples(10., 1)
        connected = b.state()
        self.assertNotEqual(before['revision'], connected['revision'])
        # The UART reader can deliver the next sample before the board draws its next frame.
        b.motion_sample = SimpleNamespace(at=10.05)
        self.assertEqual(b.state()['parts']['Motion sensor'], 'found')
        b.motion_sample = None
        b.tick(11.)
        disconnected = b.state()
        self.assertEqual(disconnected['parts']['Motion sensor'], 'not found')
        self.assertNotEqual(connected['revision'], disconnected['revision'])

    def test_plugins_only_receive_fresh_motion(self):
        b = self.board
        self.assertIsNone(b.motion(10.0))
        sample = SimpleNamespace(at=10.0)
        b.motion_sample = sample
        self.assertIs(b.motion(10.15), sample)
        self.assertIsNone(b.motion(10.26))
        self.assertIsNone(b.motion(9.9))
