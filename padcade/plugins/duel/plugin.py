import random
import math

from padcade.kit import BLACK, WHITE, mix


class Duel:
    """Reaction duel, for two side by side, half the pads each (left cyan, right magenta): wait, and when a light comes
    on in your half, hit it before the other does; a red one's a trick, hit it and the point's the other's, and so is a
    press before anything's lit. Each one's points climb the outer column; first to five, and the winner's half lights
    up. Any press starts it."""
    colour, SIDES, TO_WIN = (255, 0, 170), ((0, 200, 255), (255, 0, 170)), 5

    def __init__(self):
        self.start(0.)

    def start(self, now):
        self.rng, self.phase, self.score, self.at, self.winner, self.fake = random.Random(), 'ready', [0, 0], now, 0, False

    def round(self, now):
        self.phase, self.at = 'wait', now + self.rng.uniform(1.5, 4.)

    def point(self, side, now):
        self.score[side] += 1
        self.phase, self.winner, self.at = 'point', side, now

    def update(self, now):
        if self.phase == 'wait' and now >= self.at:  # (lights on: the same spot in each half)
            self.phase, self.fake, self.at = 'go', self.rng.random() < .25, now
            self.target = (self.rng.randint(1, 3), self.rng.randrange(8))
        elif self.phase == 'go' and now - self.at > (1.2 if self.fake else 3.):  # (left alone: again)
            self.round(now)
        elif self.phase == 'point' and now - self.at > 1.:
            if max(self.score) >= self.TO_WIN:
                self.phase, self.at = 'won', now
            else:
                self.round(now)

    def touch(self, x, y, now):
        self.update(now)
        side = int(x >= 4)
        if self.phase == 'ready' or self.phase == 'won' and now - self.at > 1.5:
            self.score = [0, 0]
            self.round(now)
        elif self.phase == 'wait' or self.phase == 'go' and self.fake:  # (too soon, or the trick: the other's point)
            self.point(1 - side, now)
        elif self.phase == 'go' and (x if side == 0 else 7 - x, y) == self.target:
            self.point(side, now)

    def frame(self, now):
        self.update(now)
        s, picture = now - self.at, []
        for y in range(8):
            for x in range(8):
                side = int(x >= 4)
                colour, glow = self.SIDES[side], .08
                if self.phase == 'ready':  # (each half breathing in its colour, waiting)
                    glow = .1 + .15 * (.5 + .5 * math.sin(now * 3))
                elif self.phase == 'won':  # (the winner's half lit, a wave running up it; the other's dark)
                    glow = 0 if side != self.winner else .55 + .45 * math.sin(s * 6 - y * .9)
                elif self.phase == 'point' and side == self.winner and int(s / .15) % 2 == 0:
                    glow = .7
                if self.phase in ('wait', 'go', 'point') and x in (0, 7) and y < self.score[side]:
                    glow = .9  # (the score)
                picture.append(mix(BLACK, colour, glow))
        if self.phase == 'go':
            tx, ty = self.target
            picture[8 * ty + tx] = picture[8 * ty + 7 - tx] = (255, 0, 0) if self.fake else WHITE
        return picture


class Plugin(Duel):
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
