"""Games: a menu of the games that are installed (plugins of kind "game"), each on the pad config.toml gives it, and
the one being played. A game may have round buttons of its own while it is on (its actions, see docs/plugins.md)."""
from padcade.kit import BLACK


class Plugin:
    def __init__(self, board):
        self.board, self.game, self.playing = board, None, None  # (the game being played, and its menu entry)

    def menu(self):
        """{(x, y): the game on that pad}, for the games that are switched on."""
        games = {game.id: game for game in self.board.plugins('game')}
        places = ((pad.split(','), games.get(to.get('thing'))) for pad, to in self.board.config.get('pads', {}).items())
        return {(int(x) - 1, int(y) - 1): game for (x, y), game in places if game}

    def start(self, entry):
        self.playing, self.game = entry, entry.start()  # (afresh every time)
        self.board.changed()  # (the management page fetches which game is on the pads)

    def frame(self, now):
        if self.game:
            picture = self.game.frame(now)
            if not self.game.stopped:
                return picture
            self.game = self.playing = None  # (it failed: the menu again)
            self.board.changed()
        picture = [BLACK] * 64
        for (x, y), game in self.menu().items():
            picture[8 * y + x] = game.colour
        return picture

    def press(self, x, y, velocity, now):
        if self.game:
            self.game.press(x, y, velocity, now)
        elif (x, y) in self.menu():
            self.start(self.menu()[x, y])

    def release(self, x, y, now):
        if self.game:
            self.game.release(x, y, now)

    def action(self, name, now):
        if name == 'menu':
            self.game = self.playing = None
            self.board.changed()
        elif name == 'restart':
            if self.playing:
                self.start(self.playing)
        elif self.game:
            self.game.call('action', name, now)

    def released(self, name, now):
        if self.game and name not in ('menu', 'restart'):
            self.game.call('released', name, now)

    def lights(self, now):
        mine = {'menu': (255, 150, 60) if self.game else (60, 35, 15), 'restart': (90, 90, 90) if self.game else (20, 20, 20)}
        theirs = self.game.call('lights', now) if self.game else None
        return (theirs if isinstance(theirs, dict) else {}) | mine

    def close(self):
        if self.game:
            self.game.call('close')  # (the page left, or the pads dark: a game lets go of what was held)

    def hosted(self):
        """The game whose own round buttons are in force."""
        return self.playing.id if self.game else None

    # ---- for the management page
    def things(self):
        return [{'id': game.id, 'name': game.name, 'colour': game.about.get('colour', '#888899')} for game in self.board.plugins('game')]

    def state(self):
        return {'game': self.playing.name if self.game else '', 'hosted': self.hosted()}
