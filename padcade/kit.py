"""Helpers for plugins that draw: a picture is 64 colours, (red, green, blue) 0-255 each, the bottom row first
(index 8 * y + x)."""
import math

BLACK, WHITE = (0, 0, 0), (255, 255, 255)


def mix(a, b, t):
    t = min(1., max(0., t))
    return tuple(p + (q - p) * t for p, q in zip(a, b))


def noise(x, y):
    """A repeatable 0-1 value for a lattice point."""
    return (math.sin(x * 12.9898 + y * 78.233) * 43758.5453) % 1


def smooth(x, y):
    """Value noise between the lattice points."""
    x0, y0 = math.floor(x), math.floor(y)
    fx, fy = x - x0, y - y0
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    top = noise(x0, y0) + (noise(x0 + 1, y0) - noise(x0, y0)) * fx
    bottom = noise(x0, y0 + 1) + (noise(x0 + 1, y0 + 1) - noise(x0, y0 + 1)) * fx
    return top + (bottom - top) * fy


def pictures(frames, palette):
    """Frames drawn as text, eight rows top to bottom separated by spaces ('.' is off), as pictures."""
    return [[palette.get(ch, BLACK) for row in reversed(frame.split()) for ch in row] for frame in frames]


def sprite(frames, palette, fps):
    """The frames, `fps` a second: a picture for any time."""
    drawn = pictures(frames, palette)
    return lambda t: drawn[int(t * fps) % len(drawn)]


def shifted(picture, up):
    """The picture moved `up` rows (down if it's negative), what goes off the pads gone."""
    out = [BLACK] * 64
    for i, c in enumerate(picture):
        if 0 <= i // 8 + up < 8:
            out[i + 8 * up] = c
    return out


def episodes(presses, lasts):
    """The presses that set off something lasting `lasts` seconds (one while it's going does nothing): their times."""
    starts = []
    for _, _, t in presses:
        if not starts or t >= starts[-1] + lasts:
            starts.append(t)
    return starts


def going(presses, lasts, t):
    """How long ago the thing a press set off started, if it's still going at t."""
    return next((t - start for start in reversed(episodes(presses, lasts)) if t - start < lasts), None)


class Stepped:
    """A picture made by stepping a little simulation RATE times a second, caught up to the time asked for. Shown again
    from the start, or left unwatched more than a second (something else was on), it carries on from where it was
    rather than playing the missed time through."""
    RATE = 30

    def __call__(self, t):
        target = int(t * self.RATE)
        if not 0 <= target - self.done <= self.RATE:
            self.done = target - 1
        for _ in range(target - self.done):
            self.done += 1
            self.tick()
        return self.draw()


def paint(picture, drawing, x, top, palette):
    """A text drawing onto a picture with its left column at x and its top row `top` rows from the top, cut off at the
    edges: each letter in its palette's colour, or all in one colour."""
    for r, row in enumerate(drawing.split()):
        for c, ch in enumerate(row):
            if ch != '.' and 0 <= x + c < 8 and 0 <= top + r < 8:
                picture[8 * (7 - top - r) + x + c] = palette[ch] if isinstance(palette, dict) else palette


def put(picture, x, top, colour):
    """One pad, `top` rows from the top, if it's on the pads."""
    if 0 <= x < 8 and 0 <= top < 8:
        picture[8 * (7 - top) + x] = colour


def pad(picture, x, y, colour):
    """One pad, (x, y) from the bottom left, if it's on the pads."""
    if 0 <= x < 8 and 0 <= y < 8:
        picture[8 * y + x] = colour


def counted(picture, n, colour):
    """A score: n pads lit, filling from the top left, a row at a time."""
    for k in range(min(64, n)):
        picture[8 * (7 - k // 8) + k % 8] = colour
    return picture
