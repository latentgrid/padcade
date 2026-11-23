import math
from padcade.kit import BLACK, WHITE, mix, noise, going, episodes

LOOP = 12.


WARP = 1.6  # (seconds a press warps the rocket)

ROCKET = '...RR... ..WWWW.. ..WBBW.. ..WWWW.. .RWWWWR. .R.WW.R. ........ ........'

FLAMES = ('...FF... ..f..f..', '...ff... ...FF...')  # (its last two rows, flickering)

def rocket(t, presses=()):
    """A rocket flying up, its flame flickering, stars streaking down past it: mostly either side, now and then one
    past the middle (behind the rocket). A press warps it off to another dimension: the flame white hot, the stars
    speeding up to streaks rushing past, the dark going violet; then back to cruising."""
    boost = going(presses, WARP, t)
    surge = math.sin(math.pi * boost / WARP) ** 2 if boost is not None else 0.  # (up to the warp's height and back)
    picture = [mix(BLACK, (40, 0, 70), surge)] * 64
    flying = t + sum(10 * (u / 2 - WARP / (4 * math.pi) * math.sin(2 * math.pi * u / WARP))  # (the distance warped, so far:
                     for u in (min(t - p, WARP) for p in episodes(presses, WARP) if p <= t))  # up to eleven times as fast)
    for k in range(5):  # the stars: two a side, and one that comes by the middle less often
        period, passes = (13, 7 + int(noise(k, 2) * 5)) if k < 4 else (40, 3)  # (passes a loop)
        travelled = flying * period * passes / LOOP + noise(k, 3) * period
        x = (0, 7)[k % 2] if k < 4 else 1 + int(noise(int(travelled // period) % passes, 9) * 6)  # (a new column each pass)
        head = int(travelled % period)  # (rows from the top; a gap after the screen)
        length = 3 + round(5 * surge)  # (stretched into streaks in the warp)
        for tail in range(length):
            y = 7 - head + tail
            if 0 <= y < 8:
                picture[8 * y + x] = mix(mix((140, 170, 255), (170, 140, 255), surge), BLACK, tail / length * (1 - .6 * surge))
    for x in (1, 6) if surge > .3 else ():  # (in the warp, more streaks, over the fins (behind the rocket))
        head = int(flying * 9 + 5 * x) % 11
        for y in range(7 - head, 12 - head):
            if 0 <= y < 8:
                picture[8 * y + x] = mix((130, 90, 255), BLACK, .4 * (y - 7 + head) / 5)
    rows = ROCKET.split()[:6] + FLAMES[int(t * (24 if boost is not None else 12)) % 2].split()
    palette = {'R': (255, 30, 30), 'W': (230, 230, 240), 'B': (40, 140, 255), 'F': (255, 220, 60), 'f': (255, 90, 0)}
    if boost is not None:
        palette.update(F=WHITE, f=(255, 220, 60))
    for r, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch in palette:
                picture[8 * (7 - r) + x] = palette[ch]
    return picture

class Plugin:
    def __init__(self, board):
        self.started, self.presses, self.held = None, [], {}

    def frame(self, now):
        if self.started is None:
            self.started = now
        t = now - self.started
        return rocket(t, [p for p in self.presses if p[2] <= t])

    def press(self, x, y, velocity, now):
        if self.started is None:
            self.started = now
        t = now - self.started
        self.held[(x, y)] = velocity / 127
        self.presses = [p for p in self.presses if t - p[2] < 60] + [(x, y, t)]

    def release(self, x, y, now):
        self.held.pop((x, y), None)
