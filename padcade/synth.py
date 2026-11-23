"""Original synthesized instruments and effects, with factory recordings rendered as stereo PCM for the board."""
import math
import random
import wave
from array import array
from functools import lru_cache, partial
from operator import add

RATE = 44100
NOTES = ('C', 'Eb', 'F', 'G', 'Bb')  # Original factory pitches remain available in existing libraries.
CHROMATIC = ('C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B')


def hz(note, octave):
    return 261.63 * 2 ** (CHROMATIC.index(note) / 12 + octave - 4)


def tone(secs, pitch, shape=math.sin, decay=0., vibrato=0., *, attack=200 / RATE, release=.005):
    """A note: `pitch(t)` in Hz, `shape` one cycle over 0..2 pi, fading by `decay` a second."""
    out, phase = [], 0.
    attack_frames, release_rate = attack * RATE, 1 / release
    for i in range(int(secs * RATE)):
        t = i / RATE
        phase += 2 * math.pi * pitch(t) * (1 + vibrato * math.sin(2 * math.pi * 6 * t)) / RATE
        out.append(shape(phase % (2 * math.pi)) * math.exp(-decay * t) * min(1., i / attack_frames, (secs - t) * release_rate))
    return out


def saw(p):
    return p / math.pi - 1


def square(p):
    return 1. if p < math.pi else -1.


def noise(secs, decay, rng, bright=False):
    out, last = [], 0.
    for i in range(int(secs * RATE)):
        sample = rng.uniform(-1, 1)
        out.append((sample - last if bright else sample) * math.exp(-decay * i / RATE))
        last = sample
    return out


def mixed(*parts):
    """Sounds laid over each other, each (starts at seconds, samples, how loud)."""
    out = [0.] * max(int(at * RATE) + len(samples) for at, samples, _ in parts)
    for at, samples, loud in parts:
        start = int(at * RATE)
        for i, sample in enumerate(samples):
            out[start + i] += sample * loud
    return out


def snare():
    return mixed((0, noise(.25, 18, random.Random(7)), .7), (0, tone(.2, lambda t: 180, decay=25), .6))


def original_noise(after):
    # Early kits used one generator for the snare, hat, clap and strings in sequence.
    rng = random.Random(7)
    for _ in range(after):
        rng.random()
    return rng


def clap():
    rng = original_noise(int(.25 * RATE) + int(.08 * RATE))
    return mixed(*[(k * .012, noise(.03, 80, rng), .6) for k in range(3)], (.036, noise(.2, 22, rng), .7))


def roll():
    hit = snare()
    return mixed(*[(n * .061, hit, .22 + .026 * n) for n in range(15)], (.94, hit, 1.))


def original_string(period, damp, noise_offset):
    rng = original_noise(noise_offset)
    string = [rng.uniform(-1, 1) for _ in range(period)]
    out = []
    for i in range(int(1.2 * RATE)):
        position = i % period
        out.append(string[position])
        string[position] = damp * .5 * (string[position] + string[(position + 1) % period])
    return out


def scratch():
    return mixed(*[(at, tone(length, sweep, saw, 6), .8) for at, length, sweep in (
        (0., .18, lambda t: 180 + 1900 * t), (.19, .19, lambda t: 580 * math.exp(-9 * t)),
        (.4, .26, lambda t: 800 * math.exp(-7 * t)))])


def flourish():
    return mixed(*[(at, tone(length, lambda t: freq, saw, 3), .2)
                   for at, length, notes in ((0., .15, (392, 494, 587)), (.18, .9, (523, 659, 784)))
                   for freq in notes])


def shaker():
    grains = noise(.17, 13, random.Random(19), bright=True)
    return [s * min(1., i / (.032 * RATE), (len(grains) - i) / (.04 * RATE)) for i, s in enumerate(grains)]


def metallic_hit():
    return mixed(*[(0, tone(.68, lambda t: freq, decay=decay, release=.04), level)
                   for freq, decay, level in ((446, 6, 1), (1091, 8, .7), (1805, 12, .5), (2941, 22, .28))])


def round_bass(freq):
    return tone(.82, lambda t: freq, lambda p: math.sin(p) + .4 * math.sin(2 * p) + .16 * math.sin(3 * p),
                decay=1.9, attack=.012, release=.11)


def plucked_bass(freq):
    # A clean, short string pluck. The octave speaks at the onset and decays before the fundamental.
    body = tone(.52, lambda t: freq, decay=7., attack=.009, release=.065)
    pick = tone(.19, lambda t: 2 * freq, decay=16., attack=.006, release=.04)
    return mixed((0, body, 1.), (0, pick, .24))


def finger_bass(freq):
    # Upper string harmonics carry the low notes on a small speaker and ring beyond the attack.
    body = tone(.76, lambda t: freq, lambda p: .78 * math.sin(p) + .74 * math.sin(2*p),
                decay=1.95, attack=.012, release=.14)
    string = tone(.76, lambda t: freq,
                  lambda p: -.58 * math.sin(3*p) + .36 * math.sin(4*p) - .17 * math.sin(5*p),
                  decay=3.1, attack=.014, release=.14)
    return mixed((0, body, 1), (0, string, 1))


def studio_bass(mono):
    # Two Butterworth high-pass sections at 180 Hz preserve the harmonics without driving the speakers with sub-bass.
    w = 2 * math.pi * 180 / RATE
    alpha = math.sin(w) / (2 * .707)
    a1, a2 = -2 * math.cos(w) / (1 + alpha), (1 - alpha) / (1 + alpha)
    b = (1 + math.cos(w)) / (2 * (1 + alpha))
    samples = list(mono) + [0.] * (RATE // 50)
    for _ in range(2):
        x1 = x2 = y1 = y2 = 0.
        for i, x in enumerate(samples):
            y = b * (x - 2 * x1 + x2) - a1 * y1 - a2 * y2
            samples[i] = y
            x2, x1, y2, y1 = x1, x, y1, y
    # Keep the auditioned level; on higher notes the filter's transient can exceed full scale.
    gain = min(.945, 32767 / max(1., max(map(abs, samples))))
    return array('h', (round(x * gain) for x in samples))


def bright_pluck(freq):
    body = tone(.54, lambda t: freq, lambda p: math.sin(p) + .27 * math.sin(2 * p), decay=8, release=.05)
    pick = tone(.19, lambda t: freq, lambda p: math.sin(3 * p) + .55 * math.sin(5 * p) + .18 * math.sin(8 * p),
                decay=24, attack=.002)
    return mixed((0, body, 1), (0, pick, .65))


def soft_keys(freq):
    body = tone(1.45, lambda t: freq, lambda p: math.sin(p) + .14 * math.sin(3 * p),
                decay=2.4, attack=.035, release=.16)
    tine = tone(.8, lambda t: freq * 2.004, decay=5.8, attack=.018, release=.07)
    return mixed((0, body, 1), (0, tine, .52))


def tambourine():
    rng = random.Random(31)
    return mixed((0, noise(.17, 26, rng, bright=True), .38), (.031, noise(.18, 28, rng, bright=True), .19),
                 (0, tone(.22, lambda t: 2780, decay=20), .28), (.015, tone(.26, lambda t: 3640, decay=17), .21))


def ride():
    wash = noise(.95, 6.2, random.Random(37), bright=True)
    wash = [s * min(1., i / 130, (len(wash) - 1 - i) / 700) for i, s in enumerate(wash)]
    return mixed((0, wash, .28), (0, tone(.85, lambda t: 893, decay=5.8), .45),
                 (0, tone(1., lambda t: 1651, decay=4.3), .36), (0, tone(.7, lambda t: 2863, decay=6.8), .19))


def resonances(secs, partials):
    """Independently damped modes of a struck shell, block or piece of metal."""
    return mixed(*[(0, tone(secs, lambda t: freq, decay=decay, attack=.0015), level)
                   for freq, decay, level in partials])


def cymbal(secs, pitch, decay, seed):
    wash = noise(secs, decay, random.Random(seed), bright=True)
    for i in range(len(wash)):
        wash[i] *= min(1., i / (.004 * RATE), (len(wash) - 1 - i) / (.025 * RATE))
    ring = resonances(secs, [(pitch * ratio, decay * damping, level)
                             for ratio, damping, level in ((1., 1.1, .18), (1.63, 1.35, .19),
                                                           (2.87, 1.7, .15), (4.31, 2.15, .12))])
    return mixed((0, wash, .65), (0, ring, 1))


def hand_drum(pitch, secs, decay, slap):
    body = tone(secs, lambda t: pitch * (1 + .19 * math.exp(-45*t)), decay=decay, attack=.0015)
    skin = resonances(secs, ((pitch*1.57, decay*1.8, .38), (pitch*2.14, decay*2.8, .27)))
    return mixed((0, body, 1), (0, skin, 1), (0, noise(.038, 90, random.Random(49)), slap))


def electric_kick(pitch=48):
    return tone(.6, lambda t: pitch + 172 * math.exp(-35*t),
                lambda p: math.tanh(2.5 * math.sin(p)), decay=7, attack=.0015, release=.04)


def electric_snare():
    shell = resonances(.28, ((178, 22, .85), (327, 28, .5)))
    wires = noise(.32, 16, random.Random(53), bright=True)
    return mixed((0, shell, 1), (.009, wires, .65))


def electronic_noise(secs, decay, seed):
    burst = noise(secs, decay, random.Random(seed), bright=True)
    for i in range(len(burst)):
        burst[i] *= min(1., i / (.0015 * RATE), (len(burst)-1-i) / (.012 * RATE))
    return burst


def electronic_hat(secs, decay):
    hiss = electronic_noise(secs, decay, 83)
    metal = resonances(secs, ((3719, decay * 1.2, .22), (5531, decay, .18), (7183, decay * 1.5, .14)))
    return mixed((0, hiss, .7), (0, metal, 1))


def punch_kick():
    body = tone(.33, lambda t: 58 + 138 * math.exp(-50*t), decay=12, attack=.002, release=.04)
    knock = tone(.035, lambda t: 620 * math.exp(-28*t), decay=85, attack=.0015)
    return mixed((0, body, 1), (0, knock, .3))


def snap_snare():
    head = tone(.16, lambda t: 202 + 135 * math.exp(-40*t), decay=24, attack=.002)
    return mixed((0, head, .8), (0, electronic_noise(.17, 25, 79), .65))


def electric_clap():
    bursts = [(at, electronic_noise(.018, 70, seed), .7) for at, seed in ((0., 87), (.013, 89), (.028, 91))]
    return mixed(*bursts, (.043, electronic_noise(.23, 19, 93), .6))


def grace_hit(hit, gap):
    # A soft pickup that belongs to the hit itself, so it works at any song tempo.
    return mixed((0, hit, .34), (gap, hit, 1))


def guiro():
    # A faster scrape crosses the ridges closer together near the middle of the stroke.
    rng = random.Random(57)
    ridge = resonances(.029, ((1580, 140, 1), (2680, 180, .48)))
    marks = (0., .035, .063, .086, .105, .123, .14, .159, .18, .205, .235)
    return mixed(*[(at, mixed((0, ridge, .60), (0, noise(.018, 160, rng, bright=True), .55)),
                    .35 + .65 * math.sin(math.pi * i / (len(marks)-1))) for i, at in enumerate(marks)])


def low_impact():
    body = tone(1.12, lambda t: 36 + 84 * math.exp(-7 * t),
                lambda p: math.sin(p) + .38 * math.sin(2 * p), decay=3.8, release=.12)
    return mixed((0, body, 1), (0, noise(.29, 26, random.Random(41)), .23))


def shimmer():
    return mixed(*[(at, tone(1.1, lambda t: freq, decay=2.9, attack=.085, release=.18), level)
                   for at, freq, level in ((0., 1007, 1.), (.042, 1729, .67), (.10, 2539, .44),
                                           (.16, 3251, .31), (.19, 4297, .18))])


def echo_hit():
    hit = tone(.24, lambda t: 520 + 960 * math.exp(-24 * t), decay=14, release=.04)
    return mixed(*[(i * .17, hit, .53 ** i) for i in range(4)])


# Stock chord voicings share oscillator notes. Twelve four-second double arrays take about 16 MiB.
@lru_cache(maxsize=12)
def warm_pad(freq):
    frames = 4 * RATE
    step = 2 * math.pi * freq / RATE
    out = array('d')
    for i in range(frames):
        p = step * i
        body = math.sin(p) + .30 * math.sin(1.003 * p) + .20 * math.sin(2 * p) + .06 * math.sin(3 * p)
        out.append(body * min(1., i / (.10 * RATE), (frames - 1 - i) / (.32 * RATE)))
    return out


def pad_chord(midi_notes):
    voices = (warm_pad(440 * 2 ** ((n - 69) / 12)) for n in midi_notes)
    summed = next(voices)
    for voice in voices:
        summed = map(add, summed, voice)
    return list(summed)


# Recipes are called one clip at a time so the board need not hold the whole library in RAM.
RECIPES = {
    'Kick': lambda: tone(.35, lambda t: 50 + 110 * math.exp(-30 * t), decay=10),
    'Snare': snare,
    'Closed hat': lambda: noise(.08, 60, random.Random(11), bright=True),
    'Open hat': lambda: noise(.53, 12, random.Random(13), bright=True),
    'Clap': clap,
    'Shaker': shaker,
    'Low tom': lambda: tone(.4, lambda t: 110 + 110 * math.exp(-12 * t), decay=8),
    'High tom': lambda: tone(.33, lambda t: 175 + 140 * math.exp(-17 * t), decay=11),
    'Rising sweep': lambda: tone(.62, lambda t: 180 * 2 ** (5 * t), decay=.5, attack=.07, release=.05),
    'Falling sweep': lambda: tone(.57, lambda t: 1450 * 2 ** (-6 * t), decay=2.6, release=.06),
    'Noise burst': lambda: noise(.32, 24, random.Random(23)),
    'Metallic hit': metallic_hit,
    'Laser': lambda: tone(.35, lambda t: 1800 * math.exp(-9 * t) + 150, square, 4),
    'Ta-da': flourish,
    'Record scratch': scratch,
    'Drum roll': roll,
}
# These earlier synthesized effects have separate names from the recordings on the Memes pads.
classic_effects = {
    'Synth airhorn': lambda: mixed(*[(0, tone(.7, lambda t, f=f: f, saw, .5), .3) for f in (466, 470, 587, 592)]),
    'Spring boing': lambda: tone(.6, lambda t: 180 + 140 * math.exp(-5 * t) * math.sin(40 * t), decay=4),
    'Sliding horn': lambda: mixed(*[(i * .38, tone(.36 if i < 3 else .9, lambda t, f=f: f * (1 - .03 * t),
                                                    saw, 1, .02 if i == 3 else 0), .5)
                                    for i, f in enumerate((233, 220, 208, 196))]),
    'Synth buzzer': lambda: tone(.5, lambda t: 110, square),
}
RECIPES.update(classic_effects)
RESTORED_EFFECT_CLIPS = frozenset(classic_effects) | {'Slow cricket chirps', 'Alternating buzzer', 'Short drum fill'}

for voice, base, render in (('Round bass', 2, round_bass), ('Bright pluck', 4, bright_pluck), ('Soft keys', 4, soft_keys)):
    positions = [(note, base) for note in NOTES] + [('C', base + 1)]
    for note, octave in positions:
        RECIPES[f'{voice} {note}{octave}'] = partial(render, hz(note, octave))

originals = {'Tom': RECIPES['Low tom'],
             'Hat': lambda: noise(.08, 60, original_noise(int(.25 * RATE)), bright=True)}
noise_offset = sum(int(seconds * RATE) for seconds in (.25, .08, .03, .03, .03, .2))
for note in NOTES:
    for family, octave, damp in (('Pluck', 4, .996), ('Pluck', 5, .996), ('Bass', 2, .99), ('Bass', 3, .99)):
        period = round(RATE / hz(note, octave))
        originals[f'{family} {note}{octave}'] = partial(original_string, period, damp, noise_offset)
        noise_offset += period
STRING_CLIPS = originals.keys() - {'Tom', 'Hat'}
LEGACY_INSTRUMENT_CLIPS = frozenset(originals)
RECIPES.update(originals)

JAM_FACTORY_CLIPS = frozenset(
    f'Jam {family} {note}{octave}' for family, octaves in (('pluck', (4, 5)), ('bass', (2, 3)))
    for octave in octaves for note in NOTES
) | {'Classic kick', 'Jam snare', 'Jam clap', 'Jam closed hat', 'Jam open hat', 'Jam rimshot', 'Jam tom', 'Jam crash'}

drums = {
    'Pedal hat': lambda: cymbal(.105, 2070, 49, 61),
    'Crash': lambda: cymbal(1.45, 1270, 3.5, 63),
    'Splash': lambda: cymbal(.52, 2190, 9, 65),
    'Ride bell': lambda: resonances(1., ((1110, 5.5, 1), (1781, 6.3, .63), (3203, 9.8, .45), (5277, 21, .2))),
    'Floor tom': lambda: hand_drum(77, .68, 7.7, .075),
    'Mid tom': lambda: hand_drum(140, .44, 10.5, .11),
    'Electric kick': electric_kick,
    'Electric snare': electric_snare,
    'Cowbell': lambda: resonances(.43, ((549, 13, 1), (824, 10, .92), (1627, 27, .46), (2445, 45, .32))),
    'Claves': lambda: resonances(.12, ((2350, 64, 1), (3590, 100, .32))),
    'High block': lambda: resonances(.17, ((920, 36, 1), (2040, 47, .57), (3190, 68, .19))),
    'Low block': lambda: resonances(.22, ((630, 29, 1), (1407, 47, .65), (2260, 63, .18))),
    'Low bongo': lambda: hand_drum(236, .26, 22, .27),
    'High bongo': lambda: hand_drum(341, .18, 32, .33),
    'Low conga': lambda: hand_drum(166, .48, 11, .20),
    'Open conga': lambda: hand_drum(209, .40, 13, .24),
    'Mute conga': lambda: hand_drum(254, .14, 39, .77),
    'Short guiro': guiro,
    'Mute triangle': lambda: resonances(.13, ((2430, 48, 1), (4170, 60, .65), (6720, 77, .38))),
    'Open triangle': lambda: resonances(1.16, ((2430, 4.2, 1), (4170, 5.4, .65), (6720, 7.5, .38))),
}
DRUM_FACTORY_CLIPS = frozenset(drums)
RECIPES.update(drums)

tuned_kicks = {f'Electric kick {note}{octave}': partial(electric_kick, hz(note, octave))
               for octave in (1, 2) for note in ('C', 'G')}
TUNED_KICK_CLIPS = frozenset(tuned_kicks) | {'Classic kick'}
RECIPES.update(tuned_kicks)

electronic_drums = {
    'Punch kick': punch_kick,
    'Snap snare': snap_snare,
    'Electric closed hat': lambda: electronic_hat(.085, 44),
    'Electric open hat': lambda: electronic_hat(.43, 9),
    'Sub kick': lambda: tone(.68, lambda t: 46 + 89 * math.exp(-40*t), decay=5.6, attack=.004, release=.095),
    'Electric clap': electric_clap,
    'Electric rim': lambda: resonances(.11, ((760, 55, 1), (1410, 79, .6), (2520, 100, .25))),
    'Electric shaker': lambda: mixed((0., electronic_noise(.075, 21, 97), .62),
                                     (.037, electronic_noise(.09, 28, 99), 1)),
    'Click kick': lambda: mixed((0, tone(.23, lambda t: 74 + 100 * math.exp(-62*t), decay=15, attack=.002), 1),
                                (0, electronic_noise(.016, 95, 103), .32)),
    'Noise snare': lambda: mixed((0., electronic_noise(.36, 13, 105), 1.),
                                 (0., tone(.18, lambda t: 150, decay=19, attack=.002), .36)),
    'Low zap tom': lambda: tone(.40, lambda t: 88 + 260 * math.exp(-18*t), decay=9, attack=.002),
    'High zap tom': lambda: tone(.29, lambda t: 158 + 460 * math.exp(-26*t), decay=13, attack=.002),
    'Pulse bell': lambda: resonances(.45, ((628, 12, .85), (934, 11, 1.), (1746, 22, .30))),
    'Snare flam': lambda: grace_hit(snap_snare(), .030),
    'Hat double': lambda: grace_hit(electronic_hat(.075, 48), .054),
    'Kick double': lambda: grace_hit(punch_kick(), .064),
}
ELECTRONIC_DRUM_CLIPS = frozenset(electronic_drums)
RECIPES.update(electronic_drums)

bass_notes = [(n, 2) for n in 'CDEFGAB'] + [('C', 3)]
bass = {f'Plucked bass {note}{octave}': partial(plucked_bass, hz(note, octave)) for note, octave in bass_notes}
NEW_BASS_CLIPS = frozenset(bass)
RECIPES.update(bass)
finger = {f'Finger bass {note}{octave}': partial(finger_bass, hz(note, octave)) for note, octave in bass_notes}
FINGER_BASS_CLIPS = frozenset(finger)
RECIPES.update(finger)
upper_bass = [(n, 3) for n in 'DEFGAB'] + [('C', 4), ('D', 4)]
black_keys = [(note, octave) for octave in (2, 3, 4) for note in CHROMATIC
              if len(note) == 2 and (octave < 4 or note == 'Db')]
studio = {f'Studio bass {note}{octave}': partial(finger_bass, hz(note, octave))
          for note, octave in bass_notes + upper_bass + black_keys}
STUDIO_BASS_CLIPS = frozenset(studio)
RECIPES.update(studio)

CHORD_NOTES = {'Chord Cm': (60, 63, 67), 'Chord Eb': (63, 67, 70),
               'Chord Ab': (56, 60, 63), 'Chord Bb': (58, 62, 65)}
expanded = {
    'Deep kick': lambda: tone(.62, lambda t: 42 + 108 * math.exp(-34 * t),
                              lambda p: math.sin(p) + .25 * math.sin(2 * p), decay=8.4),
    'Rim click': lambda: mixed((0, tone(.065, lambda t: 710, decay=83, attack=.001), 1),
                               (.002, tone(.045, lambda t: 1490, decay=100, attack=.001), .46)),
    'Tambourine': tambourine,
    'Ride': ride,
    'Reverse cymbal': lambda: ride()[::-1],
    'Low impact': low_impact,
    'Metallic shimmer': shimmer,
    'Echo hit': echo_hit,
    **{name: partial(pad_chord, notes) for name, notes in CHORD_NOTES.items()},
}
for note, octave in [(note, 4) for note in NOTES] + [('C', 5)]:
    expanded[f'Warm pad {note}{octave}'] = partial(warm_pad, hz(note, octave))
EXPANDED_FACTORY_CLIPS = frozenset(expanded)
RECIPES.update(expanded)
diatonic = {f'{voice} {note}{octave}': partial(render, hz(note, octave))
            for voice, octave, render in (('Round bass', 2, round_bass), ('Bright pluck', 4, bright_pluck),
                                         ('Soft keys', 4, soft_keys), ('Warm pad', 4, warm_pad))
            for note in ('D', 'E', 'A', 'B')}
DIATONIC_FACTORY_CLIPS = frozenset(diatonic)
RECIPES.update(diatonic)
# The earlier four-octave pluck remains available in the clip library.
legacy_plucks = {f'Bright pluck {note}{octave}': partial(bright_pluck, hz(note, octave))
                 for octave in range(2, 6) for note in CHROMATIC}
LEGACY_PLUCK_CLIPS = frozenset(legacy_plucks.keys() - RECIPES.keys())
RECIPES.update(legacy_plucks)

PIANO_PITCHES = tuple(f'{note}{octave}' for octave in range(2, 6) for note in CHROMATIC)
PIANO_FACTORY_CLIPS = frozenset('Piano ' + pitch for pitch in PIANO_PITCHES)
more_chords = {'Chord C': (60, 64, 67), 'Chord G': (55, 59, 62),
               'Chord Am': (57, 60, 64), 'Chord F': (53, 57, 60)}
CHORD_FACTORY_CLIPS = frozenset(more_chords)
RECIPES.update({name: partial(pad_chord, notes) for name, notes in more_chords.items()})
scale_chords = {'Chord C3': (48, 52, 55), 'Chord Dm': (50, 53, 57),
                'Chord Em': (52, 55, 59), 'Chord Bdim': (59, 62, 65)}
SCALE_CHORD_CLIPS = frozenset(scale_chords)
RECIPES.update({name: partial(pad_chord, notes) for name, notes in scale_chords.items()})
seventh_chords = {'Chord Cmaj7': (60, 64, 67, 71), 'Chord Dm7': (62, 65, 69, 72),
                 'Chord G7': (55, 59, 62, 65), 'Chord Am7': (57, 60, 64, 67)}
RECIPES.update({name: partial(pad_chord, notes) for name, notes in seventh_chords.items()})
SYNTH_GRID_CLIPS = (frozenset(seventh_chords)
                    | {f'Studio bass {note}{octave}' for note, octave in upper_bass}
                    | {f'Jam {voice} {note}{octave + shift}' for voice, shift in (('pluck', 2), ('bass', 0))
                       for note, octave in bass_notes + upper_bass}) - JAM_FACTORY_CLIPS
CHORD_NOTES.update(more_chords | scale_chords | seventh_chords)
_INTERVALS = {'major': (0, 4, 7), 'minor': (0, 3, 7), 'dominant seventh': (0, 4, 7, 10),
              'minor seventh': (0, 3, 7, 10), 'major seventh': (0, 4, 7, 11),
              'diminished': (0, 3, 6), 'half diminished': (0, 3, 6, 10)}
_original_chords = {notes: name for name, notes in CHORD_NOTES.items()}


def chord_clip(root, quality):
    notes = tuple(root + step for step in _INTERVALS[quality])
    return _original_chords.get(notes, f'Chord {CHROMATIC[root % 12]}{root // 12 - 1} {quality}')


chromatic_chords = {}
for root in range(48, 73):  # Major and minor triads from C3 through C5.
    qualities = ['major', 'minor']
    if root <= 60:
        qualities.extend(('dominant seventh', 'minor seventh'))
    if root in (48, 53, 60):
        qualities.append('major seventh')
    if root == 59:
        qualities.extend(('diminished', 'half diminished'))
    for quality in qualities:
        name = chord_clip(root, quality)
        if name not in CHORD_NOTES:
            chromatic_chords[name] = tuple(root + step for step in _INTERVALS[quality])
CHORD_NOTES.update(chromatic_chords)
RECIPES.update({name: partial(pad_chord, notes) for name, notes in chromatic_chords.items()})
HIGH_CHORD_CLIPS = frozenset(name for name, notes in chromatic_chords.items() if notes[0] > 60)
CHROMATIC_FACTORY_CLIPS = (frozenset(chromatic_chords) - HIGH_CHORD_CLIPS
                          | {f'Studio bass {note}{octave}' for note, octave in black_keys}
                          | {f'Jam {voice} {note}{octave + shift}' for voice, shift in (('pluck', 2), ('bass', 0))
                             for note, octave in black_keys}) - JAM_FACTORY_CLIPS
PEDAL_CLIPS = PIANO_FACTORY_CLIPS | {name for name in RECIPES if name.startswith(('Soft keys ', 'Warm pad ', 'Chord '))}
NEW_FACTORY_CLIPS = (frozenset(RECIPES) - LEGACY_PLUCK_CLIPS | PIANO_FACTORY_CLIPS | TUNED_KICK_CLIPS).difference(
    {'Kick', 'Snare', 'Clap', 'Laser', 'Ta-da', 'Record scratch', 'Drum roll'})


def piano_note(folder, key):
    # One real recording per minor third; play the nearest one at the requested pitch.
    root = min((key + 1) // 3 * 3, len(PIANO_PITCHES) - 3)
    with wave.open(str(folder / (PIANO_PITCHES[root] + '.wav')), 'rb') as wav:
        step = wav.getframerate() / RATE * 2 ** ((key - root) / 12)
        source = array('h', wav.readframes(wav.getnframes()))
    mono = array('h')
    for frame in range(int((len(source) - 1) / step)):
        pos = frame * step
        index = int(pos)
        mono.append(round(source[index] + (pos - index) * (source[index + 1] - source[index])))
    return stereo(mono)


def factory_names(recordings):
    names = list(RECIPES)
    if (recordings / 'piano').is_dir():
        names.extend('Piano ' + pitch for pitch in PIANO_PITCHES)
    return names + [clip.stem for clip in sorted(recordings.glob('*.wav'))]


def stereo(mono):
    channels = array('h', [0]) * (len(mono) * 2)
    channels[::2] = channels[1::2] = mono
    return channels.tobytes()


def normalized_note(name):
    samples = RECIPES[name]()
    peak = max(1e-9, max(map(abs, samples)))
    return array('h', (int(s / peak * 26000) for s in samples))


def fade_string_edges(mono):
    # The old string model begins with noise and ends mid-cycle. Keep its body and gain intact.
    frames = round(RATE * .004)
    for i in range(frames):
        gain = .5 - .5 * math.cos(math.pi * i / frames)
        mono[i] = round(mono[i] * gain)
        mono[-1-i] = round(mono[-1-i] * gain)


def factory_clips(recordings, names=None):
    """Yield stereo clips in request order, one at a time so a bank switch can change the next request."""
    piano = recordings / 'piano'
    pitches = {'Piano ' + pitch: key for key, pitch in enumerate(PIANO_PITCHES)} if piano.is_dir() else {}
    recorded = {clip.stem: clip for clip in recordings.glob('*.wav')}
    for name in factory_names(recordings) if names is None else names:
        if name in RECIPES:
            mono = normalized_note(name)
            if name in STRING_CLIPS:
                fade_string_edges(mono)
            yield name, RATE, stereo(studio_bass(mono) if name in STUDIO_BASS_CLIPS else mono)
        elif name in pitches:
            yield name, RATE, piano_note(piano, pitches[name])
        elif name in recorded:
            with wave.open(str(recorded[name]), 'rb') as wav:
                yield name, wav.getframerate(), stereo(array('h', wav.readframes(wav.getnframes())))


def _render_one(recordings, name):
    return next(factory_clips(recordings, (name,)), None)


def background_factory_clips(recordings, names):
    """Keep factory synthesis off the board's audio thread and its interpreter lock."""
    import multiprocessing
    import os
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context('spawn'),
                             initializer=getattr(os, 'nice', None), initargs=(10,)) as renderer:
        for name in names:
            clip = renderer.submit(_render_one, recordings, name).result()
            if clip is not None:
                yield clip
