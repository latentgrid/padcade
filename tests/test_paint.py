"""Paint: drawing on the pads, frame by frame, into the gallery that the management page and Pixel Art share."""
import json
import unittest

from tests.kit import BLACK, Home, level, pads, ship

# its round buttons, where they sit until moved: (x, y) on the board
COLOUR1, COLOUR2, GALLERY, SAVE, CLEAR, PLAYS = (9, 8), (9, 7), (0, 8), (0, 7), (0, 6), (0, 5)
PREVIOUS, NEXT, COPY, REMOVE, PLAY = (3, 0), (4, 0), (5, 0), (6, 0), (7, 0)


class Paint(unittest.TestCase):
    def setUp(self):
        self.home = Home(self)
        ship(self.home, 'games', 'paint', 'pixel_art')
        self.start()

    def start(self):
        self.board = board = self.home.board()
        settings = board.config
        settings['brightness'] = 100
        settings['plugins']['games']['pads'] = {'1,5': {'thing': 'paint'}}
        board.configure(settings)
        board.press(2, 9, 127, 0.)  # (Games)
        board.press(1, 5, 127, 0.)
        self.blank = pads(board, 0.)

    def press(self, spot, now):
        self.board.press(*spot, 127, now)
        self.board.release(*spot, now)

    def saved(self):
        return self.board.request('pixel_art', 'drawings', {}, b'')

    def test_a_pad_takes_the_colour_picked_its_other_shade_and_the_same_again_rubs_it_out(self):
        b = self.board
        self.press(COLOUR1, 1.)
        self.press((3, 3), 2.)
        first = pads(b, 2.)[18]
        self.assertNotEqual(first, BLACK)
        self.press(COLOUR1, 3.)  # (again: its other shade)
        self.press((4, 3), 4.)
        second = pads(b, 4.)[19]
        self.assertNotIn(second, (BLACK, first))
        self.press((4, 3), 5.)
        self.assertEqual(pads(b, 5.)[19], BLACK)

    def test_clear_wipes_only_when_pressed_twice(self):
        self.press((3, 3), 1.)
        drawing = pads(self.board, 1.)
        self.press(CLEAR, 2.)
        self.assertEqual(pads(self.board, 2.), drawing)
        self.press(CLEAR, 2.1)
        self.assertEqual(pads(self.board, 3.), self.blank)

    def test_frames_are_copied_stepped_through_and_removed(self):
        b = self.board
        self.press((1, 1), 1.)
        one = pads(b, 1.)
        self.press(COPY, 2.)
        self.assertEqual(pads(b, 2.), one)  # (the copy, to change)
        self.press((2, 2), 3.)
        two = pads(b, 3.)
        self.press(PREVIOUS, 4.)
        self.assertEqual(pads(b, 4.), one)
        self.press(NEXT, 5.)
        self.assertEqual(pads(b, 5.), two)
        self.press(REMOVE, 6.)
        self.assertEqual(pads(b, 6.), two)  # (once is not enough)
        self.press(REMOVE, 6.1)
        self.assertEqual(pads(b, 7.), one)
        self.press(NEXT, 8.)
        self.assertEqual(pads(b, 8.), one)  # (the only frame left)

    def test_play_shows_the_frames_in_turn_until_it_is_stopped(self):
        b = self.board
        self.press((1, 1), 1.)
        self.press(COPY, 2.)
        self.press((2, 2), 3.)
        self.press(PLAY, 4.)
        self.assertEqual(len({tuple(pads(b, 4 + k / 10)) for k in range(40)}), 2)
        self.press(PLAY, 9.)
        self.assertEqual(len({tuple(pads(b, 9 + k / 10)) for k in range(40)}), 1)

    def test_a_saved_drawing_is_in_the_gallery_after_a_restart_and_can_be_opened_and_changed(self):
        self.press((1, 1), 1.)
        self.press(COPY, 2.)
        self.press((2, 2), 3.)
        two = pads(self.board, 3.)
        self.press(SAVE, 4.)
        self.press(SAVE, 4.5)  # (saved twice: still one drawing)
        self.assertEqual([len(d['frames']) for d in self.saved()], [2])
        self.start()
        self.assertEqual(pads(self.board, 1.), self.blank)  # (a new canvas)
        self.press((5, 5), 1.)
        canvas = pads(self.board, 1.)
        self.press(GALLERY, 2.)
        self.assertNotEqual(pads(self.board, 2.), canvas)  # (the saved one, playing)
        self.press(GALLERY, 3.)
        self.assertEqual(pads(self.board, 3.), canvas)  # (looking does not touch what is being drawn)
        self.press(GALLERY, 4.)
        self.press((4, 4), 5.)  # (a pad, in the gallery: that drawing to work on)
        self.press(NEXT, 6.)
        self.assertEqual(pads(self.board, 6.), two)
        self.press((3, 3), 7.)
        self.press(SAVE, 8.)
        self.assertEqual([len(d['frames']) for d in self.saved()], [2])  # (changed, not added)
        self.assertNotEqual(self.saved()[0]['frames'][1][8 * 2 + 2], '#000000')

    def test_a_drawing_is_deleted_in_the_gallery_by_pressing_clear_twice(self):
        for k, pad in enumerate(((1, 1), (2, 2))):
            self.press(pad, 1. + k)
            self.press(SAVE, 1.5 + k)
            self.press(CLEAR, 1.6 + k)
            self.press(CLEAR, 1.7 + k)  # (wiped: the next one is a new drawing)
        self.assertEqual(len(self.saved()), 2)
        first = self.saved()[0]
        self.press(GALLERY, 5.)
        self.press(CLEAR, 6.)
        self.assertEqual(len(self.saved()), 2)
        self.press(CLEAR, 6.1)
        self.assertEqual(self.saved(), [first])

    def test_a_drawing_plays_in_pixel_art_only_when_it_is_told_to(self):
        b = self.board
        self.press((1, 1), 1.)
        self.press(SAVE, 2.)
        b.press(1, 9, 127, 3.)  # (Pixel Art: nothing to show)
        self.assertEqual(level(pads(b, 3.)[0]), 0)
        b.press(2, 9, 127, 4.)  # (back to the game, as it was left)
        self.press(PLAYS, 5.)
        self.press(SAVE, 6.)
        b.press(1, 9, 127, 7.)
        self.assertGreater(level(pads(b, 7.)[0]), 0)

    def test_a_pad_edit_keeps_both_versions_if_the_web_editor_saved_first(self):
        self.press((1, 1), 1.)
        self.press(SAVE, 2.)
        saved, = self.saved()
        self.board.request('pixel_art', 'save', {}, json.dumps(saved | {'name': 'Web edit'}).encode())
        self.press((2, 2), 3.)
        self.press(SAVE, 4.)
        self.assertEqual([d['name'] for d in self.saved()], ['Web edit', 'Drawing 1 (copy)'])
        self.assertNotEqual(*[d['frames'] for d in self.saved()])

    def test_a_drawing_made_on_the_management_page_can_be_opened_on_the_pads(self):
        drawing = {'name': 'Web', 'frames': [['#00ff00'] * 64], 'ms': 500}
        self.board.request('pixel_art', 'save', {}, json.dumps(drawing).encode())
        self.press(GALLERY, 1.)
        colour = pads(self.board, 2.)[0]  # (once it has come up)
        self.assertGreater(colour[1], colour[0] + colour[2])
