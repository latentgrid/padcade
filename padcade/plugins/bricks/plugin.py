import math
import random

from padcade.kit import BLACK, WHITE, Stepped


def nearest(v):
    """The pad a position falls on: halves go up (Python's round() takes 2.5 and 3.5 to the even pad, 2 and 4: a paddle between two rows jumped two)."""
    return math.floor(v + .5)



class Bricks(Stepped):
    """A wall of bricks played by itself: the paddle follows the ball, the ball clears the two rows of bricks, then a fresh wall.
    A pad held moves the paddle to its column. The paddle is three whole pads (its centre stays on columns 1 to 6), and
    the ball bounces off it, not off the air: past it the ball is lost (the floor blinks red) and served again."""
    def __init__(self):
        self.rng, self.done, self.flash, self.lost, self.held = random.Random(11), 0, 0, 0, {}
        self.reset()

    def reset(self):
        self.bricks = {(x, y) for x in range(8) for y in (6, 7)}
        self.ball, self.velocity, self.paddle = [3.5, 1.], [self.rng.choice((-3.5, 3.5)), 4.5], 3.5

    def tick(self):
        if self.flash:
            self.flash -= 1
            if not self.flash:
                self.reset()
            return
        if self.lost:
            self.lost -= 1
            if not self.lost:  # (served again from the top; the wall stays as it is)
                self.ball, self.velocity = [3.5, 1.], [self.rng.choice((-3.5, 3.5)), 4.5]
            return
        dt = 1 / self.RATE
        columns = [x for x, _ in self.held]  # (following the ball, or the column held)
        self.paddle = min(6., max(1., self.paddle + max(-.3, min(.3, (columns[-1] if columns else self.ball[0]) - self.paddle))))
        x, y = self.ball[0] + self.velocity[0] * dt, self.ball[1] + self.velocity[1] * dt
        if not 0 <= x <= 7:
            self.velocity[0], x = -self.velocity[0], min(7., max(0., x))
        if y >= 7.5:
            self.velocity[1], y = -abs(self.velocity[1]), 7.4
        hit = (round(x), round(y))
        if hit in self.bricks:
            self.bricks.discard(hit)
            self.velocity[1] = -self.velocity[1]
            if not self.bricks:
                self.flash = 24
        if y <= .6 and self.velocity[1] < 0:
            if abs(nearest(x) - nearest(self.paddle)) > 1:  # (not over the paddle: through)
                self.ball, self.lost = [x, y], 18
                return
            self.velocity = [self.velocity[0] + (x - self.paddle) * 1.5 + self.rng.uniform(-.4, .4), abs(self.velocity[1])]
            self.velocity[0] = max(-5., min(5., self.velocity[0]))
            y = .6
        self.ball = [x, y]

    def draw(self):
        picture = [BLACK] * 64
        rows = {7: (255, 40, 40), 6: (255, 160, 0)}
        for x, y in self.bricks:
            picture[8 * y + x] = WHITE if self.flash and self.flash % 6 < 3 else rows[y]
        if self.lost and self.lost % 4 < 2:  # (a ball lost: the floor blinks red)
            picture[:8] = [(255, 0, 0)] * 8
        for dx in (-1, 0, 1):
            picture[nearest(self.paddle) + dx] = (80, 160, 255)
        if not (self.flash or self.lost):
            picture[8 * min(7, max(0, nearest(self.ball[1]))) + min(7, max(0, nearest(self.ball[0])))] = WHITE
        return picture



class Plugin(Bricks):
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
