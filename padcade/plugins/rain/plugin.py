from padcade.kit import BLACK, WHITE, mix, noise

LOOP = 12.


def rain(t, presses=()):
    """Falling green rain: every column a falling streak, its head bright, its tail fading green. A press sends a fresh
    one down its column."""
    picture = [BLACK] * 64
    for x in range(8):
        trail, gap = 3 + int(noise(x, 2) * 3), 2 + int(noise(x, 4) * 6)
        period = 8 + trail + gap
        speed = period * (2 + int(noise(x, 1) * 4)) / LOOP  # (falling a whole number of times a loop)
        head = (t * speed + noise(x, 3) * period) % period  # rows from the top
        for k in range(trail + 1):
            row = int(head) - k
            if 0 <= row < 8:
                picture[8 * (7 - row) + x] = (190, 255, 190) if k == 0 else tuple(c * (1 - k / (trail + 1)) for c in (0, 255, 70))
    for x, _, pressed in presses:
        head = int((t - pressed) * 14)
        for k in range(5):
            if 0 <= head - k < 8:
                picture[8 * (7 - head + k) + x] = WHITE if k == 0 else mix((0, 255, 90), BLACK, k / 5)
    return picture

class Plugin:
    def __init__(self, board):
        self.started, self.presses, self.held = None, [], {}

    def frame(self, now):
        if self.started is None:
            self.started = now
        t = now - self.started
        return rain(t, [p for p in self.presses if p[2] <= t])

    def press(self, x, y, velocity, now):
        if self.started is None:
            self.started = now
        t = now - self.started
        self.held[(x, y)] = velocity / 127
        self.presses = [p for p in self.presses if t - p[2] < 60] + [(x, y, t)]

    def release(self, x, y, now):
        self.held.pop((x, y), None)
