from padcade.kit import BLACK, Stepped, mix


class Bouncer(Stepped):
    """A block bouncing round the pads, a new colour the moment it touches a wall; hitting a corner, the pads
    celebrate. A press sets another block off from the pad, in a colour of its own, for eight seconds."""
    COLOURS = ((255, 40, 40), (40, 120, 255), (255, 220, 0), (0, 255, 120), (255, 0, 200), (255, 140, 0), (160, 80, 255))
    LIFE = 8 * 30  # (ticks another block lasts)

    def __init__(self):
        self.done, self.party, self.blocks = 0, 0, [self.block(0., 0., 2.1, 1.6, 0)]

    def block(self, x, y, vx, vy, colour):
        return dict(at=[x, y], velocity=[vx, vy], colour=colour, touching=(True, True), age=0)

    def touch(self, x, y, t):
        if len(self.blocks) < 4:
            colours = {block['colour'] for block in self.blocks}
            self.blocks.append(self.block(min(5., max(0., x - 1.)), min(6., float(y)), 2.1 if x < 4 else -2.1, 1.6 if y < 4 else -1.6,
                                        next(k for k in range(len(self.COLOURS)) if k not in colours)))
        return t

    def tick(self):
        dt = 1 / self.RATE
        self.party = max(0, self.party - 1)
        for block in self.blocks:
            x, y = block['at'][0] + block['velocity'][0] * dt, block['at'][1] + block['velocity'][1] * dt
            if not 0 <= x <= 5:
                block['velocity'][0], x = -block['velocity'][0], min(5., max(0., x))
            if not 0 <= y <= 6:
                block['velocity'][1], y = -block['velocity'][1], min(6., max(0., y))
            touching = (round(x) in (0, 5), round(y) in (0, 6))  # (as drawn: at an edge)
            if any(now and not before for now, before in zip(touching, block['touching'])):
                taken = {other['colour'] for other in self.blocks if other is not block}
                block['colour'] = next(k % len(self.COLOURS) for k in range(block['colour'] + 1, block['colour'] + 8) if k % len(self.COLOURS) not in taken)
                if all(touching):
                    self.party = 45
            block['at'], block['touching'], block['age'] = [x, y], touching, block['age'] + 1
        self.blocks = self.blocks[:1] + [block for block in self.blocks[1:] if block['age'] < self.LIFE]

    def draw(self):
        if self.party and self.party % 8 < 4:
            return [self.COLOURS[(self.party // 8 + k // 9) % len(self.COLOURS)] for k in range(64)]
        picture = [BLACK] * 64
        for k, block in enumerate(self.blocks):
            x0, y0 = round(block['at'][0]), round(block['at'][1])
            fade = 1 if k == 0 else min(1., (self.LIFE - block['age']) / 30)  # (another fading over its last second)
            for dx in range(3):
                for dy in range(2):
                    picture[8 * (y0 + dy) + x0 + dx] = mix(BLACK, self.COLOURS[block['colour']], fade)
        return picture


class Plugin(Bouncer):
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
