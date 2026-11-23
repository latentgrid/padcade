from padcade.kit import pictures


HEART = ('.RR..RR. RwwRRRRR RwRRRRRR RRRRRRRR RRRRRRRd .RRRRRd. ..RRRd.. ...Rd...',
         '........ .RR..RR. .RwRRRR. .RRRRRd. ..RRRd.. ...Rd... ........ ........')

BEATS = pictures(HEART[:1] + HEART[1:] + HEART[:1] + HEART[1:] + HEART[:1] * 4, {'R': (255, 0, 0), 'd': (60, 0, 0), 'w': (255, 200, 200)})

def heart(t, presses=()):
    """A heart beating, lub-dub, and resting. Tapped, it beats faster, the more taps the faster, calming after four
    seconds."""
    beats = t + .6 * sum(min(max(0., t - p), 4.) for _, _, p in presses)  # (its own clock, running fast after each tap)
    return BEATS[int(beats * 6) % len(BEATS)]

class Plugin:
    def __init__(self, board):
        self.started, self.presses, self.held = None, [], {}

    def frame(self, now):
        if self.started is None:
            self.started = now
        t = now - self.started
        return heart(t, [p for p in self.presses if p[2] <= t])

    def press(self, x, y, velocity, now):
        if self.started is None:
            self.started = now
        t = now - self.started
        self.held[(x, y)] = velocity / 127
        self.presses = [p for p in self.presses if t - p[2] < 60] + [(x, y, t)]

    def release(self, x, y, now):
        self.held.pop((x, y), None)
