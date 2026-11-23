import random

from padcade.kit import BLACK, WHITE, mix, Stepped


class Snake(Stepped):
    """Snake playing itself: it heads for the apple the shortest way it can, grows, and starts over when it's stuck."""
    RATE = 6

    def __init__(self):
        self.rng, self.done = random.Random(3), 0
        self.reset()

    def reset(self):
        self.body, self.flash = [(3, 3), (2, 3), (1, 3)], 0
        self.apple = self.free()

    def free(self):
        return self.rng.choice([(x, y) for x in range(8) for y in range(8) if (x, y) not in self.body])

    def tick(self):
        if self.flash:
            self.flash -= 1
            if not self.flash:
                self.reset()
            return
        (hx, hy), (ax, ay) = self.body[0], self.apple
        moves = [(hx + dx, hy + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        moves = [m for m in moves if 0 <= m[0] < 8 and 0 <= m[1] < 8 and m not in self.body[:-1]]
        if not moves or len(self.body) >= 24:
            self.flash = 4  # (stuck, or long enough: a flash, then over again)
            return
        head = min(moves, key=lambda m: (abs(m[0] - ax) + abs(m[1] - ay), self.rng.random()))
        self.body.insert(0, head)
        if head == self.apple:
            self.apple = self.free()
        else:
            self.body.pop()

    def touch(self, x, y, t):
        if (x, y) not in self.body:
            self.apple = (x, y)  # (dropped where it's pressed)
        return t

    def draw(self):
        picture = [BLACK] * 64
        for k, (x, y) in enumerate(self.body):
            picture[8 * y + x] = WHITE if self.flash and self.flash % 2 else mix((130, 255, 60), (0, 110, 40), k / max(1, len(self.body) - 1))
        picture[8 * self.apple[1] + self.apple[0]] = (255, 0, 40)
        return picture



class Plugin(Snake):
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
