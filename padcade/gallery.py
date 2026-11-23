"""The owner's drawings: one gallery for the whole board. A drawing is made on the management page or on the pads
(the Paint game), has one or more frames, and plays in Pixel Art if its owner says so."""
import json
import os
import re
import threading
from uuid import NAMESPACE_URL, uuid4, uuid5

WRONG = 'A drawing is one or more frames of 64 colours.'


class DrawingConflict(ValueError):
    pass


class Gallery:
    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        try:
            self.drawings = json.loads(path.read_text())  # (replaced whole on every change, never altered in place)
            if not isinstance(self.drawings, list):
                raise ValueError(WRONG)
            ids = set()
            for index, drawing in enumerate(self.drawings):
                if not isinstance(drawing, dict):
                    raise ValueError(WRONG)
                self.validate_frames(drawing.get('frames'))
                # Assign old drawings stable IDs in memory until the owner's next edit.
                if 'id' not in drawing:
                    identity = json.dumps([index, drawing], sort_keys=True)
                    drawing.update(id=uuid5(NAMESPACE_URL, identity).hex, version=1)
                if (not isinstance(drawing['id'], str) or not drawing['id'] or drawing['id'] in ids
                        or type(drawing.get('version')) is not int or drawing['version'] < 1):
                    raise ValueError('Invalid drawing identity')
                ids.add(drawing['id'])
                drawing.setdefault('name', 'My scene')
                drawing.setdefault('ms', 500)
                if (not isinstance(drawing['name'], str) or type(drawing['ms']) is not int or drawing['ms'] < 1):
                    raise ValueError('Invalid drawing settings')
        except FileNotFoundError:
            self.drawings = []
        except ValueError:
            broken = path.with_suffix('.broken')
            if broken.exists():
                broken = path.with_suffix('.' + uuid4().hex + '.broken')
            path.rename(broken)
            self.drawings = []

    @staticmethod
    def validate_frames(frames):
        if not isinstance(frames, list) or not frames or not all(
                isinstance(frame, list) and len(frame) == 64 and all(isinstance(c, str) and re.fullmatch('#[0-9a-fA-F]{6}', c) for c in frame)
                for frame in frames):
            raise ValueError(WRONG)

    def position(self, drawing):
        for index, stored in enumerate(self.drawings):
            if stored['id'] == drawing.get('id') and stored['version'] == drawing.get('version'):
                return index
        raise DrawingConflict('This drawing was changed or deleted. Reopen it or save your changes as a new drawing.')

    def put(self, drawing, index=None):
        """API 1: save at an optional list index and return that index."""
        with self.lock:
            self.store(drawing, index)
            return len(self.drawings) - 1 if index is None else index

    def save(self, drawing):
        """Save a new drawing or replace its matching id and version."""
        with self.lock:
            return self.store(drawing, self.position(drawing) if drawing.get('id') is not None else None)

    def store(self, drawing, index):
        """Normalize and commit a drawing while the caller holds the gallery lock."""
        frames = drawing.get('frames')
        self.validate_frames(frames)
        try:
            ms = max(50, int(drawing.get('ms') or 500))
        except (TypeError, ValueError):
            ms = 500
        kept = {'name': str(drawing.get('name') or 'My scene')[:40], 'frames': frames, 'ms': ms, 'plays': drawing.get('plays') is not False}
        drawings = list(self.drawings)
        if index is None:
            kept.update(id=uuid4().hex, version=1)
            drawings.append(kept)
        else:
            old = drawings[index]
            kept.update(id=old['id'], version=old['version'] + 1)
            drawings[index] = kept
        self.keep(drawings)
        return kept

    def delete(self, drawing):
        with self.lock:
            drawings = list(self.drawings)
            del drawings[drawing if isinstance(drawing, int) else self.position(drawing)]
            self.keep(drawings)

    def keep(self, drawings):
        """The file, written whole and then put in place: a power cut leaves the gallery that was there."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = self.path.with_suffix('.tmp')
        with new.open('w') as file:
            json.dump(drawings, file)
            file.flush()
            os.fsync(file.fileno())
        new.replace(self.path)
        self.drawings = drawings
