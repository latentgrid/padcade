"""Private WAV snapshots for sounds contributed by other pages."""
import hashlib
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from fcntl import ioctl
except ImportError:
    ioctl = None

SAMPLE_WRITER = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sample-writer')
PENDING_BYTES = 16 * 1024 * 1024


def file_version(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def write_sample(target, contents):
    """Publish a complete, durable WAV. Large sources are cloned or streamed without loading them into RAM."""
    descriptor, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            if isinstance(contents, bytes):
                output.write(contents)
            else:
                path, stamp = contents
                with path.open('rb') as source:
                    try:
                        if ioctl is None:
                            raise OSError('No file cloning')
                        ioctl(output, 0x40049409, source.fileno())  # Linux FICLONE
                    except OSError:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                    if file_version(os.fstat(source.fileno())) != stamp:
                        raise OSError('The source changed while saving its recording.')
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            pass
    finally:
        Path(temporary).unlink(missing_ok=True)


class SampleStore:
    def __init__(self, clips):
        self.clips = clips
        self.pending = {}
        self.lock = threading.Lock()

    def keep(self, path):
        stamp = file_version(path.stat())
        name = 'Song sample ' + hashlib.sha256(repr(stamp).encode()).hexdigest()
        target = self.clips / (name + '.wav')
        if target.exists():
            return name
        with self.lock:
            for old, (_, future) in list(self.pending.items()):
                if future.done() and future.exception() is None:
                    del self.pending[old]
            if name in self.pending:
                return name
            queued = sum(len(raw) for raw, _ in self.pending.values())
            fits = len(self.pending) < 64 and stamp[2] <= PENDING_BYTES - queued
        if fits:
            # Capture the file before the caller can edit it. The Pi has just read it for
            # playback, so these bytes normally come straight from the kernel's page cache.
            with path.open('rb') as source:
                raw = source.read(stamp[2] + 1)
                if len(raw) != stamp[2] or file_version(os.fstat(source.fileno())) != stamp:
                    raise OSError('The source changed while saving its recording.')
            with self.lock:
                self.pending[name] = raw, SAMPLE_WRITER.submit(write_sample, target, raw)
        else:
            write_sample(target, (path, stamp))
        return name

    def flush(self, name=None):
        """An index write or immediate playback must wait for its WAVs to be published."""
        with self.lock:
            entries = list(self.pending.items()) if name is None else [(name, self.pending.get(name))]
        for key, entry in entries:
            if entry is None:
                continue
            raw, future = entry
            try:
                future.result()
            except OSError:
                # Keep the exact bytes, including after an in-place edit of the source.
                # Studio retries failed storage when free space becomes available.
                write_sample(self.clips / (key + '.wav'), raw)
            with self.lock:
                if self.pending.get(key) is entry:
                    del self.pending[key]
