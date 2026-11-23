"""Home Assistant on the pads, on as many banks as the owner likes. Every pad has two slots. A tap does one thing with
an entity: switches a light, starts a scene, opens a blind, picks an option. A hold does a second thing, with that
entity or any other, or shows more while the pad is held: the controls of an entity (a light's brightness and colours,
a level's steps, a select's options) or the pad's own subpage of further pads. It talks to Home Assistant's REST API with a long-lived access token, in a thread of its
own, so a slow answer never holds the pads up."""
import http.client
import json
import math
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from padcade.kit import BLACK, WHITE, mix
from padcade.plugins import colour as rgb

ON_OFF = {'toggle': ('Toggle', 'toggle', 'flip'), 'on': ('Turn on', 'turn_on', 'on'), 'off': ('Turn off', 'turn_off', 'off')}
START, PRESS, NEXT = {'start': ('Start', 'turn_on', None)}, {'press': ('Press', 'press', None)}, {'next': ('Next option', 'select_next', None)}
# what can sit on a pad: its heading on the Edit screen, its colour until the owner picks one, and what a tap can do:
# {tap: (words, service, the state that leaves it in: a state, 'flip' or None when there is none to show)}
DOMAINS = {
    'light': ('Lights', '#ffd23f', ON_OFF),
    'switch': ('Switches', '#f1f0f6', ON_OFF), 'input_boolean': ('Switches', '#f1f0f6', ON_OFF), 'siren': ('Switches', '#ff5a5a', ON_OFF),
    'fan': ('Fans and climate', '#3fd0ff', ON_OFF), 'climate': ('Fans and climate', '#ff9838', ON_OFF),
    'humidifier': ('Fans and climate', '#7fd6ff', ON_OFF),
    'cover': ('Covers and valves', '#c9a26b', {'toggle': ('Open or close', 'toggle', 'flip'), 'open': ('Open', 'open_cover', 'open'),
                                               'close': ('Close', 'close_cover', 'closed'), 'stop': ('Stop', 'stop_cover', None)}),
    'valve': ('Covers and valves', '#6bb7c9', {'toggle': ('Open or close', 'toggle', 'flip'), 'open': ('Open', 'open_valve', 'open'),
                                               'close': ('Close', 'close_valve', 'closed')}),
    'media_player': ('Players', '#5fe07a', ON_OFF | {'play': ('Play or pause', 'media_play_pause', None), 'next': ('Next track', 'media_next_track', None),
                                                     'previous': ('Previous track', 'media_previous_track', None),
                                                     'mute': ('Mute or unmute', 'volume_mute', None)}),
    'scene': ('Scenes and scripts', '#a070ff', START), 'script': ('Scenes and scripts', '#ff9838', START),
    'button': ('Scenes and scripts', '#ff5fc0', PRESS), 'input_button': ('Scenes and scripts', '#ff5fc0', PRESS),
    'automation': ('Automations', '#d0a0ff', {'run': ('Run', 'trigger', None), 'toggle': ('Enable or disable', 'toggle', 'flip'),
                                              'on': ('Enable', 'turn_on', 'on'), 'off': ('Disable', 'turn_off', 'off')}),
    'lock': ('Locks and vacuums', '#ff5a5a', {'lock': ('Lock', 'lock', 'locked'), 'unlock': ('Unlock', 'unlock', 'unlocked')}),
    'vacuum': ('Locks and vacuums', '#9be37a', {'start': ('Start', 'start', 'cleaning'), 'pause': ('Pause', 'pause', 'paused'),
                                                'dock': ('Return to its dock', 'return_to_base', 'returning')}),
    'select': ('Selects', '#3fd0ff', NEXT), 'input_select': ('Selects', '#3fd0ff', NEXT),
    'number': ('Levels', '#5b7bff', {}), 'input_number': ('Levels', '#5b7bff', {}),
}
SELECTS, NUMBERS, STATELESS = ('select', 'input_select'), ('number', 'input_number'), ('scene', 'script', 'button', 'input_button')
CHOSEN, LEVELLED = SELECTS + ('climate', 'media_player'), NUMBERS + ('climate', 'media_player', 'fan', 'cover', 'valve', 'humidifier')
DIM = {'off', 'idle', 'standby', 'paused', 'unknown', 'closed', 'locked', 'docked'}  # states a pad shows dimmed
FLIP = {'on': 'off', 'off': 'on', 'open': 'closed', 'closed': 'open'}
COLOURED = {'hs', 'xy', 'rgb', 'rgbw', 'rgbww'}  # the colour modes of a light that takes any colour
SWATCHES = ((255, 255, 255), (255, 0, 0), (255, 120, 0), (255, 220, 0), (0, 255, 0), (0, 220, 255), (0, 0, 255), (170, 0, 255))
DIMMABLE = COLOURED | {'brightness', 'color_temp', 'white'}
HOUSE = ('...##...', '..####..', '.######.', '########', '.#....#.', '.#.##.#.', '.#.##.#.', '.######.')
HOLD = .35  # seconds a pad or a bank's button is held before that counts as holding it
# asked of Home Assistant once: its areas and what is in each, as [[name, [entity, ...]], ...]
ROOMS = ('{% set ns = namespace(rows=[]) %}{% for a in areas() %}{% set ns.rows = ns.rows + [[area_name(a), area_entities(a)]] %}'
         '{% endfor %}{{ ns.rows | tojson }}')


def number(text, default):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def level(domain, about):
    """How a thing's level is set, or None: (service, what it sets, lowest, highest, finest step, words for it)."""
    if domain in ('cover', 'valve') and not (about.get('supported_features', 0) & 4):  # SET_POSITION
        return None
    if domain in NUMBERS:  # (Home Assistant has to say what its range is: nothing is set blindly)
        return ('set_value', 'value', number(about.get('min'), 0.), number(about.get('max'), 100.), number(about.get('step'), 0.),
                'its level') if {'min', 'max'} <= set(about) else None
    if domain == 'climate':
        return ('set_temperature', 'temperature', number(about.get('min_temp'), 0.), number(about.get('max_temp'), 0.),
                number(about.get('target_temp_step'), .5), 'temperature') if {'min_temp', 'max_temp'} <= set(about) else None
    return {'media_player': ('volume_set', 'volume_level', 0., 1., .01, 'volume'), 'fan': ('set_percentage', 'percentage', 0., 100., 1., 'speed'),
            'cover': ('set_cover_position', 'position', 0., 100., 1., 'position'), 'valve': ('set_valve_position', 'position', 0., 100., 1., 'position'),
            'humidifier': ('set_humidity', 'humidity', number(about.get('min_humidity'), 0.), number(about.get('max_humidity'), 100.), 1., 'humidity'),
            }.get(domain)


def choices(domain, state, about):
    """What a thing is picked from, or None: (service, what it sets, the choices, the one chosen, words for them)."""
    if domain in SELECTS:
        return ('select_option', 'option', about.get('options') or [], state, 'its options')
    if domain == 'climate':
        return ('set_hvac_mode', 'hvac_mode', about.get('hvac_modes') or [], state, 'its modes')
    if domain == 'media_player' and about.get('source_list'):
        return ('select_source', 'source', about['source_list'], about.get('source'), 'its sources')


def unit(domain):
    return '°' if domain == 'climate' else '' if domain in NUMBERS else ' %'


def words(data):
    """What one pad of a held thing's controls sets, in a word or two (a colour shows itself)."""
    key, value = next(iter(data.items()))
    if key == 'rgb_color':
        return ''
    if key == 'volume_level':
        return f'{round(value * 100)} %'
    return str(value) if isinstance(value, str) else f'{value:g}°' if key == 'temperature' else f'{value:g}' if key == 'value' else f'{value:g} %'


class SameServer(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        def origin(url):
            parts = urllib.parse.urlsplit(url)
            return parts.scheme, parts.hostname, parts.port or {'http': 80, 'https': 443}.get(parts.scheme)
        if origin(request.full_url) != origin(newurl):
            raise urllib.error.HTTPError(request.full_url, code, message, headers, response)
        return super().redirect_request(request, response, code, message, headers, newurl)


class Plugin:
    def __init__(self, board):
        self.board, self.states, self.found, self.rooms = board, {}, [], {}  # (entity: what Home Assistant says of it; all it has; entity: its room)
        self.result, self.calls, self.going, self.look = (None, None), queue.Queue(), True, True
        self.on_pads = False
        self.bank = self.visible = 0  # (the bank it was sent to, where it stays; the one on the pads just now)
        self.labels = {}  # (what each pad is just now, for the management page: {'column,row': words})
        self.back = None  # (a bank's round button, held: its name, the bank before, since when)
        self.hold = None  # (a pad held for what its hold does or shows: [where, the pad, since, whether that was used, what it is])
        self.inner = None  # (a pad of a subpage held for its controls, the same way)
        self.stay = None  # (a pad whose subpage a tap opened, the same way: it stays until the pad is pressed again)
        self.flash = None  # (the thing just started and by which tap, and until when its pad is lit for it)
        self.http = urllib.request.build_opener(SameServer)
        threading.Thread(target=self.talk, name='home-assistant', daemon=True).start()

    # ---- talking to Home Assistant, in its own thread
    def access(self):
        settings = self.board.settings
        return settings['address'], settings['token']

    @property
    def status(self):
        access = self.access()
        if not all(access):
            return 'Enter the Home Assistant address and an access token.'
        checked, message = self.result
        return message if checked == access else 'Checking connection…'

    def ask(self, access, path, data=None):
        address, token = access
        request = urllib.request.Request(address.rstrip('/') + path, data=None if data is None else json.dumps(data).encode(),
                                         headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        with self.http.open(request, timeout=4) as answer:
            return json.loads(answer.read() or b'null')

    def talk(self):
        known = checked = None
        check_again = 0.
        while self.going:
            settings = self.board.settings
            waiting = []
            try:
                waiting.append(self.calls.get(timeout=max(.05, float(settings['every'] or 2))))
                while True:
                    waiting.append(self.calls.get_nowait())
            except (queue.Empty, ValueError):
                pass
            if not self.going:
                return
            access = self.access()
            if not all(access):
                self.result, known, checked = (None, None), None, None
                continue
            if not self.on_pads and access == checked and not waiting and time.monotonic() < check_again:
                continue
            checked, check_again = access, time.monotonic() + 60
            try:
                watched = self.watched() if self.on_pads else ()
                if access != known or not watched:
                    if not access[0].lower().startswith(('http://', 'https://')):
                        self.say(access, 'Enter the Home Assistant address starting with http:// or https://.')
                        continue
                    reply = self.ask(access, '/api/')
                    if not isinstance(reply, dict) or reply.get('message') != 'API running.':
                        self.say(access, 'This address did not return the Home Assistant API. Check the address.')
                        continue
                    if access != known:
                        known, self.look = access, True
                for domain, service, data in filter(None, waiting):
                    self.ask(access, f'/api/services/{domain}/{service}', data)
                if self.look:  # (everything it has, for the Edit screen)
                    self.found, self.look = self.ask(access, '/api/states'), False
                    try:  # (a token that may not ask this, or a Home Assistant without areas, leaves them without rooms)
                        self.rooms = {entity: room for room, entities in self.ask(access, '/api/template', {'template': ROOMS}) for entity in entities}
                    except (urllib.error.HTTPError, ValueError, TypeError) as error:
                        getattr(error, 'close', bool)()
                        self.rooms = {}
                    self.board.changed()
                # ponytail: one request per entity of the bank on the pads every time; Home Assistant's WebSocket
                # API (subscribe_entities) pushes changes instead, if a bank ever holds very many
                for entity in watched:
                    try:
                        self.states[entity] = self.ask(access, f'/api/states/{entity}')
                    except urllib.error.HTTPError as error:
                        if error.code != 404:
                            raise
                        error.close()
                        self.states[entity] = {'state': 'unavailable', 'attributes': {}}  # (Home Assistant has no such thing)
                self.say(access, 'Connected.')
            except urllib.error.HTTPError as error:
                error.close()
                known = None
                if 300 <= error.code < 400:
                    self.say(access, 'Home Assistant redirected this connection. Enter its final address and token in Settings.')
                elif error.code == 401:
                    self.say(access, 'Home Assistant refused the access token. Enter a new long-lived access token.')
                elif error.code == 403:
                    self.say(access, 'Access denied (403). Check the account permissions and any network restrictions.')
                else:
                    self.say(access, f'Home Assistant answered with HTTP {error.code}. Check the server address.')
            except json.JSONDecodeError:
                known = None
                self.say(access, 'This address did not return the Home Assistant API. Check the address.')
            except (OSError, ValueError, http.client.HTTPException):
                known = None
                self.say(access, 'No answer from Home Assistant. Check the address and network connection.')

    def say(self, access, status):
        if self.result != (access, status):
            self.result = (access, status)
            self.board.changed()

    def call(self, domain, service, entity, **data):
        self.calls.put((domain, service, {'entity_id': entity} | data))

    def stop(self):
        self.going = False
        self.calls.put(None)

    # ---- what is on the pads
    def banks(self):
        return self.board.config.get('banks') or [{'name': 'Bank 1', 'pads': {}}]

    def shown(self, now):
        """The bank on the pads just now."""
        self.bank = min(self.bank, len(self.banks()) - 1)
        return self.bank

    @staticmethod
    def read(pads):
        """{(x, y): (pad, entity, the pad's table)} of a table of pads. A pad that opens its own subpage needs no
        entity (None then)."""
        placed = {}
        for pad, to in (pads if isinstance(pads, dict) else {}).items():
            entity = to.get('thing') if isinstance(to, dict) else None
            if (isinstance(entity, str) and entity.split('.')[0] in DOMAINS) or \
                    (isinstance(to, dict) and entity is None and 'page' in (to.get('tap'), to.get('hold'))):
                x, y = (int(n) - 1 for n in pad.split(','))
                placed[x, y] = (pad, entity, to)
        return placed

    def pads(self, bank):
        banks = self.banks()
        return self.read(banks[bank].get('pads')) if bank < len(banks) else {}

    def about(self, entity):
        return (self.states.get(entity) or {}).get('attributes') or {}

    @staticmethod
    def can(entity, wish):
        """The wish if it is something that can be done with the entity: one of its taps, or one that sets it to a value:
        'option:…' (a select's option, heating's mode, a player's source), 'brightness:…' (percent) and 'colour:#…' for a
        light, 'level:…' for what has a level (a player's volume in percent)."""
        if not isinstance(wish, str):
            return None
        domain = entity.split('.')[0]
        kind, _, value = wish.partition(':')
        fits = {'option': domain in CHOSEN and value, 'brightness': domain == 'light' and number(value, None) is not None,
                'colour': domain == 'light' and rgb(value, None), 'level': domain in LEVELLED and number(value, None) is not None}
        return wish if wish in DOMAINS[domain][2] or fits.get(kind) else None

    def tapped(self, found):
        """What a tap on the pad does: one of its entity's taps, 'page' (it opens the pad's own subpage, to stay), or None."""
        pad, entity, to = found
        if not entity:
            return 'page' if to.get('tap') == 'page' else None
        if 'tap' not in to:  # (until the owner chooses: the first thing that can be done with it)
            return next(iter(DOMAINS[entity.split('.')[0]][2]), None)
        return 'page' if to['tap'] == 'page' else self.can(entity, to['tap'])  # (one that does not fit does nothing)

    def held(self, found, inner=False):
        """What holding the pad is for: ('does', an entity, what is done with it), ('page', its own pads),
        ('controls', the entity they are of) or None."""
        pad, entity, to = found
        hold = to.get('hold')
        if hold == 'none' or (hold == 'page' and inner):
            return None
        if hold == 'page':
            return ('page', self.read(to.get('sub')))
        of = to.get('of', entity)  # (another entity, if the owner named one: one that cannot be used is not replaced)
        if not isinstance(of, str) or of.split('.')[0] not in DOMAINS:
            return None
        return ('does', of, hold) if self.can(of, hold) else ('controls', of) if hold == 'controls' and self.controls(of, to) else None

    def watched(self):
        """The entities to ask Home Assistant about: those of the bank on the pads, their subpages and what they control."""
        founds = list(self.pads(self.visible).values())
        founds += [inner for _, _, to in founds for inner in self.read(to.get('sub')).values()]
        return {entity for _, entity, _ in founds if entity} | {to['of'] for _, _, to in founds if isinstance(to.get('of'), str)}

    def controls(self, entity, to):
        """What an entity offers while a pad is held for it, row by row: [(colour, (domain, service, what to set)), ...]."""
        domain, about = entity.split('.')[0], self.about(entity)
        state, steps = (self.states.get(entity) or {}).get('state'), [mix(BLACK, WHITE, (x + 1) / 8) for x in range(8)]
        rows = []
        if domain == 'light' and DIMMABLE.intersection(about.get('supported_color_modes') or ()):
            rows.append([(steps[x], ('light', 'turn_on', {'brightness_pct': round((x + 1) * 12.5)})) for x in range(8)])
            if COLOURED & set(about.get('supported_color_modes') or ()):
                rows.append([(colour, ('light', 'turn_on', {'rgb_color': list(colour)})) for colour in SWATCHES])
        how = level(domain, about)
        if how:  # (eight steps from its lowest to its highest)
            service, key, low, high, fine, _ = how
            values = [low + (high - low) * x / 7 for x in range(8)]
            values = [round(low + math.floor((v - low) / fine + 1e-9) * fine, 6) if fine > 0 else v for v in values]  # (never above)
            rows.append([(steps[x], (domain, service, {key: values[x]})) for x in range(8)])
        which = choices(domain, state, about)
        if which:
            service, key, options, chosen, _ = which
            colour = rgb(to.get('colour') or DOMAINS[domain][1])
            cells = [(mix(BLACK, colour, 1 if o == chosen else .2), (domain, service, {key: o})) for o in options[:40]]
            rows += [cells[k:k + 8] for k in range(0, len(cells), 8)]
        return rows

    def cells(self, held, fingers=()):
        """Where a held pad's controls are: {(x, y): (colour, call)}, in the rows at the bottom, or at the top when the
        pad held is down there itself; never under a finger."""
        (x, y), rows = held[0], self.controls(held[4][1], held[1][2])
        ys = list(range(len(rows))) if y >= len(rows) else list(range(7, 7 - len(rows), -1))
        placed = {(cx, cy): cell for row, cy in zip(rows, ys) for cx, cell in enumerate(row)}
        for k, finger in enumerate(f for f in ((x, y), *fingers) if f in placed):  # (under a finger: it starts the next row)
            placed[k, ys[-1] + (1 if ys[0] == 0 else -1)] = placed.pop(finger)
        return placed

    def lit(self, found, now):
        pad, entity, to = found
        if not entity:  # (a pad that only opens its subpage: bright while that is open)
            return mix(BLACK, rgb(to.get('colour') or '#b9b6cc'), 1 if any(held and held[1][0] == pad for held in (self.stay, self.hold)) else .45)
        domain, known, tap = entity.split('.')[0], self.states.get(entity), self.tapped(found)
        kind, _, value = (tap or '').partition(':')
        colour = rgb(to.get('colour') or (value if kind == 'colour' else DOMAINS[domain][1]))
        state, about = (known['state'], known['attributes']) if known else (None, {})
        if state in (None, 'unavailable'):
            glow, colour = .12, colour if state is None else (255, 0, 0)
        elif self.flash and self.flash[:2] == (entity, tap) and now < self.flash[2]:
            glow = 1
        elif kind == 'option' and value:  # (the one chosen is lit)
            glow = 1 if (choices(domain, state, about) or [None] * 4)[3] == value else .2
        elif kind == 'colour' and value:
            glow = 1
        elif kind == 'brightness' and value:  # (a row of them reads as a ramp)
            glow = .15 + .85 * min(1., max(0., number(value, 100.) / 100))
        elif kind == 'level' and value:
            how = level(domain, about) or (None, '', 0., 100.)
            low, high = (0., 100.) if how[1] == 'volume_level' else how[2:4]
            glow = .15 + .85 * min(1., max(0., (number(value, low) - low) / ((high - low) or 1)))
        elif domain in STATELESS:
            glow = .4
        elif domain in SELECTS:
            glow = .6
        elif domain in NUMBERS:
            low, high = number(about.get('min'), 0.), number(about.get('max'), 100.)
            glow = .15 + .85 * min(1., max(0., (number(state, low) - low) / ((high - low) or 1)))
        else:
            bright = about.get('brightness')
            glow = (.4 + .6 * bright / 255 if isinstance(bright, (int, float)) else 1) if state not in DIM else .15
        return mix(BLACK, colour, glow)

    def name(self, found):
        """What the pad is, for the management page: its thing, or the value its tap sets."""
        if found[2].get('name') or not found[1]:  # (the owner's own word for it, and a pad that only opens its subpage)
            return str(found[2].get('name') or 'Subpage')
        kind, _, value = (self.tapped(found) or '').partition(':')
        if value and kind in ('brightness', 'level'):
            return f'{number(value, 0.):g}{" %" if kind == "brightness" else unit(found[1].split(".")[0])}'
        return value if kind == 'option' and value else self.about(found[1]).get('friendly_name') or found[1]

    def over(self, now):
        """The pads held long enough to show something of their own: (the bank's, the one of its subpage)."""
        shows = lambda held: held if held and held[4][0] != 'does' and now - held[2] > HOLD else None
        outer = self.stay or shows(self.hold)
        return outer, outer and shows(self.inner)

    def due(self, now):
        """A pad held long enough for what its hold does: done once, while it is still down."""
        for held in (self.hold, self.inner):
            if held and held[4][0] == 'does' and not held[3] and now - held[2] > HOLD:
                held[3] = True
                self.do(held[4][1], held[4][2], now)

    def showing(self, now):
        """(the pads lit just now: {(x, y): pad}, the controls among them: {(x, y): (colour, call)})."""
        outer, inner = self.over(now)
        if not outer:
            return self.pads(self.shown(now)), {}
        if inner:
            return {outer[0]: outer[1], inner[0]: inner[1]}, self.cells(inner, (outer[0],))
        if outer[4][0] == 'page':
            return {at: found for at, found in outer[4][1].items() if at != outer[0]} | {outer[0]: outer[1]}, {}
        return {outer[0]: outer[1]}, self.cells(outer)

    def frame(self, now):
        picture, visible = [BLACK] * 64, self.shown(now)
        if visible != self.visible:
            self.visible = visible
            self.calls.put(None)  # (another bank on the pads: its things are asked about at once)
        if self.status != 'Connected.' or not any(bank.get('pads') for bank in self.banks()):  # (nothing to switch yet: a house, breathing)
            self.label({})
            glow = .22 + .12 * math.sin(now * 2)
            for r, row in enumerate(HOUSE):
                for x, c in enumerate(row):
                    if c == '#':
                        picture[8 * (7 - r) + x] = mix(BLACK, rgb('#a070ff'), glow)
            return picture
        self.due(now)
        pads, controls = self.showing(now)
        labels = {}
        for (x, y), found in pads.items():
            picture[8 * y + x], labels[f'{x + 1},{y + 1}'] = self.lit(found, now), self.name(found)
        for (x, y), (colour, call) in controls.items():
            picture[8 * y + x], labels[f'{x + 1},{y + 1}'] = colour, words(call[2])
        self.label(labels)
        return picture

    def label(self, labels):
        if labels != self.labels:
            self.labels = labels
            self.board.changed()  # (the management page fetches what the pads are now)

    # ---- pressing them
    def press(self, x, y, velocity, now):
        if self.status != 'Connected.':
            return
        outer, _ = self.over(now)
        if not outer:
            found = self.pads(self.shown(now)).get((x, y))
            what = found and not self.hold and self.held(found)
            if what:
                self.hold = [(x, y), found, now, False, what]  # (what a tap does, it does when the pad is let go)
            elif found:
                self.tap(found, now)
            return
        if outer is self.stay and (x, y) == outer[0]:  # (the pad whose subpage is open: it closes)
            self.stay = self.inner = None
            return
        pads, controls = self.showing(now)
        outer[3] = True
        if (x, y) in controls:  # (a held thing's controls are showing: one of them)
            domain, service, data = controls[x, y][1]
            entity = (self.inner or outer)[4][1]
            if 'option' in data or 'hvac_mode' in data:
                self.states.setdefault(entity, {'attributes': {}})['state'] = next(iter(data.values()))  # (shown at once)
            self.call(domain, service, entity, **data)
        elif (x, y) in pads and (x, y) != outer[0] and not self.inner:  # (a pad of the subpage that is showing)
            what = self.held(pads[x, y], inner=True)
            if what:
                self.inner = [(x, y), pads[x, y], now, False, what]
            else:
                self.tap(pads[x, y], now)

    def tap(self, found, now):
        if self.tapped(found) != 'page':
            return self.do(found[1], self.tapped(found), now)
        at = tuple(int(n) - 1 for n in found[0].split(','))
        if not self.stay and self.pads(self.shown(now)).get(at) == found:  # (a pad of the bank: a subpage has none of its own)
            self.stay = [at, found, now, False, ('page', self.read(found[2].get('sub')))]

    def do(self, entity, tap, now):
        if not tap or not entity:
            return
        domain = entity.split('.')[0]
        known = self.states.setdefault(entity, {'state': 'unknown', 'attributes': {}})
        kind, _, value = tap.partition(':')
        if value:  # (a tap that sets it to a value)
            about, self.flash = known.get('attributes') or {}, (entity, tap, now + .3)
            which, how = choices(domain, known.get('state'), about), level(domain, about)
            if kind == 'option' and which:
                if domain != 'media_player':
                    known['state'] = value  # (shown at once; Home Assistant confirms)
                self.call(domain, which[0], entity, **{which[1]: value})
            elif (kind == 'brightness' and DIMMABLE.intersection(about.get('supported_color_modes') or ())) or (
                    kind == 'colour' and COLOURED.intersection(about.get('supported_color_modes') or ())):
                known['state'] = 'on'
                self.call('light', 'turn_on', entity, **({'brightness_pct': number(value, 100.)} if kind == 'brightness' else {'rgb_color': list(rgb(value))}))
            elif kind == 'level' and how:
                self.call(domain, how[0], entity, **{how[1]: min(how[3], max(how[2], number(value, how[2]) / (100 if how[1] == 'volume_level' else 1)))})
            return
        _, service, leaves = DOMAINS[domain][2][tap]
        data = {'is_volume_muted': not known.get('attributes', {}).get('is_volume_muted')} if tap == 'mute' else {}
        if leaves:
            known['state'] = FLIP.get(known.get('state'), 'on') if leaves == 'flip' else leaves
        else:
            self.flash = (entity, tap, now + .3)
        self.call(domain, service, entity, **data)

    def release(self, x, y, now):
        self.due(now)
        for held in (self.inner, self.hold):
            if held and held[0] == (x, y):
                if not held[3] and now - held[2] <= HOLD:
                    self.tap(held[1], now)
                if held is self.hold and held[4][0] == 'page' and held[1][2].get('stays') and now - held[2] > HOLD:
                    self.stay, self.hold = held, None  # (a subpage that stays, until its pad is pressed again: what is held on it stays held)
                elif held is self.hold:
                    self.hold = self.inner = None  # (what it showed is gone, and whatever was held there)
                else:
                    self.inner = None
                return

    def go(self, bank):
        self.bank, self.hold, self.inner, self.stay = bank, None, None, None

    def open(self):
        self.on_pads = True
        self.calls.put(None)

    def close(self):
        self.on_pads = False
        if self.back:
            self.bank = self.back[1]  # (a bank only shown while its button is held does not stay)
        self.back = self.hold = self.inner = self.stay = None

    def action(self, name, now):
        banks = self.banks()
        if name == 'all_off':
            founds = [found for bank in range(len(banks)) for found in self.pads(bank).values()]
            founds += [inner for _, _, to in founds for inner in self.read(to.get('sub')).values()]
            for entity in {entity for _, thing, to in founds for entity in (thing, to.get('of')) if isinstance(entity, str) and entity.startswith('light.')}:
                self.states.setdefault(entity, {'attributes': {}})['state'] = 'off'
                self.call('light', 'turn_off', entity)
        elif name == 'next_bank':
            self.go((self.bank + 1) % len(banks))
        elif name.startswith('bank:') and name[5:].isdigit() and int(name[5:]) < len(banks):
            self.back = (name, self.bank, now)  # (a tap goes there to stay; held, it is shown until the button is let go)
            self.go(int(name[5:]))

    def released(self, name, now):
        if self.back and self.back[0] == name:
            if now - self.back[2] > HOLD:
                self.go(self.back[1])
                self.board.changed()
            self.back = None

    def actions(self):
        return {f'bank:{k}': f'Bank {k + 1}: {bank.get("name", "")}' for k, bank in enumerate(self.banks())}

    def lights(self, now):
        return {'all_off': (255, 60, 30), 'next_bank': (0, 90, 30)} | \
               {f'bank:{k}': (0, 255, 80) if k == self.shown(now) else (0, 60, 20) for k in range(len(self.banks()))}

    # ---- for the management page
    def things(self):
        """What Home Assistant has that can sit on a pad, kind by kind. Each says what a tap can do with it (`taps`:
        [[tap, words], …]; one for each option of a select, with what to call such a pad; one that sets a value has a
        fourth entry saying what the value can be, and the pad keeps it as 'tap:value'), what holding a pad for it shows (`controls`:
        words, or None) and the room it is in (`place`)."""
        things, kinds = [], list(dict.fromkeys(kind for kind, _, _ in DOMAINS.values()))
        for found in self.found:
            entity, domain = found['entity_id'], found['entity_id'].split('.')[0]
            if domain not in DOMAINS:
                continue
            kind, colour, taps = DOMAINS[domain]
            about, how = found.get('attributes') or {}, level(domain, found.get('attributes') or {})
            which = choices(domain, found.get('state'), about)
            modes = set(about.get('supported_color_modes') or ()) if domain == 'light' else set()
            dimmable, coloured = bool(DIMMABLE & modes), bool(COLOURED & modes)
            offers = (['brightness'] if dimmable else []) + (['colours'] if coloured else [])
            offers += ([how[5]] if how else []) + ([which[4]] if which else [])
            low, high, fine = (0., 100., 1.) if how and how[1] == 'volume_level' else how[2:5] if how else (0., 0., 0.)
            things.append({'id': entity, 'name': about.get('friendly_name') or entity, 'colour': colour, 'kind': kind,
                           'taps': [[tap, said] for tap, (said, _, _) in taps.items()]
                                   + [['brightness', 'Set brightness', None, {'type': 'range', 'min': 1, 'max': 100, 'step': 1, 'default': 50, 'unit': ' %'}]] * dimmable
                                   + [['colour', 'Set colour', None, {'type': 'colour', 'default': '#ff0000'}]] * bool(coloured)
                                   + ([['level', f'Set {how[5]}', None, {'type': 'range', 'min': low, 'max': high, 'step': fine or 1,
                                                                              'default': (low + high) / 2, 'unit': unit(domain)}]] if how else [])
                                   + [[f'option:{option}', f'Choose {option}', option] for option in (which[2] if which else [])],
                           'controls': ' and '.join(offers) or None, 'place': self.rooms.get(entity)})
        natural = lambda name: [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', name.lower())]  # (lamp 5 before lamp 12)
        return sorted(things, key=lambda thing: (kinds.index(thing['kind']), natural(thing['name'])))

    def state(self):
        """`setup`: what is missing before the page can do anything, or None once Home Assistant answers."""
        status = self.status
        return {'status': status, 'bank': self.bank, 'labels': self.labels,
                'setup': None if status in ('Connected.', 'Checking connection…') else status}

    def request(self, name, params, body):
        """look: read again what Home Assistant has."""
        self.look = True
        self.calls.put(None)
        return {}
