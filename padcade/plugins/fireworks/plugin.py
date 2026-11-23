import colorsys
import math
import random

from padcade.kit import Stepped


class Fireworks(Stepped):
    """Fireworks: a spark rises from the bottom and bursts into a ring of falling, fading colour. A press sends one up
    from its column to burst where it's pressed, the others quiet for four seconds."""
    def __init__(self):
        self.rng, self.done, self.shells, self.sparks, self.wait = random.Random(9), 0, [], [], 0
        self.quiet = 0  # (ticks until it sends its own up again, after one of yours)

    def tick(self):
        dt = 1 / self.RATE
        self.wait, self.quiet = self.wait - 1, max(0, self.quiet - 1)
        if self.wait <= 0 and not self.quiet:
            self.shells.append([self.rng.uniform(1, 6), 0., self.rng.uniform(4.5, 6.5), self.rng.random()])  # x, y, burst height, hue
            self.wait = self.rng.randint(12, 26)
        for shell in self.shells[:]:
            shell[1] += 9 * dt
            if shell[1] >= shell[2]:
                self.shells.remove(shell)
                n = self.rng.randint(9, 13)
                for k in range(n):
                    angle, speed = 2 * math.pi * k / n, self.rng.uniform(3.5, 5.5)
                    self.sparks.append([shell[0], shell[1], math.cos(angle) * speed, math.sin(angle) * speed, 1.3, shell[3]])
        for spark in self.sparks:
            spark[0] += spark[2] * dt
            spark[1] += spark[3] * dt
            spark[3] -= 3 * dt  # (gravity)
            spark[4] -= dt
        self.sparks = [s for s in self.sparks if s[4] > 0]

    def touch(self, x, y, t):
        self.shells.append([x, 0., max(2., y), self.rng.random()])  # (one sent up from the pad's column, to burst there,
        self.quiet = 4 * self.RATE  # its own quiet for four seconds)
        return t

    def draw(self):
        picture = [[0., 0., 0.] for _ in range(64)]
        for x, y, *_ in self.shells:
            if 0 <= round(y) < 8:
                picture[8 * round(y) + round(x)] = [255., 230., 160.]
        for x, y, _, _, life, hue in self.sparks:
            xi, yi = round(x), round(y)
            if 0 <= xi < 8 and 0 <= yi < 8:
                rgb = colorsys.hsv_to_rgb(hue, 1, 1)
                cell = picture[8 * yi + xi]
                for k in range(3):
                    cell[k] = min(255., cell[k] + 255 * rgb[k] * min(1., life) ** .5)
        return [tuple(c) for c in picture]



class Plugin(Fireworks):
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
