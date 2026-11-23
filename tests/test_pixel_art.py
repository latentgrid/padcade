"""The Pixel Art page: the installed scenes and the owner's drawings, one after another."""
import json
import unittest

from tests.kit import Home, level, pads, ship

SCENE = '''
class Plugin:
    def __init__(self, board):
        self.pressed = False
    def frame(self, now):
        return [(255, 255, 255) if self.pressed else COLOUR] * 64
    def press(self, x, y, velocity, now):
        self.pressed = True
    def release(self, x, y, now):
        self.pressed = False
'''
RED, GREEN, BLUE = (200, 0, 0), (0, 200, 0), (0, 0, 200)


class Scenes(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'pixel_art')
        for name, colour in (('a_red', RED), ('b_green', GREEN), ('c_blue', BLUE)):
            self.home.plugin(name, code=SCENE, colour=colour, kind='scene')
        self.board = self.home.board()
        self.set(next_after=30)

    def set(self, **settings):
        config = self.board.config
        config['plugins']['pixel_art']['settings'] = settings
        self.board.configure(config)

    def shows(self, now):
        colour = pads(self.board, now)[0]
        return 'red green blue'.split()[colour.index(max(colour))] if len(set(colour)) > 1 else 'white'

    def test_a_damaged_gallery_is_kept_and_can_be_used_again(self):
        path = self.home.path / 'data/drawings.json'
        path.parent.mkdir(exist_ok=True)
        broken = '[{"frames": []}, 5]'
        path.write_text(broken)
        again = self.home.board()
        self.assertEqual(again.request('pixel_art', 'drawings', {}, b''), [])
        self.assertEqual(path.with_suffix('.broken').read_text(), broken)
        new = {'name': 'Again', 'frames': [['#c0ffee'] * 64]}
        self.assertEqual(again.request('pixel_art', 'save', {}, json.dumps(new).encode())['name'], 'Again')
        self.assertEqual(path.with_suffix('.broken').read_text(), broken)

    def test_api_one_plugins_can_edit_and_delete_drawings_by_index(self):
        gallery = self.board.gallery
        drawing = {'name': 'First', 'frames': [['#112233'] * 64]}
        index = gallery.put(drawing)
        gallery.put(drawing | {'name': 'Second'}, index)
        self.assertEqual(gallery.drawings[index]['name'], 'Second')
        gallery.delete(index)
        self.assertEqual(gallery.drawings, [])

    def test_the_scenes_take_turns_after_the_time_set(self):
        self.assertEqual(self.shows(0.), 'red')
        self.assertEqual(self.shows(29.), 'red')
        self.assertEqual(self.shows(31.), 'green')
        self.assertEqual(self.shows(62.), 'blue')
        self.assertEqual(self.shows(93.), 'red')
        self.set(next_after=0)  # (never)
        self.assertEqual(self.shows(9999.), 'red')

    def test_a_scene_being_played_with_stays_until_it_is_left_alone(self):
        b = self.board
        self.shows(0.)
        b.press(4, 4, 127, 25.)
        self.assertEqual(self.shows(25.1), 'white')  # (the press reached the scene)
        b.release(4, 4, 25.2)
        self.assertEqual(self.shows(40.), 'red')
        self.assertEqual(self.shows(56.), 'green')

    def test_round_buttons_step_through_the_scenes_and_pause_holds_one(self):
        b = self.board
        self.shows(0.)
        b.press(2, 0, 127, 1.)  # (next)
        self.assertEqual(self.shows(1.), 'green')
        b.press(1, 0, 127, 2.)  # (previous)
        b.press(1, 0, 127, 3.)
        self.assertEqual(self.shows(3.), 'blue')
        b.press(3, 0, 127, 4.)  # (pause)
        paused = level(b.frame(4.5)[3])
        self.assertEqual(self.shows(500.), 'blue')
        b.press(3, 0, 127, 501.)
        self.assertGreater(paused, level(b.frame(501.5)[3]))  # (its button said so)
        self.assertEqual(self.shows(532.), 'red')

    def test_a_drawing_saved_meanwhile_does_not_change_which_scene_is_showing(self):
        self.assertEqual(self.shows(0.), 'red')
        self.board.press(2, 0, 127, 1.)  # (next: green)
        self.board.request('pixel_art', 'save', {}, json.dumps({'name': 'New', 'frames': [['#ffffff'] * 64]}).encode())
        self.assertEqual(self.shows(2.), 'green')

    def test_the_page_lists_the_scenes_that_take_turns_and_shows_the_one_asked_for(self):
        board = self.board
        config = board.config
        config['plugins']['b_green']['enabled'] = False
        board.configure(config)
        self.assertEqual(self.shows(0.), 'red')
        said = board.state()['plugins']['pixel_art']['state']
        self.assertEqual((said['scenes'], said['key']), ([['a_red', 'A_Red'], ['c_blue', 'C_Blue']], 'a_red'))
        board.now = 5.
        self.assertEqual(board.request('pixel_art', 'show', {}, b'{"key":"c_blue"}'), {})
        self.assertEqual(self.shows(6.), 'blue')
        self.assertEqual(board.state()['plugins']['pixel_art']['state']['key'], 'c_blue')
        self.assertEqual(self.shows(34.), 'blue')  # (it has its full time from when it was asked for)
        self.assertEqual(self.shows(36.), 'red')
        self.assertIn('error', board.request('pixel_art', 'show', {}, b'{"key":"b_green"}'))  # (switched off: not in the turns)

    def test_the_page_gets_a_picture_of_a_scene_without_disturbing_the_one_showing(self):
        board = self.board
        config = board.config
        config['plugins']['b_green']['enabled'] = False
        board.configure(config)
        self.assertEqual(self.shows(0.), 'red')
        board.now = 1.
        self.assertEqual(board.request('pixel_art', 'picture', {}, b'{"key":"c_blue"}'), {'pixels': ['#0000c8'] * 64})
        self.assertEqual(self.shows(2.), 'red')
        # One switched off is not run for this: its picture comes when it is switched on.
        self.assertIn('error', board.request('pixel_art', 'picture', {}, b'{"key":"b_green"}'))
        self.assertIn('error', board.request('pixel_art', 'picture', {}, b'{"key":"nothing"}'))

    def test_preview_plays_a_disabled_scene_then_returns_to_the_paused_scene(self):
        board = self.board
        config = board.config
        config['plugins']['b_green']['enabled'] = False
        board.configure(config)
        self.shows(0.)
        board.act('pause', 1.)
        board.request('pixel_art', 'preview', {}, b'{"key":"b_green"}')
        self.assertEqual(self.shows(2.), 'green')
        self.assertEqual(self.shows(600.), 'red')
        self.assertTrue(board.state()['plugins']['pixel_art']['state']['paused'])
        self.assertFalse(board.state()['plugins']['b_green']['enabled'])

    def test_a_gallery_only_drawing_can_be_previewed_and_stopped(self):
        board = self.board
        self.shows(0.)
        d = board.request('pixel_art', 'save', {}, json.dumps({
            'name': 'Test', 'frames': [['#0000ff'] * 64], 'plays': False}).encode())
        board.request('pixel_art', 'preview', {}, json.dumps({'key': 'drawing ' + d['id']}).encode())
        self.assertEqual(self.shows(1.), 'blue')
        board.request('pixel_art', 'preview', {}, b'{"key":null}')
        self.assertEqual(self.shows(2.), 'red')
        self.assertFalse(board.request('pixel_art', 'drawings', {}, b'')[0]['plays'])

    def test_a_scene_switched_off_is_left_out(self):
        config = self.board.config
        config['plugins']['b_green']['enabled'] = False
        self.board.configure(config)
        self.assertEqual([self.shows(t) for t in (0., 31., 62.)], ['red', 'blue', 'red'])

    def test_a_scene_that_raises_is_stopped_and_the_others_carry_on(self):
        self.home.plugin('b_green', kind='scene', code='''
            class Plugin:
                def __init__(self, board): pass
                def frame(self, now):
                    raise RuntimeError('no')
        ''')
        b = self.home.board()
        self.board = b
        self.set(next_after=30)
        self.assertEqual([self.shows(t) for t in (0., 31., 62.)], ['red', 'blue', 'red'])
        state = b.state()
        self.assertIn('frame() raised RuntimeError', state['plugins']['b_green']['error'])
        self.assertIsNone(state['plugins']['pixel_art']['error'])


class Drawings(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'pixel_art')
        self.board = self.home.board()

    def ask(self, what, **data):
        return self.board.request("pixel_art", what, {}, json.dumps(data).encode())

    def test_a_drawing_plays_its_frames_and_is_still_there_after_a_restart(self):
        frames = [['#ff0000'] * 64, ['#0000ff'] * 64]
        self.ask('save', name='Flag', frames=frames, ms=500)
        colours = {pads(self.board, t / 10)[0] for t in range(20)}
        self.assertEqual(len(colours), 2)
        again = self.home.board()
        self.assertEqual([d['name'] for d in again.request('pixel_art', 'drawings', {}, b'')], ['Flag'])
        self.assertGreater(level(pads(again, 0.)[0]), 0)

    def test_an_existing_gallery_keeps_its_order_and_ids_across_restarts(self):
        path = self.home.path / 'data' / 'drawings.json'
        path.parent.mkdir(exist_ok=True)
        old = [{'name': name, 'frames': [['#ffffff'] * 64], 'ms': 500} for name in ('Second', 'First')]
        original_file = json.dumps(old)
        path.write_text(original_file)
        self.board = self.home.board()
        migrated = self.ask('drawings')
        self.assertEqual([d['name'] for d in migrated], ['Second', 'First'])
        self.assertEqual(len({d['id'] for d in migrated}), 2)
        self.board = self.home.board()
        self.assertEqual(self.ask('drawings'), migrated)
        self.assertEqual(path.read_text(), original_file)  # an install rollback can open the original file
        self.ask('save', **(migrated[0] | {'name': 'Edited'}))
        self.board = self.home.board()
        edited, unchanged = self.ask('drawings')
        self.assertEqual((edited['id'], edited['name'], unchanged), (migrated[0]['id'], 'Edited', migrated[1]))

    def test_a_drawing_can_be_changed_and_deleted(self):
        self.ask('save', name='One', frames=[['#ff0000'] * 64], ms=500)
        original, = self.ask('drawings')
        self.ask('save', **(original | {'name': 'Two', 'frames': [['#00ff00'] * 64], 'ms': 200}))
        self.assertEqual([d['name'] for d in self.ask('drawings')], ['Two'])
        colour = pads(self.board, 0.)[0]
        self.assertGreater(colour[1], colour[0])
        self.ask('delete', **self.ask('drawings')[0])
        self.assertEqual(self.ask('drawings'), [])
        self.assertEqual(level(pads(self.board, 1.)[0]), 0)

    def test_a_stale_editor_cannot_overwrite_another_drawing_or_another_edit(self):
        for name in ['A', 'B', 'C']:
            self.ask('save', name=name, frames=[['#ffffff'] * 64])
        a, b, _ = self.ask('drawings')
        self.ask('delete', **a)
        self.ask('save', **(b | {'name': 'Updated B'}))
        self.assertEqual([d['name'] for d in self.ask('drawings')], ['Updated B', 'C'])
        self.assertIn('error', self.ask('save', **(b | {'name': 'Stale B'})))
        self.assertIn('error', self.ask('delete', **b))
        self.assertEqual([d['name'] for d in self.ask('drawings')], ['Updated B', 'C'])

    def test_a_request_the_page_got_wrong_is_answered_and_the_plugin_keeps_running(self):
        self.ask('save', name='One', frames=[['#ff0000'] * 64], ms=500)
        self.assertIn('error', self.board.request('pixel_art', 'save', {}, b'not json'))
        self.assertIn('error', self.ask('delete', index=7))
        self.assertIsNone(self.board.state()['plugins']['pixel_art']['error'])
        self.assertEqual([d['name'] for d in self.ask('drawings')], ['One'])

    def test_a_drawing_that_is_not_64_colours_a_frame_is_refused(self):
        self.assertIn('error', self.ask('save', name='Bad', frames=[['#ff0000'] * 3], ms=500))
        self.assertEqual(self.ask('drawings'), [])
