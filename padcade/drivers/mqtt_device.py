"""The Launchpad's Home Assistant device."""
import asyncio
import json
import math
import os
from time import monotonic

from .sound import LEVELS


def apply_command(board, name, value):
    if name == 'display' and value in ('ON', 'OFF'):
        if board.power != (value == 'ON'):
            board.set_power(value == 'ON')
    elif name == 'profile':
        board.open(next((key for key, label in board.pages().items() if value in (key, label)), None))
    elif name in ('brightness', 'volume'):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(number) or not number.is_integer():
            return
        if name == 'brightness' and 5 <= number <= 100:
            board.configure(board.config | {'brightness': int(number)})
        if name == 'volume' and 1 <= number <= len(LEVELS) and board.volume != number:
            board.set_volume(int(number))


def settings(board):
    return dict(display='ON' if board.power else 'OFF', profile=board.pages().get(board.page),
                brightness=board.config['brightness'], volume=board.volume)


def restore_settings(board, path):
    try:
        values = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if isinstance(values, dict):
        for name, value in values.items():
            apply_command(board, name, value)


def save_settings(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as file:
        json.dump(values, file)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def discovery(board, device_id):
    components = {
        'display': dict(platform='switch', name='Display', icon='mdi:power'),
        'profile': dict(platform='select', name='Profile', options=list(board.pages().values())),
        'brightness': dict(platform='number', name='Brightness', min=5, max=100, step=5, mode='slider',
                           unit_of_measurement='%', icon='mdi:brightness-6'),
        'volume': dict(platform='number', name='Volume', min=1, max=len(LEVELS), step=1, mode='slider',
                       icon='mdi:volume-high'),
        'battery': dict(platform='sensor', name='Battery', device_class='battery', state_class='measurement',
                        unit_of_measurement='%', entity_category='diagnostic'),
        'external_power': dict(platform='binary_sensor', name='External power', device_class='plug',
                               entity_category='diagnostic'),
    }
    for name, component in components.items():
        component.update(unique_id=f'{device_id}_{name}', default_entity_id=f'{component["platform"]}.{device_id}_{name}',
                         value_template='{{ value_json.' + name + ' }}')
        if name in ('battery', 'external_power'):
            component.update(availability=[dict(topic=f'{device_id}/availability'), dict(topic=f'{device_id}/battery/availability')], availability_mode='all')
        else:
            component['command_topic'] = f'{device_id}/set/{name}'
            component['retain'] = False
    return dict(device=dict(identifiers=['launchpad_pro_pi' if device_id == 'launchpad' else device_id],
                            name='Launchpad' if device_id == 'launchpad' else f'Padcade {device_id.removeprefix("padcade_")}', manufacturer='Novation',
                            model='Launchpad Pro (Raspberry Pi)'),
                origin=dict(name='Launchpad'), components=components, state_topic=f'{device_id}/state',
                availability=[dict(topic=f'{device_id}/availability')], qos=1)


async def run_mqtt(board, credentials, path, stopped):
    from paho.mqtt import client as mqtt

    loop = asyncio.get_running_loop()
    api = {'callback_api_version': mqtt.CallbackAPIVersion.VERSION2} if hasattr(mqtt, 'CallbackAPIVersion') else {}
    device_id = board.config['mqtt_id']
    commands = f'{device_id}/set/'
    availability = f'{device_id}/availability'
    client = mqtt.Client(client_id='launchpad-pro-pi' if device_id == 'launchpad' else device_id,
                         clean_session=False, **api)
    client.username_pw_set(credentials.get('username'), credentials.get('password'))
    client.will_set(availability, 'offline', qos=1, retain=True)
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    connected = False
    sent = None

    def connection(online, message=None):
        nonlocal connected, sent
        if message:
            board.mqtt_status(message)
        elif connected:
            board.mqtt_status('Disconnected. Retrying…')
        connected, sent = online, None

    def on_connect(client, userdata, flags, reason, properties=None):
        if getattr(reason, 'is_failure', reason != 0):
            code = getattr(reason, 'value', reason)
            message = ('Credentials rejected. Check the MQTT username, password and broker permissions.'
                       if code in (4, 5, 134, 135) else 'Connection refused by the broker. Retrying…')
            loop.call_soon_threadsafe(connection, False, message)
            return
        client.subscribe(commands + '+', qos=1)
        client.publish(f'homeassistant/device/{device_id}/config', json.dumps(discovery(board, device_id)), qos=1, retain=True)
        loop.call_soon_threadsafe(connection, True, 'Connected')

    def on_message(client, userdata, message):
        if message.retain:
            return
        try:
            value = message.payload.decode('utf-8')
        except UnicodeDecodeError:
            return
        loop.call_soon_threadsafe(apply_command, board, message.topic.removeprefix(commands), value)

    client.on_connect = on_connect
    client.on_connect_fail = lambda *_: loop.call_soon_threadsafe(connection, False, 'Broker unreachable. Check the address and network.')
    client.on_message = on_message
    client.on_disconnect = lambda *_: loop.call_soon_threadsafe(connection, False)
    client.connect_async(credentials['host'], credentials.get('port', 1883), keepalive=15)
    client.loop_start()
    saved = settings(board)
    try:
        while not stopped.is_set():
            values = settings(board)
            if values != saved:
                try:
                    await asyncio.to_thread(save_settings, path, values)
                except OSError as error:
                    print(f'Launchpad settings could not be saved: {type(error).__name__}', flush=True)
                    await asyncio.sleep(1)
                else:
                    saved = values
            sample = board.battery_sample
            fresh = sample is not None and 0 <= monotonic() - sample.at <= 45
            values = dict(values, battery=sample.percent if fresh else None,
                          external_power=('ON' if sample.plugged else 'OFF') if fresh else None)
            if connected and values != sent:
                client.publish(f'{device_id}/state', json.dumps(values), qos=1, retain=True)
                if sent is None or (sent['battery'] is not None) != fresh:
                    client.publish(f'{device_id}/battery/availability', 'online' if fresh else 'offline', qos=1, retain=True)
                if sent is None:
                    client.publish(availability, 'online', qos=1, retain=True)
                sent = values
            await asyncio.sleep(.25)
    finally:
        try:
            if client.is_connected():
                offline = client.publish(availability, 'offline', qos=1, retain=True)
                await asyncio.to_thread(offline.wait_for_publish, 1)
        finally:
            client.disconnect()
            await asyncio.to_thread(client.loop_stop)
