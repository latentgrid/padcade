import random

from padcade.kit import BLACK, WHITE, mix, pad, counted


HOLES = [(x, y) for y in (6, 3, 0) for x in (0, 3, 6)]  # The moles' nine holes, 2 by 2: their bottom left pads


class Moles:
    """Moles: moles pop up out of nine holes; hit one (any pad of it) before it ducks back down. The first hit
    starts a 30-second round, the moles coming faster and faster, a golden one worth three, the grass flashing red for
    the last three seconds; then the score, a gold pad a point. A press after starts the next round."""
    colour, ROUND = (200, 110, 30), 30.

    def __init__(self):
        self.start(0.)

    def start(self, now):
        self.rng, self.phase, self.score, self.moles, self.next, self.began = random.Random(), 'ready', 0, [], now + .5, now

    def begin(self, now):
        self.phase, self.score, self.began = 'play', 0, now

    def frame(self, now):
        if self.phase == 'play' and now >= self.began + self.ROUND:
            self.phase, self.ended, self.moles = 'score', now, []
        if self.phase == 'score':  # (the score counting up)
            return counted([(0, 15, 0)] * 64, min(self.score, int((now - self.ended) / .05)), (255, 190, 0))
        pace = min(1., (now - self.began) / self.ROUND) if self.phase == 'play' else 0.
        if now >= self.next:  # (a mole up, out of a free hole)
            free = [h for h in range(9) if not any(m[0] == h for m in self.moles)]
            if free:
                self.moles.append([self.rng.choice(free), now, now + (1.3 if self.phase == 'ready' else 1.15 - .5 * pace),
                                   self.rng.random() < .12, None])
            self.next = now + (1.4 if self.phase == 'ready' else .95 - .55 * pace) * self.rng.uniform(.6, 1.2)
        self.moles = [m for m in self.moles if now < (m[4] + .4 if m[4] else m[2])]
        late = self.phase == 'play' and self.began + self.ROUND - now < 3 and int(now * 4) % 2
        picture = [(45, 0, 0) if late else (0, 14, 0)] * 64  # (the grass)
        for x0, y0 in HOLES:
            for dx in (0, 1):
                for dy in (0, 1):
                    pad(picture, x0 + dx, y0 + dy, (14, 7, 2))
        for hole, up, down, golden, hit in self.moles:
            x0, y0 = HOLES[hole]
            if hit is not None:  # (hit: a white flash, then stars flying off the corners)
                s = now - hit
                for dx in (0, 1):
                    for dy in (0, 1):
                        if s < .12:
                            pad(picture, x0 + dx, y0 + dy, WHITE)
                        else:
                            pad(picture, x0 + 3 * dx - 1, y0 + 3 * dy - 1, mix((255, 230, 80), BLACK, (s - .12) / .28))
                continue
            for dx in (0, 1):  # (its body out of the hole; its head too, once it's right up)
                pad(picture, x0 + dx, y0, (200, 150, 0) if golden else (170, 100, 40))
                if now - up >= .1 and down - now >= .1:
                    pad(picture, x0 + dx, y0 + 1, (255, 210, 0) if golden else (255, 190, 120))
        return picture

    def touch(self, x, y, now):
        if self.phase == 'score':
            if now - self.ended > 2:
                self.begin(now)
            return
        for m in self.moles:
            x0, y0 = HOLES[m[0]]
            if m[4] is None and x0 <= x <= x0 + 1 and y0 <= y <= y0 + 1:
                if self.phase == 'ready':
                    self.begin(now)
                m[4], self.score = now, self.score + (3 if m[3] else 1)
                return


class Plugin(Moles):
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
