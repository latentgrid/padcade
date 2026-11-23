"""Games played through the surface."""
import math
import unittest

from padcade.drivers.imu import MotionSample
from tests.kit import Home, level, pads, ship


class GamePlugins(unittest.TestCase):
    def play(self, name):
        home = Home(self)
        ship(home, 'games', name)
        board = home.board()
        settings = board.config
        settings['brightness'] = 100
        settings['plugins']['games']['pads'] = {'1,5': {'thing': name}}
        board.configure(settings)
        board.press(1, 5, 127, 0.)
        return home, board

    def test_a_puzzle_press_toggles_its_cross_and_pressing_again_undoes_it(self):
        _, board = self.play('all_off')
        before = pads(board, 0.)
        board.press(4, 4, 127, .1)
        after = pads(board, .1)
        changed = {i for i, pair in enumerate(zip(before, after)) if pair[0] != pair[1]}
        self.assertEqual(changed, {27, 19, 35, 26, 28})
        board.press(4, 4, 127, .2)
        self.assertEqual(pads(board, .2), before)

    def test_sand_pads_toggle_grains_and_restart_restores_them(self):
        _, board = self.play('sand')
        initial = pads(board, 0.)
        board.press(1, 1, 127, .1)
        self.assertNotEqual(pads(board, .1), initial)
        board.press(1, 1, 127, .2)
        self.assertEqual(pads(board, .2), initial)
        board.press(2, 3, 127, .3)
        self.assertNotEqual(pads(board, .3), initial)
        board.press(2, 0, 127, .4)
        self.assertEqual(pads(board, .4), initial)

    def test_moles_hit_flash_and_the_round_finishes_with_a_score(self):
        _, board = self.play('moles')
        empty = pads(board, 0.)
        appeared = pads(board, .7)
        target = max(range(64), key=lambda i: level(appeared[i]) - level(empty[i]))
        self.assertGreater(level(appeared[target]), level(empty[target]))
        board.press(target % 8 + 1, target // 8 + 1, 127, .7)
        self.assertNotEqual(pads(board, .7)[target], appeared[target])
        board.press(3, 3, 127, .8)  # A gap between holes does not score.
        pads(board, 40.)
        score = pads(board, 45.)
        self.assertGreater(len(set(score)), 1)
        board.press(3, 3, 127, 46.)
        self.assertNotEqual(pads(board, 46.), score)

    def test_duel_false_starts_award_the_other_player_the_match(self):
        _, board = self.play('duel')
        pads(board, 0.)
        board.press(2, 2, 127, .1)
        waiting = pads(board, .1)
        board.press(2, 2, 127, .2)
        point = pads(board, .2)
        self.assertGreater(level(point[7]), level(waiting[7]))
        self.assertEqual(point[0], waiting[0])
        for now in (1.4, 2.6, 3.8, 5.):
            pads(board, now)
            board.press(2, 2, 127, now + .01)
        won = pads(board, 6.2)
        left = sum(level(c) for i, c in enumerate(won) if i % 8 < 4)
        right = sum(level(c) for i, c in enumerate(won) if i % 8 >= 4)
        self.assertGreater(right, left)
        self.assertEqual(left, 0)

    def test_mines_first_tap_opens_a_safe_area(self):
        _, board = self.play('mines')
        covered = pads(board, 0.)
        board.press(1, 1, 127, .1)
        board.release(1, 1, .2)
        revealed = pads(board, .2)
        self.assertGreater(sum(a != b for a, b in zip(covered, revealed)), 1)
        board.press(1, 1, 127, .3)
        board.release(1, 1, .4)
        self.assertEqual(pads(board, .4), revealed)

    def test_mines_held_pads_flag_protect_and_unflag(self):
        _, board = self.play('mines')
        initial = pads(board, 0.)
        board.press(8, 8, 127, 1.)
        flagged = pads(board, 2.)
        board.release(8, 8, 2.1)
        self.assertNotEqual(flagged[63], initial[63])
        board.press(8, 8, 127, 3.)
        board.release(8, 8, 3.1)
        self.assertEqual(pads(board, 3.1), flagged)
        board.press(8, 8, 127, 4.)
        pads(board, 5.)
        board.release(8, 8, 5.1)
        self.assertEqual(pads(board, 5.1), initial)

    def motion(self, board, now, pose=(0., 0., 0., 1.)):
        board.motion_sample = MotionSample(now, (0., 0., 9.81), (0., 0., 0.), (0., 0., 0.), pose, pose)
        return pads(board, now)

    def test_tilt_moves_pieces_and_missing_samples_freeze_play_and_warn(self):
        for game in ('sand', 'marble_maze'):
            with self.subTest(game=game):
                _, board = self.play(game)
                missing = board.frame(0.)[69]
                initial = self.motion(board, 0.)
                ready = board.frame(0.)[69]
                self.assertNotEqual(ready, missing)
                for k in range(1, 13):
                    moved = self.motion(board, k / 30, (0., .3, 0., .954))
                self.assertNotEqual(moved, initial)
                self.assertEqual(pads(board, 2.), moved)
                self.assertEqual(pads(board, 20.), moved)
                self.assertEqual(board.frame(20.)[69], missing)

    def test_recenter_holds_the_current_angle_without_moving_pieces(self):
        for game in ('sand', 'marble_maze'):
            with self.subTest(game=game):
                _, board = self.play(game)
                self.motion(board, 0.)
                pose = (0., .3, 0., .954)
                for k in range(1, 13):
                    moved = self.motion(board, k / 30, pose)
                board.press(9, 6, 127, .4)
                board.release(9, 6, .4)
                for k in range(13, 28):
                    self.assertEqual(self.motion(board, k / 30, pose), moved)

    def test_neutral_yaw_drift_and_hand_jitter_do_not_move_pieces(self):
        for game in ('sand', 'marble_maze'):
            with self.subTest(game=game):
                _, board = self.play(game)
                initial = self.motion(board, 0.)
                for k in range(1, 13):
                    angle = math.radians(2 * (-1)**k) / 2
                    self.assertEqual(self.motion(board, k / 30, (0., math.sin(angle), 0., math.cos(angle))), initial)
                self.assertEqual(self.motion(board, 1., (0., 0., math.sqrt(.5), math.sqrt(.5))), initial)

    def test_maze_brake_is_held_and_slows_the_ball_and_a_tap_retries(self):
        finishes = []
        for braking in (False, True):
            _, board = self.play('marble_maze')
            initial = self.motion(board, 0.)
            idle = board.frame(0.)[59]
            if braking:
                board.press(9, 5, 127, 0.)
                self.assertGreater(level(board.frame(0.)[59]), level(idle))
            for k in range(1, 13):
                moved = self.motion(board, k / 30, (0., .3, 0., .954))
            finishes.append(moved)
            board.release(9, 5, .4)
            self.assertEqual(board.frame(.4)[59], idle)
            board.press(4, 4, 127, .4)
            self.assertEqual(pads(board, .4), initial)
        def horizontal(frame):
            weights = [min(c) for c in frame]
            return sum((i % 8) * w for i, w in enumerate(weights)) / sum(weights)
        self.assertGreater(horizontal(finishes[0]), horizontal(finishes[1]))

    def test_solving_the_visible_puzzle_celebrates_and_deals_another(self):
        _, board = self.play('all_off')
        picture = pads(board, 0.)
        on = max(picture, key=level)
        rows = [sum(1 << x for x in range(8) if picture[8*y + x] == on) for y in range(8)]

        def switch(state, y, mask):
            state[y] ^= mask ^ ((mask << 1) & 255) ^ (mask >> 1)
            if y:
                state[y - 1] ^= mask
            if y < 7:
                state[y + 1] ^= mask

        # Try the first row, then extinguish each row by pressing directly above it.
        for first in range(256):
            state, moves = rows.copy(), [first]
            switch(state, 0, first)
            for y in range(1, 8):
                moves.append(state[y - 1])
                switch(state, y, moves[-1])
            if not any(state):
                break
        else:
            self.fail('The visible puzzle cannot be solved')
        for y, mask in enumerate(moves):
            for x in range(8):
                if mask & (1 << x):
                    board.press(x + 1, y + 1, 127, 1.)
        celebration = pads(board, 1.2)
        self.assertGreater(len(set(celebration)), len(set(picture)))
        next_puzzle = pads(board, 4.)
        self.assertEqual(set(next_puzzle), set(picture))
        self.assertNotEqual(next_puzzle, celebration)

    def test_sand_pours_in_each_direction_without_losing_grains(self):
        for roll, pitch, axis, direction in ((0, 40, 0, 1), (0, -40, 0, -1), (-40, 0, 1, 1), (40, 0, 1, -1)):
            with self.subTest(roll=roll, pitch=pitch):
                _, board = self.play('sand')
                initial = self.motion(board, 0.)
                angle = math.radians(math.hypot(roll, pitch)) / 2
                scale = math.sin(angle) / math.hypot(roll, pitch)
                pose = (roll * scale, pitch * scale, 0., math.cos(angle))
                for k in range(1, 25):
                    final = self.motion(board, k / 30, pose)
                grains = lambda frame: [(i % 8, i // 8) for i, c in enumerate(frame) if level(c)]
                before, after = grains(initial), grains(final)
                self.assertEqual(len(after), len(before))
                self.assertGreater(direction * (sum(p[axis] for p in after) - sum(p[axis] for p in before)), 0)

    def test_sand_recognises_the_captured_case_orientation_and_front_tilt(self):
        _, board = self.play('sand')
        rest = (.00018310546875, -.99993896484375, .00872802734375, 0.)
        front = (-.00067138671875, .95428466796875, -.29888916015625, .00323486328125)
        initial = self.motion(board, 0., rest)
        for k in range(1, 31):
            final = self.motion(board, k / 30, front)
        height = lambda frame: sum(i // 8 for i, c in enumerate(frame) if level(c))
        self.assertLess(height(final), height(initial))

    def test_vertical_and_upside_down_rest_poses_stay_still_and_accept_tilt(self):
        for game in ('sand', 'marble_maze'):
            for neutral, tilt in ((90, 60), (180, 160)):
                with self.subTest(game=game, neutral=neutral):
                    _, board = self.play(game)
                    angle = math.radians(neutral) / 2
                    pose = (0., math.sin(angle), 0., math.cos(angle))
                    initial = self.motion(board, 0., pose)
                    self.assertEqual(self.motion(board, .1, pose), initial)
                    angle = math.radians(tilt) / 2
                    pose = (0., math.sin(angle), 0., math.cos(angle))
                    for k in range(1, 25):
                        moved = self.motion(board, .1 + k / 30, pose)
                    self.assertNotEqual(moved, initial)

    def test_configured_table_rest_does_not_recenter_to_the_entry_angle(self):
        _, board = self.play('marble_maze')
        settings = board.config
        settings['tabletop_gravity'] = [0., 0., 1.]
        board.configure(settings)
        board.press(2, 0, 127, 0.)
        entry = math.radians(160) / 2
        initial = self.motion(board, 0., (math.sin(entry), 0., 0., math.cos(entry)))
        rest = (-.0001220703125, 1., -.00274658203125, .0020751953125)
        for k in range(1, 16):
            self.assertEqual(self.motion(board, k / 30, rest), initial)

    def test_duel_hitting_a_signal_awards_the_player_and_tricks_award_the_opponent(self):
        _, board = self.play('duel')
        pads(board, 0.)
        board.press(1, 1, 127, .1)
        waiting = pads(board, .1)
        for step in range(1, 51):
            now = .1 + step / 10
            frame = pads(board, now)
            signals = [i for i, (a, b) in enumerate(zip(waiting, frame)) if a != b]
            if signals:
                break
        else:
            self.fail('No signal appeared')
        self.assertEqual(len(signals), 2)
        target = next(i for i in signals if i % 8 >= 4)
        genuine = len(set(frame[target])) == 1  # The genuine signal is white; tricks are red.
        if not genuine:
            target = next(i for i in signals if i % 8 < 4)
        board.press(target % 8 + 1, target // 8 + 1, 127, now)
        awarded = pads(board, now)
        self.assertGreater(level(awarded[7]), level(waiting[7]))
        self.assertEqual(awarded[0], waiting[0])

    def test_mines_a_finished_round_can_be_started_again(self):
        _, board = self.play('mines')
        covered = pads(board, 0.)
        # Uncover every available pad: the round must finish by a mine or by clearing the safe pads.
        for i in range(64):
            now = 1. + i / 100
            board.press(i % 8 + 1, i // 8 + 1, 127, now)
            board.release(i % 8 + 1, i // 8 + 1, now + .001)
        self.assertNotEqual(pads(board, 2.), covered)
        board.press(1, 1, 127, 4.)
        self.assertEqual(pads(board, 4.), covered)


if __name__ == '__main__':
    unittest.main()
