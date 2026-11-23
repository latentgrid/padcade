import math

from padcade.kit import BLACK


class Tilt:
    """Neutral-relative tilt, with a small dead zone and a short smoothing window."""
    def __init__(self):
        self.previous = None
        self.calibrate(tabletop=True)

    def calibrate(self, tabletop=False):
        self.axes = None
        self.filtered = (0., 0.)
        self.tabletop = tabletop

    def read(self, now, board):
        dt = 0 if self.previous is None else min(.05, max(0., now - self.previous))
        self.previous = now
        sample = board.motion(now)
        if sample is None:
            return None
        q = sample.game_orientation
        if len(q) != 4 or not all(map(math.isfinite, q)):
            return None
        length = math.sqrt(sum(v * v for v in q))
        if not .5 < length < 1.5:
            return None
        x, y, z, w = (v / length for v in q)
        # Gravity ignores yaw drift, including when recentered at an angled holding pose.
        gravity = (2 * (w*y - x*z), -2 * (w*x + y*z), 2 * (x*x + y*y) - 1)
        if self.axes is None:
            neutral = getattr(board, 'tabletop_gravity', gravity) if self.tabletop else gravity
            # Keep right along sensor X, and use the whole neutral plane even when mounted upside down.
            right = tuple((i == 0) - neutral[0] * g for i, g in enumerate(neutral))
            length = math.sqrt(sum(v*v for v in right))
            if length < 1e-6:  # Sensor X points straight up/down, so its horizontal direction is undefined.
                right = tuple((i == 1) - neutral[1] * g for i, g in enumerate(neutral))
                length = math.sqrt(sum(v*v for v in right))
            right = tuple(v / length for v in right)
            up = (right[1]*neutral[2] - right[2]*neutral[1],
                  right[2]*neutral[0] - right[0]*neutral[2],
                  right[0]*neutral[1] - right[1]*neutral[0])
            self.axes = (right, up)
        gx, gy = (sum(g*a for g, a in zip(gravity, axis)) for axis in self.axes)
        magnitude = math.hypot(gx, gy)
        scale = min(1., max(0., magnitude - .07) / .93) / magnitude if magnitude else 0
        alpha = 1 - math.exp(-dt / .10)
        self.filtered = tuple(old + alpha * (value * scale - old)
                              for old, value in zip(self.filtered, (gx, gy)))
        return dt, *self.filtered


class MarbleMaze:
    """Roll to green, avoid red, and hold the brake for narrow turns. Tap to retry."""
    colour = (100, 220, 100)
    wall_colour, hole_colour, goal_colour = (0, 60, 220), (240, 0, 0), (0, 220, 70)
    levels = (
        ('########', '#.....G#', '#......#', '#......#', '#..#...#', '#..#.O.#', '#S.#...#', '########'),
        ('########', '#.....G#', '#.O#####', '#......#', '#####..#', '#..O...#', '#S.....#', '########'),
        ('########', '#.O..#G#', '#O...#.#', '#..#.#.#', '#.O#.#.#', '#..#...#', '#S.#O..#', '########'),
    )

    def __init__(self):
        self.start(0)

    def start(self, now):
        self.tilt = Tilt()
        self.tilt.previous = now
        self.motion_ready = False
        self.brake = False
        self.level = 0
        self.reset()

    def reset(self):
        cells = {(x, y): ch for y, row in enumerate(reversed(self.levels[self.level])) for x, ch in enumerate(row)}
        self.walls = {p for p, ch in cells.items() if ch == '#'}
        self.holes = {p for p, ch in cells.items() if ch == 'O'}
        self.goal = next(p for p, ch in cells.items() if ch == 'G')
        self.ball = list(next(p for p, ch in cells.items() if ch == 'S'))
        self.velocity = [0., 0.]

    def calibrate(self):
        self.tilt.calibrate()
        self.velocity = [0., 0.]

    def touch(self, x, y, now):
        self.reset()

    def clear(self, x, y):
        radius = .22
        if not -.5 + radius <= x <= 7.5 - radius or not -.5 + radius <= y <= 7.5 - radius:
            return False
        return all(max(abs(x - wx) - .5, 0)**2 + max(abs(y - wy) - .5, 0)**2 >= radius**2
                   for wx, wy in self.walls)

    def frame(self, now, board):
        reading = self.tilt.read(now, board)
        self.motion_ready = reading is not None
        if reading is not None:
            dt, gx, gy = reading
            acceleration = 14 if self.brake else 60
            drag = math.exp(-(14 if self.brake else .8) * dt)
            self.velocity = [(v + acceleration*g*dt) * drag for v, g in zip(self.velocity, (gx, gy))]
            # At most a tenth of a pad per collision check, including after a long pause.
            steps = max(1, math.ceil(max(map(abs, self.velocity)) * dt / .1))
            for _ in range(steps):
                for axis in (0, 1):
                    target = self.ball.copy()
                    target[axis] += self.velocity[axis] * dt / steps
                    if self.clear(*target):
                        self.ball = target
                    else:
                        self.velocity[axis] = 0.
                if any(math.dist(self.ball, hole) < .36 for hole in self.holes):
                    self.reset()
                    break
                if math.dist(self.ball, self.goal) < .36:
                    self.level = (self.level + 1) % len(self.levels)
                    self.reset()
                    break
        picture = [BLACK] * 64
        for x, y in self.walls:
            picture[8*y + x] = self.wall_colour
        for x, y in self.holes:
            picture[8*y + x] = self.hole_colour
        picture[8*self.goal[1] + self.goal[0]] = self.goal_colour
        x, y = self.ball
        for px in (math.floor(x), math.floor(x) + 1):
            for py in (math.floor(y), math.floor(y) + 1):
                if 0 <= px < 8 and 0 <= py < 8:
                    white = round(255 * (1 - abs(px - x)) * (1 - abs(py - y)))
                    picture[8*py + px] = tuple(max(c, white) for c in picture[8*py + px])
        return picture


class Plugin(MarbleMaze):
    def __init__(self, board):
        self.board, self.started = board, False

    def begin(self, now):
        if not self.started:
            self.start(now)
            self.started = True

    def frame(self, now):
        self.begin(now)
        return super().frame(now, self.board)

    def press(self, x, y, velocity, now):
        self.begin(now)
        self.touch(x, y, now)

    def release(self, x, y, now):
        pass

    def action(self, name, now):
        self.begin(now)
        if name == 'recenter':
            self.calibrate()
        elif name == 'brake':
            self.brake = True

    def released(self, name, now):
        if name == 'brake':
            self.brake = False

    def close(self):
        self.brake = False

    def lights(self, now):
        return {'recenter': (0, 170, 220) if getattr(self, 'motion_ready', False) else (220, 100, 0),
                'brake': (255, 255, 255) if getattr(self, 'brake', False) else (60, 60, 60)}
