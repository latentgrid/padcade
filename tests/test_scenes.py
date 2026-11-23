"""Scenes played through the same guarded interface as the pads."""
import unittest

from tests.kit import shipped


SCENES = ('lava', 'rain', 'campfire', 'fireworks', 'snake', 'paddles', 'bricks', 'heart', 'rocket', 'coin', 'bouncer')
BLACK = (0, 0, 0)


class Scenes(unittest.TestCase):
    def test_every_scene_draws_moves_and_survives_a_press(self):
        for name in SCENES:
            with self.subTest(name=name):
                scene = shipped(name)
                frames = [scene.frame(100 + k / 20) for k in range(40)]
                for picture in frames:
                    self.assertEqual(len(picture), 64)
                    self.assertTrue(all(isinstance(c, tuple) and len(c) == 3 and
                                        all(0 <= v <= 255 for v in c) for c in picture))
                self.assertGreater(len({tuple(f) for f in frames}), 1)
                scene.press(3, 3, 100, 102)
                scene.frame(102.2)
                scene.release(3, 3, 102.2)
                scene.frame(102.4)
                self.assertFalse(scene.stopped, scene.plugin.error)

    def played(self, name, presses, times):
        scene = shipped(name)
        scene.frame(100)
        pending = iter(sorted(presses))
        press = next(pending, None)
        frames = []
        for t in times:
            while press is not None and press[0] <= t:
                at, x, y = press
                scene.press(x, y, 127, 100 + at)
                scene.release(x, y, 100 + at)
                press = next(pending, None)
            frames.append(scene.frame(100 + t))
        self.assertFalse(scene.stopped, scene.plugin.error)
        return frames

    def test_tapping_the_heart_makes_it_beat_faster(self):
        times = [k / 30 for k in range(90)]
        changes = lambda frames: sum(a != b for a, b in zip(frames, frames[1:]))
        calm = self.played('heart', [], times)
        tapped = self.played('heart', [(k / 4, 3, 3) for k in range(8)], times)
        self.assertGreater(changes(tapped), changes(calm))

    def test_a_press_warms_a_new_blob_of_lava_that_rises(self):
        times = [1.6, 3.5]
        calm = self.played('lava', [], times)
        warmed = self.played('lava', [(1, 3, 1)], times)
        centres = []
        for before, after in zip(calm, warmed):
            heat = [max(0, sum(b) - sum(a)) for a, b in zip(before, after)]
            self.assertGreater(sum(heat), 0)
            centres.append(sum((i // 8) * h for i, h in enumerate(heat)) / sum(heat))
        self.assertGreater(centres[1], centres[0])

    def test_collecting_the_coin_lifts_it_then_it_disappears_and_returns(self):
        frames = self.played('coin', [(1, 3, 3)], [k / 30 for k in range(120)])
        low = lambda f: min(i // 8 for i, c in enumerate(f) if c != BLACK)
        before = low(frames[0])
        self.assertTrue(any(low(f) > before for f in frames if any(c != BLACK for c in f)))
        gone = next(i for i, f in enumerate(frames) if all(c == BLACK for c in f))
        self.assertTrue(any(any(c != BLACK for c in f) for f in frames[gone + 1:]))
        self.assertEqual(low(frames[-1]), before)

    def test_a_press_changes_what_these_scenes_show(self):
        for name in ('rain', 'fireworks', 'snake', 'rocket', 'bouncer'):
            with self.subTest(name=name):
                times = [k / 30 for k in range(60)]
                self.assertNotEqual(self.played(name, [], times),
                                    self.played(name, [(1, 3, 3)], times))

    def test_a_campfire_press_flares_then_dies_down(self):
        times = [k / 20 for k in range(140)]
        calm = self.played('campfire', [], times)
        tapped = self.played('campfire', [(2, 3, 0)], times)
        extra = [sum(map(sum, b)) - sum(map(sum, a)) for a, b in zip(calm, tapped)]
        peak = max(extra)
        self.assertGreater(peak, 0)
        self.assertLess(extra[-1], peak)

    def test_holding_the_campfire_harder_stokes_it_and_releasing_lets_it_cool(self):
        soft, hard, released = [shipped('campfire') for _ in range(3)]
        for scene, pressure in ((soft, 1), (hard, 127), (released, 127)):
            scene.press(3, 0, pressure, 100)
        released.release(3, 0, 100)
        heat = [0, 0, 0]
        for k in range(1, 80):
            for i, scene in enumerate((soft, hard, released)):
                heat[i] += sum(map(sum, scene.frame(100 + k / 20)))
        self.assertGreater(heat[1], heat[0])
        self.assertGreater(heat[1], heat[2])
        for scene in (soft, hard, released):
            self.assertFalse(scene.stopped, scene.plugin.error)

    def test_holding_a_pad_steers_the_paddles_of_both_ball_games(self):
        for name, low, high, cells, position in (
            ('paddles', (0, 0), (0, 7), range(0, 64, 8), lambda i: i // 8),
            ('bricks', (0, 0), (7, 0), range(8), lambda i: i % 8),
        ):
            with self.subTest(name=name):
                scenes = [shipped(name), shipped(name)]
                centres = [[], []]
                for scene, pad in zip(scenes, (low, high)):
                    scene.press(*pad, 127, 100)
                for k in range(1, 31):
                    for j, scene in enumerate(scenes):
                        frame = scene.frame(100 + k / 30)
                        lit = [position(i) for i in cells if frame[i] != BLACK]
                        centres[j].append(sum(lit) / len(lit))
                self.assertGreater(sum(centres[1]), sum(centres[0]))
                held = shipped(name)
                held.press(*high, 127, 100)
                for k in range(1, 31):
                    held.frame(100 + k / 30)
                scenes[1].release(*high, 101)
                free_frames, held_frames = [], []
                for k in range(1, 61):
                    free_frames.append(scenes[1].frame(101 + k / 30))
                    held_frames.append(held.frame(101 + k / 30))
                self.assertNotEqual(free_frames, held_frames)
                for scene in (*scenes, held):
                    self.assertFalse(scene.stopped, scene.plugin.error)

    def test_scene_time_starts_with_the_first_frame_or_press(self):
        for name in SCENES:
            for press_first in (False, True):
                with self.subTest(name=name, press_first=press_first):
                    early, late = shipped(name), shipped(name)
                    for scene, origin in ((early, 0), (late, 1000)):
                        if press_first:
                            scene.press(2, 2, 80, origin)
                        else:
                            scene.frame(origin)
                    for k in range(20):
                        self.assertEqual(early.frame(k / 16), late.frame(1000 + k / 16))
                    for scene in (early, late):
                        self.assertFalse(scene.stopped, scene.plugin.error)
