import math
import random

from padcade.kit import BLACK, mix, smooth, Stepped


class Fire(Stepped):
    """A campfire: two logs crossed on the bottom rows, embers glowing between them; a flame over them tapering to a
    tip, flickering and swaying, now and then a lick of it breaking off and going out; sparks drifting up into the
    dark. A press flares it up into a huge flame, right to the top, sparks flying; it dies back down in a second or
    two. Held, it's stoked: the harder, the higher it burns (growing, and dying down again, gradually)."""
    RATE = 20
    RAMP = ((110, 0, 0), (220, 30, 0), (255, 90, 0), (255, 170, 10), (255, 235, 120))  # (tip to core)
    TALL = (0, 1.3, 2.7, 4.7, 4.7, 2.7, 1.3, 0)  # (the flame's height over each column, at rest)
    FLARE = (1.5, 4.5, 7.5, 10, 10, 7.5, 4.5, 1.5)  # (and flared up)
    LOGS = ('L......L', '.LLEELL.')  # (their ends up at the sides; the embers in the middle)

    def __init__(self):
        self.rng, self.done, self.sparks, self.licks, self.held, self.stoke = random.Random(4), 0, [], [], {}, 0.
        self.boost = self.flare = 0.

    def reset(self):
        self.stoke = self.boost = self.flare = 0.

    def touch(self, x, y, t):
        self.boost = 1.
        self.sparks += [[self.rng.uniform(1.5, 5.5), self.rng.uniform(3, 6), 1.] for _ in range(8)]  # (a shower of sparks)
        return t

    def tick(self):
        rng = self.rng
        self.stoke += (max(self.held.values(), default=0.) - self.stoke) * .15
        self.boost = max(0., self.boost - 1 / (1.5 * self.RATE))  # (a press's flare: up at once, dying down)
        self.flare += (self.boost - self.flare) * .5
        if rng.random() < .06 + .3 * self.stoke:
            self.sparks.append([rng.uniform(2.5, 4.5), 4., 1.])  # x, y, life
        if rng.random() < .08 + .2 * self.stoke:  # (a lick of flame breaking off a middle column's tip)
            x = rng.choice((2, 3, 4, 5))
            self.licks.append([x, 1 + self.height(x), 1.])
        for spark in self.sparks:
            spark[0] += rng.uniform(-.25, .25)
            spark[1] += .25
            spark[2] -= .04
        for lick in self.licks:
            lick[1] += .35
            lick[2] -= .3
        self.sparks = [sp for sp in self.sparks if sp[2] > 0 and sp[1] < 8]
        self.licks = [lick for lick in self.licks if lick[2] > 0]

    def height(self, x):
        """The flame's height over a column now: flickering (each column its own way, all of it breathing together)."""
        t = self.done / self.RATE
        tall = self.TALL[x] + (self.FLARE[x] - self.TALL[x]) * self.flare
        return tall * (.75 + .3 * smooth(x, t * 7) + .15 * smooth(9, t * 3)) * (1 + .7 * self.stoke)

    def draw(self):
        t, picture = self.done / self.RATE, [BLACK] * 64
        lean = .7 * math.sin(t * 1.7)  # (swaying, the top more than the bottom)
        for y in range(1, 8):
            for x in range(8):
                column = min(7, max(0, round(x - lean * (y - 1) / 4)))
                tall, up = self.height(column), y - .5
                if up < tall:
                    heat = (1 - up / tall) * (1 - abs(column - 3.5) / 7)
                    colour = mix(self.RAMP[min(3, int(heat * 4))], self.RAMP[min(4, int(heat * 4) + 1)], heat * 4 % 1)
                    picture[8 * y + x] = mix(BLACK, colour, min(1., (tall - up) * 2))  # (its tip faint)
        for x, y, life in self.licks:
            if round(y) < 8:
                picture[8 * round(y) + x] = mix(BLACK, self.RAMP[1], life)
        for x, y, life in self.sparks:
            cell = 8 * round(y) + max(0, min(7, round(x)))
            if round(y) < 8 and sum(picture[cell]) < 150:
                picture[cell] = mix(BLACK, (255, 200, 60), life)
        glow = .55 + .45 * smooth(5, t * 4)
        for top, row in enumerate(self.LOGS):  # (the logs, and the embers pulsing)
            for x, ch in enumerate(row):
                if ch != '.':
                    picture[8 * (1 - top) + x] = (110, 50, 15) if ch == 'L' else mix((150, 20, 0), (255, 110, 0), glow)
        return picture



class Plugin(Fire):
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
        t = now - self.started + self.ahead
        self.ahead += self.touch(x, y, t) - t

    def release(self, x, y, now):
        self.held.pop((x, y), None)
