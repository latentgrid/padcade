"""The Games page: a menu of the games that are installed, and the one being played."""
import unittest

from tests.kit import Home, level, pads, ship

GAME = '''
class Plugin:
    def __init__(self, board):
        self.lit = False
    def frame(self, now):
        return [(255, 255, 255) if self.lit else COLOUR] * 64
    def press(self, x, y, velocity, now):
        self.lit = True
    def release(self, x, y, now):
        pass
    def action(self, name, now):
        self.lit = True
    def released(self, name, now):
        self.lit = False
    def lights(self, now):
        return {'flash': (0, 0, 255)}
    def close(self):
        self.lit = False
'''
RED, BLUE = (200, 0, 0), (0, 0, 200)


class Games(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'games')
        for name, colour in (('reds', RED), ('blues', BLUE)):
            self.home.plugin(name, code=GAME, colour=colour, kind='game', actions={'flash': 'Flash'},
                             defaults={'buttons': {'right1': 'flash'}})
        self.board = self.home.board()
        settings = self.board.config
        settings['plugins']['games']['pads'] = {'1,5': {'thing': 'reds'}, '2,5': {'thing': 'blues'}}
        self.board.configure(settings)

    def shows(self, now):
        colours = set(pads(self.board, now))
        if len(colours) > 1:
            return 'menu'
        colour = colours.pop()
        return 'white' if len(set(colour)) == 1 else 'red' if colour[0] > colour[2] else 'blue'

    def test_the_menu_has_a_pad_for_each_game_and_pressing_one_plays_it(self):
        b = self.board
        frame = b.frame(0.)
        self.assertGreater(frame[51][0], frame[51][2])  # (the first game's pad in its colour)
        self.assertGreater(frame[52][2], frame[52][0])
        self.assertEqual(level(frame[53]), 0)
        b.press(3, 5, 127, 1.)  # (a pad with no game: still the menu)
        self.assertEqual(self.shows(1.), 'menu')
        b.press(2, 5, 127, 2.)
        b.release(2, 5, 2.1)
        self.assertEqual(self.shows(2.2), 'blue')
        b.press(4, 4, 127, 3.)  # (the pads are the game's now)
        self.assertEqual(self.shows(3.), 'white')

    def test_switching_off_the_current_game_returns_to_the_menu(self):
        b = self.board
        b.press(1, 5, 127, 1.)
        settings = b.state()['config']
        settings['plugins']['reds']['enabled'] = False
        b.configure(settings)
        b.frame(2.)
        self.assertIsNone(b.state()['plugins']['games']['state']['hosted'])

    def test_the_management_page_hears_when_a_game_starts_and_when_the_menu_is_back(self):
        b = self.board
        seen = b.state()['revision']
        b.press(1, 5, 127, 1.)
        started = b.state()['revision']
        self.assertGreater(started, seen)  # (something new to fetch: which game is on the pads)
        b.press(1, 0, 127, 2.)  # (menu)
        self.assertGreater(b.state()['revision'], started)

    def test_round_buttons_go_back_to_the_menu_and_start_the_game_over(self):
        b = self.board
        b.press(1, 5, 127, 1.)
        b.press(4, 4, 127, 2.)
        b.press(2, 0, 127, 3.)  # (restart)
        self.assertEqual(self.shows(3.), 'red')
        b.press(1, 0, 127, 4.)  # (menu)
        self.assertEqual(self.shows(4.), 'menu')
        b.press(2, 0, 127, 5.)  # (nothing to start over)
        self.assertEqual(self.shows(5.), 'menu')

    def test_a_games_own_round_button_works_and_is_lit_while_it_is_played(self):
        b = self.board
        self.assertEqual(level(b.frame(3600.)[89]), 0)  # (in the menu: dark)
        b.press(9, 8, 127, 3601.)
        self.assertEqual(self.shows(3601.), 'menu')
        b.press(1, 5, 127, 3602.)
        b.press(9, 8, 127, 3603.)
        self.assertEqual(self.shows(3603.), 'white')
        b.release(9, 8, 3604.)
        self.assertEqual(self.shows(3604.), 'red')
        light = b.frame(9999.)[89]  # (long after the last press: the game's button is still lit)
        self.assertGreater(light[2], light[0])

    def test_a_held_button_does_not_stay_held_when_the_pads_go_dark(self):
        b = self.board
        b.press(1, 5, 127, 1.)
        b.press(9, 8, 127, 2.)  # (held)
        b.press(8, 9, 127, 3.)  # (power: off)
        b.press(8, 9, 127, 4.)  # (and on again)
        self.assertEqual(self.shows(4.), 'red')

    def test_a_game_that_fails_is_stopped_and_the_menu_comes_back_without_it(self):
        self.home.plugin('reds', kind='game', code="""
            class Plugin:
                def __init__(self, board): pass
                def frame(self, now):
                    raise RuntimeError('no')
        """)
        b = self.home.board()
        self.board = b
        b.press(1, 5, 127, 1.)
        self.assertEqual(self.shows(1.), 'menu')
        self.assertEqual(level(b.frame(2.)[51]), 0)
        state = b.state()
        self.assertIn('RuntimeError', state['plugins']['reds']['error'])
        self.assertIsNone(state['plugins']['games']['error'])
        self.assertEqual([game['id'] for game in state['plugins']['games']['items']], ['blues'])

    def test_a_game_switched_off_is_not_on_the_menu(self):
        settings = self.board.config
        settings['plugins']['blues']['enabled'] = False
        self.board.configure(settings)
        self.assertEqual(level(self.board.frame(0.)[52]), 0)
        self.board.press(2, 5, 127, 1.)
        self.assertEqual(self.shows(1.), 'menu')
