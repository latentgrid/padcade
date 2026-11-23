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


class DigitalSand:
    """Tilt to pour; tap a pad to add or remove a grain."""
    colour = (255, 170, 40)
    palette = ((255, 170, 40), (255, 80, 35), (255, 220, 70), (235, 65, 145), (90, 210, 230))

    def __init__(self):
        self.start(0)

    def start(self, now):
        self.tilt = Tilt()
        self.tilt.previous = now
        self.motion_ready = False
        self.reset()

    def reset(self):
        self.grains = {(x, y): self.palette[(x + y) % len(self.palette)]
                       for y in range(2, 6) for x in range(1, 6)}
        self.travel = 0.

    def calibrate(self):
        self.tilt.calibrate()
        self.travel = 0.

    def touch(self, x, y, now):
        if 0 <= x < 8 and 0 <= y < 8:
            if (x, y) in self.grains:
                del self.grains[x, y]
            else:
                self.grains[x, y] = self.palette[(x + y) % len(self.palette)]

    def frame(self, now, board):
        reading = self.tilt.read(now, board)
        self.motion_ready = reading is not None
        if reading is not None:
            dt, gx, gy = reading
            self.travel += math.hypot(gx, gy) * dt * 14
            while self.travel >= 1:
                self.travel -= 1
                directions = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                              if dx * gx + dy * gy > 0]
                directions.sort(key=lambda d: (d[0]*gx + d[1]*gy) / math.hypot(*d), reverse=True)
                for x, y in sorted(self.grains, key=lambda p: p[0]*gx + p[1]*gy, reverse=True):
                    for dx, dy in directions:
                        target = (x + dx, y + dy)
                        if 0 <= target[0] < 8 and 0 <= target[1] < 8 and target not in self.grains:
                            self.grains[target] = self.grains.pop((x, y))
                            break
        picture = [BLACK] * 64
        for (x, y), colour in self.grains.items():
            picture[8*y + x] = colour
        return picture


class Plugin(DigitalSand):
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

    def lights(self, now):
        return {'recenter': (0, 170, 220) if getattr(self, 'motion_ready', False) else (220, 100, 0)}
