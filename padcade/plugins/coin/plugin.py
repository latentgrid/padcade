from padcade.kit import BLACK, WHITE, pictures, shifted, going


COIN = ('..YYYY.. .YYYYYY. YYwYYYYo YYwYYYYo YYYYYYYo YYYYYYYo .YYYYYo. ..YYYY..',
        '...YY... ..YYYo.. ..YwYo.. ..YwYo.. ..YYYo.. ..YYYo.. ..YYYo.. ...YY...',
        '...oo... ...oo... ...oo... ...oo... ...oo... ...oo... ...oo... ...oo...')

SPINNING = pictures(COIN[:1] * 2 + COIN[1:] + COIN[1:2], {'Y': (255, 210, 0), 'o': (220, 75, 0), 'w': (255, 255, 230)})

def coin(t, presses=()):
    """A gold coin spinning, glinting. A press collects it: spinning fast it hops up, half off the top, drops back a
    little and vanishes in a sparkle; a moment later a new one drops in."""
    s = going(presses, 2.4, t)
    if s is not None and s < .7:
        return shifted(SPINNING[int(s * 30) % len(SPINNING)], round(16 * s - 16 * s * s))
    if s is not None and s < 1.85:  # (a sparkle, then nothing a while)
        return sparkle(3.5, 1, s - .7) if s < .95 else [BLACK] * 64
    picture = SPINNING[int(t * 8) % len(SPINNING)]
    return picture if s is None else shifted(picture, round(8 * (1 - (s - 1.85) / .55) ** 2))

def sparkle(x, top, s):
    """A sparkle twinkling out round (x, top) over a quarter of a second: a cross, then its tips."""
    picture = [BLACK] * 64
    for dx, dy in ((-.5, 0), (.5, 0), (-.5, -1), (-.5, 1), (.5, -1), (.5, 1)) if s < .12 else ((-2, -1), (2, -1), (-2, 1), (2, 1)):
        put(picture, round(x + dx), top + dy, WHITE if s < .12 else (255, 230, 120))
    return picture

def put(picture, x, top, colour):
    """One pad, `top` rows from the top, if it's on the pads."""
    if 0 <= x < 8 and 0 <= top < 8:
        picture[8 * (7 - top) + x] = colour

class Plugin:
    def __init__(self, board):
        self.started, self.presses, self.held = None, [], {}

    def frame(self, now):
        if self.started is None:
            self.started = now
        t = now - self.started
        return coin(t, [p for p in self.presses if p[2] <= t])

    def press(self, x, y, velocity, now):
        if self.started is None:
            self.started = now
        t = now - self.started
        self.held[(x, y)] = velocity / 127
        self.presses = [p for p in self.presses if t - p[2] < 60] + [(x, y, t)]

    def release(self, x, y, now):
        self.held.pop((x, y), None)
