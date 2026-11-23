"""The Launchpad Pro (2015) itself, no MIDI library: the kernel's raw MIDI device on its Standalone port, in the
Programmer layout a 10 by 10 surface, (x, y) from the bottom left, each LED and button numbered 10 * y + x (the 8 by 8
pads 11-88, the round buttons round them; the corners 0, 9, 90 and 99 aren't there). A frame is 100 colours, 0-255
RGB, in that order."""
import fcntl
import os
import struct
import time
from pathlib import Path

HEADER = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x10]  # Novation's SysEx header for the Launchpad Pro
SIDE_OFF = HEADER + [0x0B, 99, 0, 0, 0, 0xF7]  # the side LED (99) dark: the Launchpad lights it in the layout's colour
PREFER_SUBDEVICE = 0x40045542  # SNDRV_CTL_IOCTL_RAWMIDI_PREFER_SUBDEVICE
STANDALONE = 1  # the Launchpad's second MIDI port: the Programmer layout's


def grid(frame):
    """One message that lights the whole surface (RGB grid, 10 by 10; the Launchpad's levels are 0-63)."""
    return HEADER + [0x0F, 0x00] + [min(63, max(0, round(c)) >> 2) for rgb in frame for c in rgb] + [0xF7]


class Reader:
    """The Launchpad's MIDI bytes, as they're read, into events: ('press' | 'release' | 'pressure', x, y, value). Pads
    send notes (velocity), round buttons CCs (127, 0), held pads their pressure; running status allowed, SysEx skipped."""
    def __init__(self):
        self.status, self.data = None, []

    def feed(self, chunk):
        events = []
        for b in chunk:
            if b >= 0xF8:  # (realtime bytes may come anywhere)
                continue
            if b >= 0x80:
                self.status, self.data = b, []  # (F0 starts a SysEx and F7 ends it: their bytes are skipped)
                continue
            if self.status is None or self.status >= 0xF0:
                continue
            self.data.append(b)
            kind = self.status & 0xF0
            if len(self.data) < (1 if kind in (0xC0, 0xD0) else 2):
                continue
            (n, v), self.data = (self.data + [0])[:2], []
            y, x = divmod(n, 10)
            if kind in (0x90, 0xB0):
                events.append(('press' if v else 'release', x, y, v))
            elif kind == 0x80:
                events.append(('release', x, y, 0))
            elif kind == 0xA0:
                events.append(('pressure', x, y, v))
        return events


def launchpad_card():
    """The Launchpad Pro's sound card number, or None while it isn't plugged in."""
    return next((int(line.split()[0]) for line in Path('/proc/asound/cards').read_text().splitlines()
                 if line[:3].strip().isdigit() and 'Launchpad Pro' in line), None)


def open_launchpad():
    """The Launchpad Pro's Standalone port as one raw MIDI file (the kernel's raw MIDI device, its subdevice chosen
    through the sound card's control device, which must stay open), once it's plugged in."""
    if (card := launchpad_card()) is None:
        print('waiting for the Launchpad to be plugged in', flush=True)
        while (card := launchpad_card()) is None:
            time.sleep(2)
    control = os.open(f'/dev/snd/controlC{card}', os.O_RDWR)
    try:
        fcntl.ioctl(control, PREFER_SUBDEVICE, struct.pack('i', STANDALONE))
        return control, os.open(f'/dev/snd/midiC{card}D0', os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        os.close(control)
        raise


def set_up(midi):
    """Every USB connection must be put in Standalone, then Programmer mode, or the pads show the stock layout. The
    USB port can appear before the firmware is ready, hence the pauses."""
    for command, value in ((0x21, 0x01), (0x2C, 0x03)):
        time.sleep(.2)
        message = bytes(HEADER + [command, value, 0xF7])
        if os.write(midi, message) != len(message):
            raise OSError('incomplete Launchpad setup command')
    time.sleep(.2)
