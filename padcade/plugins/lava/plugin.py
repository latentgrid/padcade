import math
from padcade.kit import mix

LOOP = 12.


def lava(t, presses=()):
    """Lava: three blobs rising and sinking, melting into each other. A press warms a new blob where it's
    pressed, which swells and rises and melts away at the top."""
    turn = 2 * math.pi / LOOP  # (each blob up and down once a loop, swaying once or twice: so it loops)
    blobs = [(1.3 + k * 2.6 + .7 * math.sin(t * turn * (1 + k % 2) + k * 2), 3.5 + 3.4 * math.sin(t * turn + k * 2.1), 1.1 + .15 * k)
             for k in range(3)]
    for x, y, pressed in presses[-6:]:  # (yours: swelling, rising, shrinking away near the top)
        s = t - pressed
        rise = y + 1.1 * s
        if s < 8 and rise < 9:
            blobs.append((x + .5 * math.sin(s * 1.7), rise, 1.1 * min(1., s / .6) * min(1., (8.5 - rise) / 2.5)))
    picture = []
    for y in range(8):
        for x in range(8):
            field = sum(r * r / ((x - bx) ** 2 + (y - by) ** 2 + .3) for bx, by, r in blobs) + .55 / (y + .4)  # (the hot pool below)
            if field > 1.:
                picture.append(mix((255, 60, 0), (255, 200, 40), min(1., (field - 1) / 2)))
            else:
                picture.append(mix((25, 0, 40), (90, 0, 70), y / 7))
    return picture

class Plugin:
    def __init__(self, board):
        self.started, self.presses, self.held = None, [], {}

    def frame(self, now):
        if self.started is None:
            self.started = now
        t = now - self.started
        return lava(t, [p for p in self.presses if p[2] <= t])

    def press(self, x, y, velocity, now):
        if self.started is None:
            self.started = now
        t = now - self.started
        self.held[(x, y)] = velocity / 127
        self.presses = [p for p in self.presses if t - p[2] < 60] + [(x, y, t)]

    def release(self, x, y, now):
        self.held.pop((x, y), None)
