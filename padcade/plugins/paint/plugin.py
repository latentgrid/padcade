"""Paint: drawing on the pads, one frame after another. A press paints its pad, or rubs it out if it is that colour
already. Round buttons, in three groups: the colours down the right in the order of the rainbow (pressed again, each
its other shade); the drawing on the left (gallery, save, clear pressed twice, whether it plays in Pixel Art); the
frames along the bottom (previous, next, copy, remove pressed twice, play, its speed). Drawings go to the board's one gallery, where the management page finds them too; a drawing
opened from the gallery is changed, not added again."""
from padcade.gallery import DrawingConflict
from padcade.kit import BLACK, WHITE, mix
from padcade.plugins import colour as rgb

PALETTE = (((255, 0, 0), (130, 0, 0)), ((255, 110, 0), (140, 70, 10)), ((255, 220, 0), (160, 255, 0)), ((0, 220, 40), (0, 90, 20)),
           ((0, 220, 220), (0, 110, 110)), ((0, 90, 255), (0, 20, 140)), ((170, 0, 255), (255, 60, 170)),
           (WHITE, (110, 110, 110)))  # (round the rainbow, then white; each: its two shades)
SPEEDS = (1000, 500, 200)  # milliseconds a frame, slow to fast
VIOLET, PINK = (170, 70, 255), (255, 95, 192)


class Plugin:
    def __init__(self, board):
        self.gallery = board.gallery
        self.pick, self.shade = len(PALETTE) - 1, 0  # (white)
        self.opened = self.empty = -1e9  # (when the gallery was opened; when it was asked for with nothing in it)
        self.clearing = self.removing = self.saved = -1e9  # (when clear and remove were pressed once; when it was saved)
        self.wiped = None  # (the frame just wiped and when, fading out)
        self.viewing = self.playing = None  # (the saved drawing shown while the gallery is open; playing since)
        self.new()

    def new(self):
        self.frames, self.at, self.ms, self.plays, self.original, self.name = [[BLACK] * 64], 0, 500, False, None, None

    def ink(self):
        return PALETTE[self.pick][self.shade]

    def frame(self, now):
        if self.viewing is not None:  # (the gallery: the drawing as it will play)
            drawing = self.viewing
            tint = max(0., .7 * (1 - (now - self.opened) / .4))  # (it comes up out of violet: the drawing may be the one just saved)
            return [mix(rgb(c, BLACK), VIOLET, tint) for c in drawing['frames'][int(now * 1000 / drawing['ms']) % len(drawing['frames'])]]
        if self.playing is not None:
            return self.frames[int((now - self.playing) * 1000 / self.ms) % len(self.frames)]
        if self.wiped and now - self.wiped[1] < .4:  # (wiped: fading out)
            return [mix(c, BLACK, (now - self.wiped[1]) / .4) for c in self.wiped[0]]
        return [mix(c, WHITE, .6 * (1 - (now - self.saved) / .5)) if now - self.saved < .5 else c for c in self.frames[self.at]]

    def press(self, x, y, velocity, now):
        if self.viewing is not None:  # (a pad in the gallery: that drawing, to work on)
            drawing = self.viewing
            self.frames = [[rgb(c, BLACK) for c in frame] for frame in drawing['frames']]
            self.at, self.ms, self.plays, self.original, self.name = 0, drawing['ms'], drawing.get('plays', True), self.viewing, drawing['name']
            self.viewing = None
        elif self.playing is not None:
            self.playing = None  # (a touch stops it)
        else:
            picture = self.frames[self.at]
            picture[8 * y + x] = BLACK if picture[8 * y + x] == self.ink() else self.ink()

    def release(self, x, y, now):
        pass

    def action(self, name, now):
        drawings = self.gallery.drawings
        if name == 'gallery':
            self.viewing = None if self.viewing is not None or not drawings else drawings[-1]
            self.clearing, self.playing, self.opened = -1e9, None, now
            if not drawings:
                self.empty = now  # (nothing to show yet: the save button says so)
        elif self.viewing is not None:
            index = next((i for i, d in enumerate(drawings) if d['id'] == self.viewing['id']), -1)
            if name in ('previous', 'next'):
                self.viewing = drawings[(index + (1 if name == 'next' else -1)) % len(drawings)] if drawings else None
            elif name == 'clear' and now - self.clearing < 2:
                try:
                    self.gallery.delete(self.viewing)
                except DrawingConflict:
                    pass  # show the current version before accepting another delete
                remaining = self.gallery.drawings
                self.viewing = remaining[max(0, min(index, len(remaining) - 1))] if remaining else None
                self.clearing = -1e9
            elif name == 'clear':
                self.clearing = now
        elif name.startswith('colour'):
            k = int(name[6:]) - 1
            self.pick, self.shade = k, self.shade ^ 1 if k == self.pick else 0
        elif name == 'clear' and now - self.clearing < 2:  # (pressed again: wiped)
            self.wiped, self.frames[self.at], self.clearing = (self.frames[self.at], now), [BLACK] * 64, -1e9
            if not any(map(any, (c for frame in self.frames for c in frame))):
                self.new()  # (nothing left of it: the next thing drawn is a new drawing)
        elif name == 'clear':
            self.clearing = now
        elif name == 'save':
            if any(map(any, (c for frame in self.frames for c in frame))):
                drawing = {'name': self.name or f'Drawing {len(drawings) + 1}', 'ms': self.ms, 'plays': self.plays,
                           'frames': [['#%02x%02x%02x' % tuple(c) for c in frame] for frame in self.frames]}
                try:
                    self.original = self.gallery.save((self.original or {}) | drawing)
                except DrawingConflict:
                    self.original = self.gallery.save(drawing | {'name': drawing['name'][:32] + ' (copy)'})
                self.name, self.saved = self.original['name'], now
        elif name in ('previous', 'next'):
            self.at, self.playing = (self.at + (1 if name == 'next' else -1)) % len(self.frames), None
        elif name == 'copy':
            self.frames.insert(self.at + 1, list(self.frames[self.at]))
            self.at, self.playing = self.at + 1, None
        elif name == 'remove' and now - self.removing < 2:
            if len(self.frames) > 1:
                del self.frames[self.at]
                self.at = min(self.at, len(self.frames) - 1)
            self.removing, self.playing = -1e9, None
        elif name == 'remove':
            self.removing = now
        elif name == 'play':
            self.playing = None if self.playing is not None or len(self.frames) < 2 else now
        elif name == 'speed':
            self.ms = SPEEDS[(SPEEDS.index(self.ms) + 1) % len(SPEEDS)] if self.ms in SPEEDS else 500
        elif name == 'plays':
            self.plays = not self.plays

    def lights(self, now):
        blink = lambda since: (255, 0, 0) if now - since < 2 and int(now * 6) % 2 else (70, 0, 0)
        step = (90, 90, 255)
        if self.viewing is not None:
            return {'gallery': VIOLET, 'previous': step, 'next': step, 'clear': blink(self.clearing)}
        many = len(self.frames) > 1
        lit = {f'colour{k + 1}': shades[self.shade] if k == self.pick else mix(shades[0], BLACK, .6) for k, shades in enumerate(PALETTE)}
        return lit | {
            'clear': blink(self.clearing),
            'save': (0, 255, 80) if now - self.saved < 1 or now - self.empty < 1.5 and int(now * 6) % 2 else (0, 70, 25),
            'gallery': mix(VIOLET, BLACK, .75 if self.gallery.drawings else .95),
            'previous': step if many else mix(step, BLACK, .8), 'next': step if many else mix(step, BLACK, .8),
            'copy': (90, 90, 90), 'remove': blink(self.removing) if many else (25, 0, 0),
            'play': (0, 255, 80) if self.playing is not None else (0, 70, 25) if many else (0, 20, 8),
            'speed': mix(BLACK, (255, 220, 0), (.25, .55, 1)[SPEEDS.index(self.ms)] if self.ms in SPEEDS else .55),
            'plays': PINK if self.plays else mix(PINK, BLACK, .85),
        }
