"""A stand-in for Home Assistant's REST API: a few entities, a token, and services that do what they are asked.
The tests use it; run by itself it is something to try the Home Assistant page against without a Home Assistant:

    python3 -m tests.hass        then, on the page's Settings: the address it prints and the access token "good"
    python3 -m tests.hass 8124 crowd      the same with 150 more things in six rooms
"""
import json
from collections import Counter
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Hass(ThreadingHTTPServer):
    """It answers who has the token, and keeps what its services were asked (`calls`)."""
    def __init__(self, port=0):
        super().__init__(('127.0.0.1', port), Answer)
        self.daemon_threads, self.calls, self.asked, self.seen = True, [], 0, Counter()  # (seen: how often each entity was asked about)
        self.states = {
            'light.desk': {'state': 'off', 'attributes': {'friendly_name': 'Desk lamp', 'supported_color_modes': ['brightness']}},
            'light.strip': {'state': 'on', 'attributes': {'friendly_name': 'Strip', 'supported_color_modes': ['color_temp', 'hs'], 'brightness': 128}},
            'switch.fan': {'state': 'on', 'attributes': {'friendly_name': 'Fan'}},
            'scene.movie': {'state': '2026-01-01T00:00:00', 'attributes': {'friendly_name': 'Movie'}},
            'select.mood': {'state': 'Calm', 'attributes': {'friendly_name': 'Mood', 'options': ['Calm', 'Party']}},
            'number.heat': {'state': '20.0', 'attributes': {'friendly_name': 'Heat', 'min': 10, 'max': 30, 'step': .5}},
            'media_player.speakers': {'state': 'on', 'attributes': {'friendly_name': 'Speakers', 'volume_level': .2, 'is_volume_muted': False,
                                                                    'source': 'TV', 'source_list': ['TV', 'Radio']}},
            'cover.blind': {'state': 'open', 'attributes': {'friendly_name': 'Blind', 'current_position': 100, 'supported_features': 15}},
            'climate.room': {'state': 'heat', 'attributes': {'friendly_name': 'Heating', 'min_temp': 16, 'max_temp': 30, 'target_temp_step': .5,
                                                             'temperature': 21, 'hvac_modes': ['off', 'heat', 'auto']}},
            'lock.door': {'state': 'locked', 'attributes': {'friendly_name': 'Front door'}},
            'vacuum.robot': {'state': 'docked', 'attributes': {'friendly_name': 'Robot'}},
            'automation.night': {'state': 'on', 'attributes': {'friendly_name': 'Night mode'}},
            'sensor.heat': {'state': '21', 'attributes': {}},
        }
        self.rooms = {'Study': ['light.desk', 'light.strip', 'switch.fan', 'select.mood'], 'Living room': ['media_player.speakers', 'cover.blind', 'scene.movie']}
        threading.Thread(target=self.serve_forever, args=(.02,), daemon=True).start()
        self.address = 'http://127.0.0.1:%d' % self.server_address[1]


class Answer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def answer(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        hass = self.server
        hass.asked += 1
        if self.path == '/api/' and hasattr(hass, 'pending'):
            hass.pending.wait(3)
        if self.headers.get('Authorization') == 'Bearer restricted':
            return self.answer({}, 403)
        if self.headers.get('Authorization') != 'Bearer good':
            return self.answer({}, 401)
        if self.path == '/moved/api/' and hasattr(hass, 'moved_to'):
            self.send_response(302)
            self.send_header('Location', hass.moved_to + '/api/')
            self.end_headers()
            return
        if self.path == '/api/':
            return self.answer(getattr(hass, 'api_reply', {'message': 'API running.'}))
        if self.path == '/api/states':
            return self.answer([{'entity_id': entity} | state for entity, state in hass.states.items()])
        entity = self.path.removeprefix('/api/states/')
        hass.seen[entity] += 1
        self.answer(hass.states[entity], 200) if entity in hass.states else self.answer({}, 404)

    def do_POST(self):
        hass = self.server
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/api/template':  # (the one template the page asks: its areas and what is in them)
            return self.answer([[room, entities] for room, entities in hass.rooms.items()])
        _, _, _, domain, service = self.path.split('/')
        hass.calls.append((domain, service, data))
        state = hass.states.get(data['entity_id'], {'attributes': {}})
        about = state['attributes']
        sets = {'set_value': None, 'volume_set': 'volume_level', 'volume_mute': 'is_volume_muted', 'set_percentage': 'percentage',
                'set_cover_position': 'current_position', 'set_temperature': 'temperature', 'select_source': 'source'}
        leaves = {'open_cover': 'open', 'close_cover': 'closed', 'lock': 'locked', 'unlock': 'unlocked', 'start': 'cleaning',
                  'pause': 'paused', 'return_to_base': 'returning'}
        if service == 'toggle':
            state['state'] = {'on': 'off', 'off': 'on', 'open': 'closed', 'closed': 'open'}.get(state['state'], 'on')
        elif service in ('turn_on', 'turn_off') and domain not in ('scene', 'script'):
            state['state'] = service[5:]
            if 'brightness_pct' in data:
                about['brightness'] = round(data['brightness_pct'] * 2.55)
            if 'rgb_color' in data:
                about['rgb_color'] = data['rgb_color']
        elif service in leaves:
            state['state'] = leaves[service]
        elif service == 'select_option':
            state['state'] = data['option']
        elif service == 'select_next':
            state['state'] = about['options'][(about['options'].index(state['state']) + 1) % len(about['options'])]
        elif service == 'set_hvac_mode':
            state['state'] = data['hvac_mode']
        elif service == 'set_value':
            state['state'] = str(data['value'])
        elif service in sets:
            about[sets[service]] = next(v for k, v in data.items() if k != 'entity_id')
        self.answer([])


def crowd(hass, count=150):
    """A house full of things, to see the page's lists with as many as a real home has."""
    kinds = (('light', 'lamp'), ('switch', 'plug'), ('script', 'routine'), ('scene', 'mood'), ('automation', 'rule'), ('script', 'sequence'))
    rooms = ('Kitchen', 'Bedroom', 'Hall', 'Bath', 'Office', 'Garden', None)
    for k in range(count):
        (domain, word), room = kinds[k % 6], rooms[k % 7]
        entity = f'{domain}.{(room or "house").lower()}_{word}_{k // 6 + 1}'
        hass.states[entity] = {'state': 'off', 'attributes': {'friendly_name': f'{room or "House"} {word} {k // 6 + 1}'}}
        if room:
            hass.rooms.setdefault(room, []).append(entity)


if __name__ == '__main__':
    hass = Hass(int(sys.argv[1]) if len(sys.argv) > 1 else 8124)
    if 'crowd' in sys.argv:
        crowd(hass)
    print(f'A stand-in Home Assistant at {hass.address}, access token: good  (Ctrl-C ends it)', flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
