"""Pixel Art: the scenes that are installed (plugins of kind "scene") and the ones the owner drew on the management
page, one after another. A press plays with the scene that is showing, and a scene being played with stays."""
import json
from typing import NamedTuple

from padcade.gallery import DrawingConflict
from padcade.kit import BLACK
from padcade.plugins import colour as rgb


class Drawn:
    """A scene the owner drew: its frames in turn."""
    stopped = False

    def __init__(self, drawing):
        self.frames, self.ms, self.started = [[rgb(c, BLACK) for c in frame] for frame in drawing['frames']], drawing['ms'], None

    def frame(self, now):
        if self.started is None:
            self.started = now
        return self.frames[int((now - self.started) * 1000 / self.ms) % len(self.frames)]

    def press(self, x, y, velocity, now):
        pass

    def release(self, x, y, now):
        pass


class Preview(NamedTuple):
    key: str
    name: str
    scene: object
    until: float


class Plugin:
    board_requests = ('preview', 'show', 'picture')
    read_requests = ('drawings',)

    def __init__(self, board):
        self.board, self.known = board, board.gallery.drawings  # (the gallery as it was when last looked at)
        self.key = self.scene = self.since = None  # (what is showing, and since when)
        self.played, self.paused, self.order = -1e9, False, []
        self.preview, self.active = None, False
        self.stills = {}  # (an installed scene's picture for the page, once taken)

    def scenes(self, include_disabled=False):
        """What takes turns: {key: (name, how to start it)}."""
        installed = {scene.id: (scene.name, scene.start) for scene in self.board.plugins('scene', include_disabled=include_disabled)}
        drawn = {f'drawing {d["id"]}': (d['name'], lambda d=d: Drawn(d)) for d in self.board.gallery.drawings if include_disabled or d.get('plays', True)}
        return installed | drawn

    def end_preview(self, now):
        if self.preview:
            scene = self.preview.scene
            if not isinstance(scene, Drawn):
                scene.call('close')
                scene.call('stop')
            self.preview, self.since = None, now
            self.board.changed()

    def still(self, key):
        """What an installed scene looks like, for its card on the page: the fullest picture of its first seconds,
        taken from one started for this and put away again."""
        # ponytail: kept until the board restarts; a scene plugin replaced meanwhile keeps its old picture till then
        if key not in self.stills:
            found = {scene.id: scene for scene in self.board.plugins('scene')}.get(key)  # (one switched off is not run for this)
            if not found:
                return None
            scene, pictures = found.start(), []
            for after in (0, .5, 1, 1.5, 2, 3):
                picture = scene.frame(self.board.now + after)
                if scene.stopped:
                    return None
                pictures.append(picture)
            scene.call('close')
            scene.call('stop')
            fullest = max(pictures, key=lambda picture: sum(max(pad) > 40 for pad in picture))
            self.stills[key] = ['#%02x%02x%02x' % tuple(min(255, max(0, int(part))) for part in pad) for pad in fullest]
        return self.stills[key]

    def close(self):
        self.active = False
        self.end_preview(self.board.now)

    def open(self):
        self.active = True

    def step(self, by, now):
        scenes = self.scenes()
        keys = list(scenes)
        if self.key in keys or self.key not in self.order:
            at = (keys.index(self.key) if self.key in keys else -1 if by > 0 else 0) + by
        else:  # (the one that was showing is gone: the one that came after it)
            following = self.order[self.order.index(self.key) + 1:]
            at = next((keys.index(k) for k in following if k in keys), 0)
        self.key, self.order = keys[at % len(keys)] if keys else None, keys
        self.scene, self.since = scenes[self.key][1]() if keys else None, now  # (started afresh every time it comes up)

    def frame(self, now):
        if self.preview:
            if now < self.preview.until:
                picture = self.preview.scene.frame(now)
                if not self.preview.scene.stopped:
                    return picture
            self.end_preview(now)
        after = self.board.settings['next_after']
        if self.board.gallery.drawings is not self.known:  # (a drawing saved or deleted: what is showing starts afresh)
            self.known = self.board.gallery.drawings
            if self.scene is not None:
                self.step(0, now)
        for _ in range(len(self.scenes()) + 1):  # (a scene that has stopped is passed over)
            if self.scene is None or self.key not in self.scenes():
                self.step(1, now)
            elif after and not self.paused and now - max(self.since, self.played) > after:
                self.step(1, now)  # (one being played with stays)
            if self.scene is None:
                break
            picture = self.scene.frame(now)
            if not self.scene.stopped:
                return picture
            self.scene = None
        return [BLACK] * 64

    def press(self, x, y, velocity, now):
        if self.preview:
            return self.preview.scene.press(x, y, velocity, now)
        self.played = now
        if self.scene:
            self.scene.press(x, y, velocity, now)

    def release(self, x, y, now):
        if self.preview:
            self.preview.scene.release(x, y, now)
        self.played = now
        if self.scene:
            self.scene.release(x, y, now)

    def action(self, name, now):
        self.end_preview(now)
        if name in ('previous', 'next'):
            self.step(1 if name == 'next' else -1, now)
        elif name == 'pause':
            self.paused, self.since = not self.paused, now  # (going on: the scene gets its full time again)

    def lights(self, now):
        return {'previous': (90, 90, 255), 'next': (90, 90, 255), 'pause': (255, 160, 0) if self.paused else (25, 15, 0)}

    def state(self):
        scenes = self.scenes()
        return {'scene': self.preview.name if self.preview else scenes.get(self.key, ('',))[0],
                'paused': self.paused, 'preview': self.preview.key if self.preview else None,
                'key': self.key, 'scenes': [[key, name] for key, (name, _) in scenes.items()]}  # (what takes turns, in order)

    # ---- for the management page: the owner's drawings
    def things(self):
        return self.board.gallery.drawings

    def request(self, name, params, body):
        """drawings: the list; save (name, frames, ms, plays, id and version when editing); delete (id, version);
        show (key): that scene now, in its turn; preview (key): any scene for a moment, also one switched off;
        picture (key): an installed scene's 64 colours, to show what it is."""
        gallery = self.board.gallery
        if name == 'drawings':
            return gallery.drawings
        ask = json.loads(body)
        try:
            if name == 'picture':
                pixels = self.still(ask['key'])
                return {'pixels': pixels} if pixels else {'error': 'That scene has no picture.'}
            if name == 'show':
                scenes = self.scenes()
                if ask['key'] not in scenes:
                    return {'error': 'That scene does not take turns here.'}
                self.end_preview(self.board.now)
                self.key, self.order = ask['key'], list(scenes)
                self.scene, self.since = scenes[self.key][1](), self.board.now
                self.board.changed()
                return {}
            if name == 'preview':
                key = ask['key']
                if key is None:
                    self.end_preview(self.board.now)
                else:
                    if not self.active:
                        return {'error': 'Open Pixel Art on the board to preview a scene.'}
                    found = self.scenes(include_disabled=True).get(key)
                    if not found:
                        return {'error': 'That scene is unavailable.'}
                    self.end_preview(self.board.now)
                    title, start = found
                    scene = start()
                    if scene.stopped:
                        return {'error': 'That scene could not start.'}
                    self.preview = Preview(key, title, scene, self.board.now + 8)
                return {}
            if ask.get('index') is not None:
                raise ValueError('Reload the editor to edit this drawing by its ID.')
            if name == 'save':
                return gallery.save(ask)
            elif name == 'delete':
                gallery.delete(ask)
        except ValueError as error:
            return {'error': str(error), 'conflict': isinstance(error, DrawingConflict)}
        return gallery.drawings
