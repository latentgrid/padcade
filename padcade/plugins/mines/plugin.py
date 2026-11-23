import random

from padcade.kit import BLACK, WHITE, mix


NUMBERS = (None, (0, 200, 0), (0, 90, 255), (255, 0, 255), (0, 210, 210), (130, 0, 255), WHITE, (150, 150, 150), (140, 200, 255))  # (none red, orange or yellow: the mines and flags are)


class Mines:
    """Mines: ten mines under the grey pads. A tap uncovers a pad: its colour says how many mines touch it (green
    one, blue two, magenta three, cyan four, then purple, white, grey, pale blue), and none opens up all round it; holding a
    pad (it fills orange) flags it, or takes the flag off. Uncover a mine and it's over, every mine shown red; uncover
    everything else and you've won, the mines shown green. A press after starts a new one."""
    colour, COUNT, LONG = (150, 150, 165), 10, .45

    def __init__(self):
        self.start(0.)

    def start(self, now):
        self.rng, self.mines, self.revealed, self.flags = random.Random(), set(), set(), set()
        self.ended, self.won, self.pressed, self.boom = None, False, None, None

    def around(self, x, y):
        return [(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx or dy) and 0 <= x + dx < 8 and 0 <= y + dy < 8]

    def reveal(self, x, y, now):
        if not self.mines:  # (laid after the first tap, never under or round it)
            free = sorted({(a, b) for a in range(8) for b in range(8)} - {(x, y)} - set(self.around(x, y)))
            self.mines = set(self.rng.sample(free, self.COUNT))
        if (x, y) in self.mines:
            self.ended, self.boom = now, (x, y)
            return
        opening = [(x, y)]
        while opening:
            q = opening.pop()
            if q not in self.revealed:
                self.revealed.add(q)
                self.flags.discard(q)
                if not any(r in self.mines for r in self.around(*q)):
                    opening += self.around(*q)
        if len(self.revealed) == 64 - self.COUNT:
            self.ended, self.won = now, True

    def touch(self, x, y, now):
        if self.ended is None:
            self.pressed = (x, y, now)
        elif now - self.ended > 1.5:
            self.start(now)

    def hold(self, x, y, value, now):
        if value or not self.pressed or self.pressed[:2] != (x, y):
            return
        at, self.pressed = self.pressed[2], None
        if now - at < self.LONG and (x, y) not in self.flags | self.revealed:  # (a tap)
            self.reveal(x, y, now)

    def frame(self, now):
        if self.pressed and now - self.pressed[2] >= self.LONG:  # (held long enough: a flag, or one taken off)
            x, y, _ = self.pressed
            self.pressed = None
            if (x, y) not in self.revealed:
                self.flags ^= {(x, y)}
        picture = []
        for y in range(8):
            for x in range(8):
                if self.ended is not None and (x, y) in self.mines:
                    colour = (0, 220, 60) if self.won else WHITE if (x, y) == self.boom and int(now * 6) % 2 else (255, 0, 0)
                elif (x, y) in self.revealed:
                    colour = NUMBERS[sum(r in self.mines for r in self.around(x, y))] or (10, 10, 16)
                else:
                    colour = (255, 110, 0) if (x, y) in self.flags else (45, 45, 55)
                    if self.pressed and self.pressed[:2] == (x, y):  # (filling orange as it's held)
                        colour = mix(colour, (255, 110, 0), (now - self.pressed[2]) / self.LONG)
                picture.append(colour)
        return picture


class Plugin(Mines):
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
        if self.started:
            self.hold(x, y, 0, now)
