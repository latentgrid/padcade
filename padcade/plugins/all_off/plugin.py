import random
import colorsys

from padcade.kit import BLACK, mix


class AllOff:
    """All off: a press switches its pad and the four next to it, on or off; put them all out. Then a rainbow, and
    the next, one press harder."""
    colour = (255, 170, 40)

    def __init__(self):
        self.start(0.)

    def start(self, now):
        self.rng, self.level = random.Random(), 0
        self.new()

    def new(self):
        self.on, self.solution, self.won = set(), set(), None
        while not self.on:  # (scrambled from all out, so it can be solved: those presses again)
            for x, y in self.rng.sample([(x, y) for x in range(8) for y in range(8)], min(14, 3 + self.level)):
                self.flip(x, y)

    def flip(self, x, y):
        self.solution ^= {(x, y)}
        for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
            if 0 <= x + dx < 8 and 0 <= y + dy < 8:
                self.on ^= {(x + dx, y + dy)}

    def touch(self, x, y, now):
        if self.won is None:
            self.flip(x, y)
            if not self.on:
                self.won, self.level = now, self.level + 1

    def frame(self, now):
        if self.won is not None and now - self.won >= 1.5:
            self.new()
        if self.won is not None:  # (a rainbow rolling across, fading)
            s = now - self.won
            return [mix(tuple(255 * c for c in colorsys.hsv_to_rgb(((x + y) / 14 - s * 1.5) % 1, 1, 1)), BLACK, s / 1.5)
                    for y in range(8) for x in range(8)]
        return [(255, 170, 40) if (x, y) in self.on else (0, 12, 35) for y in range(8) for x in range(8)]


class Plugin(AllOff):
    def __init__(self, board):
        self.started = False

    def frame(self, now):
        if not self.started:
            self.start(now)
            self.started = True
        return super().frame(now)

    def press(self, x, y, velocity, now):
        if not self.started:
            self.start(now)
            self.started = True
        self.touch(x, y, now)

    def release(self, x, y, now):
        pass
