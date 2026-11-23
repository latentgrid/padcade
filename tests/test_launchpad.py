"""What goes to the Launchpad and what its bytes mean."""
import unittest

from padcade.drivers.launchpad import Reader, grid

BLACK = (0, 0, 0)


class Grid(unittest.TestCase):
    def test_a_picture_of_the_surface_becomes_one_grid_message(self):
        frame = [BLACK] * 100
        frame[11] = (255, 0, 0)  # the bottom left pad (x 1, y 1: programmer layout's note 11)
        frame[98] = (0, 128, 255)  # the top row's last round button
        message = grid(frame)
        self.assertEqual(message[:8], [0xF0, 0x00, 0x20, 0x29, 0x02, 0x10, 0x0F, 0x00])  # (RGB grid, 10 by 10)
        self.assertEqual(message[-1], 0xF7)
        self.assertEqual(len(message), 8 + 300 + 1)
        rgb = message[8:-1]
        self.assertEqual(rgb[3 * 11:3 * 11 + 3], [63, 0, 0])  # (the Launchpad's 0-63 levels)
        self.assertEqual(rgb[3 * 98:3 * 98 + 3], [0, 32, 63])
        self.assertEqual(sum(rgb), 63 + 32 + 63)


class Presses(unittest.TestCase):
    def test_pads_buttons_and_pressure_come_out_as_events_however_the_bytes_arrive(self):
        reader = Reader()
        stream = bytes([0x90, 81, 100,  # the top left pad, hit hard
                        0xA0, 81, 40, 81, 70,  # held: its pressure (the second with running status)
                        0x90, 81, 0,  # let go
                        0xF0, 0x7E, 0x00, 0x06, 0x02, 0x00, 0xF7,  # a SysEx reply among them
                        0xB0, 91, 127, 91, 0])  # the top row's first round button, pressed and let go
        events = []
        for k in range(0, len(stream), 4):  # (in pieces, as reads come)
            events += reader.feed(stream[k:k + 4])
        self.assertEqual(events, [('press', 1, 8, 100), ('pressure', 1, 8, 40), ('pressure', 1, 8, 70), ('release', 1, 8, 0),
                                  ('press', 1, 9, 127), ('release', 1, 9, 0)])
