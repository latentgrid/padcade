import math
import random

from padcade.kit import BLACK, Stepped


def nearest(v):
    """The pad a position falls on: halves go up (Python's round() takes 2.5 and 3.5 to the even pad, 2 and 4: a paddle between two rows jumped two)."""
    return math.floor(v + .5)



class Paddles(Stepped):
    """Two paddles playing each other: chasing the ball, now and then one too slow (that side flashes red). A pad held
    on a side moves that side's paddle to its row. The paddles are three whole pads (their centres stay on rows 1 to 6),
    the ball hits what is drawn, and it never goes faster than FASTEST: it sped up 3% a hit for ever, was through the
    paddle by the hundredth, and drawn off the pads (a crash) soon after."""
    FASTEST = 9.  # pads a second, either way: a pad is never skipped in a frame (30 a second)

    def __init__(self):
        self.rng, self.done, self.paddles, self.missed, self.held = random.Random(5), 0, [3.5, 3.5], (None, 0), {}
        self.serve()

    def serve(self):
        self.ball = [3.5, 3.5]
        self.velocity = [self.rng.choice((-5., 5.)), self.rng.uniform(-3., 3.)]
        self.speeds = [self.rng.uniform(3., 6.) for _ in range(2)]  # (how fast each paddle follows this rally)

    def tick(self):
        side, left = self.missed
        if left:
            self.missed = (side, left - 1)
            if left == 1:
                self.serve()
            return
        dt = 1 / self.RATE
        for k in (0, 1):  # (each following the ball, or the row held on its half)
            rows = [y for x, y in self.held if (x < 4) == (k == 0)]
            step = (12 if rows else self.speeds[k]) * dt
            self.paddles[k] = min(6., max(1., self.paddles[k] + max(-step, min(step, (rows[-1] if rows else self.ball[1]) - self.paddles[k]))))
        self.ball = [self.ball[0] + self.velocity[0] * dt, self.ball[1] + self.velocity[1] * dt]
        if not 0 <= self.ball[1] <= 7:
            self.velocity[1] = -self.velocity[1]
            self.ball[1] = min(7., max(0., self.ball[1]))
        for k, edge in ((0, 1.), (1, 6.)):
            if (self.ball[0] <= edge if k == 0 else self.ball[0] >= edge) and (self.velocity[0] < 0) == (k == 0):
                if abs(nearest(self.ball[1]) - nearest(self.paddles[k])) <= 1:  # (the pad it is drawn on is one of the paddle's)
                    fastest = lambda v: max(-self.FASTEST, min(self.FASTEST, v))
                    self.velocity = [fastest(-self.velocity[0] * 1.03), fastest(self.velocity[1] + self.rng.uniform(-1.5, 1.5))]
                else:
                    self.missed = (k, 20)

    def draw(self):
        picture = [BLACK] * 64
        side, left = self.missed
        if left and left % 4 < 2:
            for y in range(8):
                picture[8 * y + (0 if side == 0 else 7)] = (255, 0, 0)
        for k, x in ((0, 0), (1, 7)):
            for dy in (-1, 0, 1):
                picture[8 * (nearest(self.paddles[k]) + dy) + x] = (180, 200, 255)
        if not left:
            picture[8 * min(7, max(0, nearest(self.ball[1]))) + min(7, max(0, nearest(self.ball[0])))] = (255, 220, 0)
        return picture



class Plugin(Paddles):
    def __init__(self, board):
        super().__init__()
        self.started, self.ahead = None, 0.
        self.held = {}

    def frame(self, now):
        if self.started is None:
            self.started = now
        return self(now - self.started + self.ahead)

    def press(self, x, y, velocity, now):
        if self.started is None:
            self.started = now
        self.held[(x, y)] = velocity / 127

    def release(self, x, y, now):
        self.held.pop((x, y), None)
