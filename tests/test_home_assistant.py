"""The Home Assistant page, against a stand-in for Home Assistant's REST API."""
import threading
import time
import unittest

from padcade import config
from tests.hass import Hass
from tests.kit import Home, level, pads, ship


def soon(what, seconds=3.):
    """Waits until what() is true (the page talks to Home Assistant in a thread of its own)."""
    deadline = time.monotonic() + seconds
    while not what() and time.monotonic() < deadline:
        time.sleep(.01)
    return what()


class Page(unittest.TestCase):
    def setUp(self):
        self.hass = Hass()
        self.addCleanup(self.hass.shutdown)
        self.addCleanup(self.hass.server_close)
        self.home = Home(self)
        ship(self.home, 'home_assistant')
        self.board = self.home.board()
        self.addCleanup(self.set, enabled=False)  # (stops the page's thread)
        self.set(pads={'1,8': {'thing': 'light.desk', 'hold': 'controls'}, '2,8': {'thing': 'switch.fan'}, '3,8': {'thing': 'scene.movie'},
                       '4,8': {'thing': 'select.mood', 'tap': 'option:Calm', 'hold': 'none'},
                       '5,8': {'thing': 'select.mood', 'tap': 'option:Party', 'hold': 'none'}, '6,8': {'thing': 'light.gone'}})
        self.board.press(4, 9, 127, 0.)
        self.assertTrue(soon(lambda: self.status() == 'Connected.'), self.status())
        asked = self.hass.asked  # (and it has asked about every pad's entity since: five of them, so two rounds make one whole)
        self.assertTrue(soon(lambda: self.hass.asked >= asked + 10))

    def set(self, token='good', pads=None, **more):
        settings = self.board.config
        table = settings['plugins']['home_assistant']
        table |= {'enabled': True, 'settings': {'address': self.hass.address, 'token': token, 'every': .05}} | more
        if pads is not None:
            table['pads'] = pads
        self.board.configure(settings)

    def status(self):
        return self.board.state()['plugins']['home_assistant']['state'].get('status')

    def pad(self, column, now=1.):
        return level(self.board.frame(now)[80 + column])

    def tap(self, x, y, now):
        self.board.press(x, y, 127, now)
        self.board.release(x, y, now + .1)

    def banks(self, *banks):
        """More banks behind the first one, each on its bottom button (bottom 1 is the first bank)."""
        settings = self.board.config
        table = settings['plugins']['home_assistant']
        table['banks'] = table['banks'][:1] + [{'name': f'Bank {k + 2}', 'pads': pads} for k, pads in enumerate(banks)]
        table['buttons'] = {f'bottom{k + 1}': f'bank:{k}' for k in range(len(table['banks']))}
        self.board.configure(settings)

    def place(self, **pads):
        """Other things on the first bank's top row, from the left: what is there now goes."""
        settings = self.board.config
        placed = {f'{k + 1},8': to if isinstance(to, dict) else {'thing': to} for k, to in enumerate(pads.values())}
        self.lay(placed)

    def lay(self, pads):
        """These pads as the first bank, and Home Assistant's answer about each of their entities in."""
        settings, entities = self.board.config, set()
        for to in [*pads.values(), *(inner for to in pads.values() for inner in (to.get('sub') or {}).values())]:
            entities.update(entity for entity in (to.get('thing'), to.get('of')) if isinstance(entity, str))
        settings['plugins']['home_assistant']['banks'][0]['pads'] = pads
        before = self.hass.seen.copy()
        self.board.configure(settings)
        # (asked twice since: it asks one after the other, so the first answer has arrived)
        self.assertTrue(soon(lambda: all(self.hass.seen[entity] >= before[entity] + 2 for entity in entities)))

    def called(self):
        return [(domain, service, {k: v for k, v in data.items() if k != 'entity_id'}) for domain, service, data in self.hass.calls]

    def things(self):
        return {thing['id']: thing for thing in self.board.state()['plugins']['home_assistant']['items']}

    def labels(self):
        return self.board.state()['plugins']['home_assistant']['state']['labels']

    def test_a_moved_server_asks_for_the_new_address(self):
        other = Hass()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        self.hass.moved_to = other.address
        self.set(settings={'address': self.hass.address + '/moved', 'token': 'good', 'every': .05})
        self.assertTrue(soon(lambda: 'redirect' in self.status().lower()), self.status())
        self.assertEqual(other.asked, 0)

    def test_it_offers_what_home_assistant_has_that_can_be_switched_or_set_kind_by_kind(self):
        items = lambda: self.board.state()['plugins']['home_assistant']['items']
        self.assertTrue(soon(lambda: len(items()) == 12))
        self.assertEqual([(thing['kind'], thing['name']) for thing in items()],
                         [('Lights', 'Desk lamp'), ('Lights', 'Strip'), ('Switches', 'Fan'), ('Fans and climate', 'Heating'),
                          ('Covers and valves', 'Blind'), ('Players', 'Speakers'), ('Scenes and scripts', 'Movie'),
                          ('Automations', 'Night mode'), ('Locks and vacuums', 'Front door'), ('Locks and vacuums', 'Robot'),
                          ('Selects', 'Mood'), ('Levels', 'Heat')])

    def test_the_page_learns_what_a_tap_can_do_with_a_thing_and_what_a_hold_shows_of_it(self):
        self.assertTrue(soon(lambda: len(self.things()) == 12))
        things = self.things()
        self.assertEqual([tap[0] for tap in things['select.mood']['taps']], ['next', 'option:Calm', 'option:Party'])
        self.assertEqual([tap[0] for tap in things['lock.door']['taps']], ['lock', 'unlock'])  # (no toggle, on purpose)
        self.assertEqual({thing: about['controls'] for thing, about in things.items() if about['controls']},
                         {'light.desk': 'brightness', 'light.strip': 'brightness and colours', 'climate.room': 'temperature and its modes',
                          'cover.blind': 'position', 'media_player.speakers': 'volume and its sources', 'select.mood': 'its options',
                          'number.heat': 'its level'})
        self.assertTrue(self.board.state()['plugins']['home_assistant']['holds'])  # (a page to hold pads on)

    def test_on_off_lights_and_fixed_position_covers_only_offer_their_supported_actions(self):
        self.hass.states['light.simple'] = {'state': 'off', 'attributes': {'supported_color_modes': ['onoff']}}
        self.hass.states['cover.fixed'] = {'state': 'closed', 'attributes': {'supported_features': 3}}
        self.board.request('home_assistant', 'look', {}, b'')
        self.assertTrue(soon(lambda: 'cover.fixed' in self.things()))
        for entity, unavailable in [('light.simple', 'brightness'), ('cover.fixed', 'level')]:
            thing = self.things()[entity]
            self.assertIsNone(thing['controls'])
            self.assertNotIn(unavailable, [tap[0] for tap in thing['taps']])

    def test_the_page_learns_which_room_a_thing_is_in_if_home_assistant_says(self):
        self.assertTrue(soon(lambda: len(self.things()) == 12))
        self.assertEqual([self.things()[thing]['place'] for thing in ('light.desk', 'cover.blind', 'lock.door')], ['Study', 'Living room', None])

    def test_pads_placed_before_the_page_had_banks_are_its_first_bank(self):
        table = config.load(self.home.path / 'config.toml')['plugins']['home_assistant']
        self.assertEqual(len(table['banks'][0]['pads']), 6)
        self.assertNotIn('pads', table)

    def test_a_round_button_goes_to_its_bank_and_held_it_shows_the_bank_only_meanwhile(self):
        self.banks({'1,8': {'thing': 'switch.fan'}})
        self.tap(2, 0, 2.)  # (bottom 2, tapped: the second bank, to stay)
        self.tap(1, 8, 3.)
        self.assertTrue(soon(lambda: self.hass.states['switch.fan']['state'] == 'off'))
        self.tap(1, 0, 4.)  # (back on the first)
        self.board.press(2, 0, 127, 5.)  # (held)
        self.tap(1, 8, 6.)
        self.assertTrue(soon(lambda: self.hass.states['switch.fan']['state'] == 'on'))
        self.board.release(2, 0, 7.)
        self.tap(1, 8, 8.)  # (the first bank again: its lamp)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['state'] == 'on'))
        self.assertEqual(self.hass.states['switch.fan']['state'], 'on')

    def test_a_bank_only_shown_is_not_kept_when_the_pads_go_dark_meanwhile_and_the_page_hears_of_the_way_back(self):
        self.banks({'1,8': {'thing': 'switch.fan'}})
        self.board.press(2, 0, 127, 2.)  # (bottom 2 held: the second bank)
        self.board.press(8, 9, 127, 3.)  # (Power)
        self.board.release(2, 0, 4.)
        self.board.press(8, 9, 127, 5.)  # (any press wakes it)
        self.tap(1, 8, 6.)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['state'] == 'on'))  # (the first bank's lamp)
        self.board.press(2, 0, 127, 7.)
        seen = self.board.state()['revision']
        self.board.release(2, 0, 8.)
        self.assertGreater(self.board.state()['revision'], seen)

    # ---- a tap
    def test_a_tap_switches_a_light_there_and_the_pad_shows_what_it_is(self):
        off = self.pad(1)
        self.tap(1, 8, 2.)
        self.assertGreater(self.pad(1, 2.2), off)  # (at once)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['state'] == 'on'))
        self.assertEqual(self.hass.calls, [('light', 'toggle', {'entity_id': 'light.desk'})])

    def test_a_tap_does_what_the_owner_chose_for_the_pad_or_nothing(self):
        self.place(blind={'thing': 'cover.blind', 'tap': 'close'}, door={'thing': 'lock.door', 'tap': 'unlock'},
                   lamp={'thing': 'light.desk', 'tap': 'on'}, fan={'thing': 'switch.fan', 'tap': 'none'}, robot='vacuum.robot')
        for column in (1, 2, 3, 4, 5):
            self.tap(column, 8, 2. + column)
        self.assertTrue(soon(lambda: len(self.called()) == 4))
        self.assertEqual(self.called(), [('cover', 'close_cover', {}), ('lock', 'unlock', {}), ('light', 'turn_on', {}), ('vacuum', 'start', {})])
        self.assertEqual(self.hass.states['switch.fan']['state'], 'on')

    def test_what_changes_in_home_assistant_shows_on_the_pads(self):
        on = self.pad(2)
        self.hass.states['switch.fan']['state'] = 'off'
        self.assertTrue(soon(lambda: self.pad(2) < on))

    def test_a_tap_can_set_a_light_to_a_brightness_or_a_colour_and_a_level_to_a_value(self):
        self.place(dim={'thing': 'light.strip', 'tap': 'brightness:25'}, red={'thing': 'light.strip', 'tap': 'colour:#ff0000'},
                   warm={'thing': 'number.heat', 'tap': 'level:15'}, quiet={'thing': 'media_player.speakers', 'tap': 'level:40'},
                   radio={'thing': 'media_player.speakers', 'tap': 'option:Radio'})
        self.board.frame(1.)
        self.assertEqual([self.labels()[f'{k},8'] for k in (1, 3, 4, 5)], ['25 %', '15', '40 %', 'Radio'])
        for k in range(1, 6):
            self.tap(k, 8, 2. + k)
        self.assertTrue(soon(lambda: len(self.hass.calls) == 5))
        self.assertEqual(self.called(), [('light', 'turn_on', {'brightness_pct': 25.}), ('light', 'turn_on', {'rgb_color': [255, 0, 0]}),
                                         ('number', 'set_value', {'value': 15.}), ('media_player', 'volume_set', {'volume_level': .4}),
                                         ('media_player', 'select_source', {'source': 'Radio'})])
        things = self.things()
        self.assertEqual([tap[0] for tap in things['light.strip']['taps']], ['toggle', 'on', 'off', 'brightness', 'colour'])
        self.assertEqual([tap[0] for tap in things['light.desk']['taps']], ['toggle', 'on', 'off', 'brightness'])  # (it takes no colours)
        self.assertEqual(things['number.heat']['taps'][0][3], {'type': 'range', 'min': 10., 'max': 30., 'step': .5, 'default': 20., 'unit': ''})

    def test_a_select_can_have_a_pad_for_each_option_and_the_one_chosen_is_lit(self):
        self.assertGreater(self.pad(4), self.pad(5))
        self.tap(5, 8, 2.)
        self.assertTrue(soon(lambda: self.hass.states['select.mood']['state'] == 'Party'))

    def test_a_scene_is_started_by_its_pad(self):
        self.tap(3, 8, 2.)
        self.assertTrue(soon(lambda: self.hass.calls == [('scene', 'turn_on', {'entity_id': 'scene.movie'})]))

    # ---- a hold
    def test_a_light_held_can_be_set_brighter_or_dimmer_without_being_switched(self):
        self.board.press(1, 8, 127, 2.)
        dark = level(self.board.frame(2.1)[11])
        self.assertGreater(level(self.board.frame(3.)[18]), dark)  # (held: the steps show along the bottom row)
        self.board.press(8, 1, 127, 3.1)
        self.board.release(8, 1, 3.2)
        self.board.release(1, 8, 3.3)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['attributes'].get('brightness') == 255))
        self.assertEqual([call[:2] for call in self.hass.calls], [('light', 'turn_on')])
        self.assertEqual(level(self.board.frame(4.)[18]), 0)

    def test_a_held_light_that_takes_colours_has_a_row_of_them_and_nothing_else_is_in_the_way(self):
        self.place(strip={'thing': 'light.strip', 'hold': 'controls'}, desk='light.desk', fan='switch.fan')
        strip = self.pad(1)
        self.board.press(1, 8, 127, 2.)
        held = self.board.frame(3.)
        self.assertEqual((level(held[81]), level(held[83])), (strip, 0))  # (the pad held as it is, the fan's gone)
        self.assertGreater(level(held[18]), 0)
        self.assertNotEqual(held[21], held[22])  # (the second row: colours)
        for x in (2, 3):
            self.tap(x, 2, 3. + x / 10)
        self.board.release(1, 8, 4.)
        self.assertTrue(soon(lambda: len(self.called()) == 2))
        first, second = (what['rgb_color'] for _, _, what in self.called())
        self.assertNotEqual(first, second)
        self.assertEqual(self.hass.states['light.strip']['state'], 'on')  # (held, not tapped: not switched off)
        self.board.press(2, 8, 127, 5.)  # (a lamp without colours: its brightness only)
        self.assertEqual(level(self.board.frame(6.)[22]), 0)

    def test_a_level_is_set_in_steps_while_held_from_its_lowest_to_its_highest(self):
        self.place(heat={'thing': 'number.heat', 'hold': 'controls'}, speakers={'thing': 'media_player.speakers', 'hold': 'controls'})
        self.tap(1, 8, 2.)  # (a number has nothing to switch)
        for column, step in ((1, 8), (1, 1), (2, 4)):
            self.board.press(column, 8, 127, 3.)
            self.tap(step, 1, 4.)
            self.board.release(column, 8, 5.)
        self.assertTrue(soon(lambda: len(self.called()) == 3))
        self.assertEqual(self.called(), [('number', 'set_value', {'value': 30.}), ('number', 'set_value', {'value': 10.}),
                                         ('media_player', 'volume_set', {'volume_level': .42})])  # (the fourth of eight steps, in whole percent: never above)
        self.tap(2, 8, 6.)  # (a player is still switched by a tap)
        self.assertTrue(soon(lambda: self.called()[-1] == ('media_player', 'toggle', {})))

    def test_heating_held_has_its_temperature_in_steps_and_its_modes(self):
        self.place(heating={'thing': 'climate.room', 'hold': 'controls'})
        self.board.press(1, 8, 127, 2.)
        self.board.frame(3.)
        self.assertEqual((self.labels()['1,1'], self.labels()['8,1'], self.labels()['3,2']), ('16°', '30°', 'auto'))
        self.tap(8, 1, 3.1)
        self.tap(3, 2, 3.3)
        self.board.release(1, 8, 4.)
        self.assertTrue(soon(lambda: len(self.called()) == 2))
        self.assertEqual(self.called(), [('climate', 'set_temperature', {'temperature': 30.}), ('climate', 'set_hvac_mode', {'hvac_mode': 'auto'})])

    def test_a_select_held_shows_its_options(self):
        self.place(mood={'thing': 'select.mood', 'tap': 'none', 'hold': 'controls'})
        self.tap(1, 8, 2.)
        self.board.press(1, 8, 127, 3.)
        calm, party = (level(self.board.frame(4.)[10 + x]) for x in (1, 2))
        self.assertGreater(calm, party)  # (the one chosen is the lit one)
        self.tap(2, 1, 4.1)
        def party_selected():
            frame = self.board.frame(4.3)
            return level(frame[12]) > level(frame[11])
        self.assertTrue(soon(party_selected))
        self.board.release(1, 8, 5.)
        self.assertTrue(soon(lambda: self.hass.states['select.mood']['state'] == 'Party'))
        self.assertEqual(self.called(), [('select', 'select_option', {'option': 'Party'})])
        self.assertEqual(level(self.board.frame(6.)[12]), 0)

    def test_a_select_with_many_options_has_a_pad_for_each_wherever_it_is_held(self):
        self.hass.states['select.many'] = {'state': '0', 'attributes': {'options': [str(k) for k in range(40)]}}
        self.lay({'1,4': {'thing': 'select.many', 'hold': 'controls'}})
        self.board.press(1, 4, 127, 2.)
        self.assertEqual(sum(level(colour) > 0 for colour in pads(self.board, 3.)), 41)  # (the options, and the pad held)

    def test_a_hold_can_show_the_controls_of_another_thing_than_the_one_the_tap_switches(self):
        self.place(lamp={'thing': 'light.desk', 'hold': 'controls', 'of': 'select.mood'})
        self.tap(1, 8, 2.)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['state'] == 'on'))
        self.board.press(1, 8, 127, 3.)
        self.board.frame(4.)
        self.assertEqual(self.labels(), {'1,8': 'Desk lamp', '1,1': 'Calm', '2,1': 'Party'})
        self.tap(2, 1, 4.1)
        self.board.release(1, 8, 5.)
        self.assertTrue(soon(lambda: self.hass.states['select.mood']['state'] == 'Party'))
        self.assertEqual(self.hass.states['light.desk']['state'], 'on')  # (held, not tapped: not switched again)

    def test_a_hold_can_do_something_with_another_thing_than_the_one_the_tap_switches(self):
        self.place(lamp={'thing': 'light.desk', 'hold': 'off', 'of': 'light.strip'})
        self.tap(1, 8, 2.)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['state'] == 'on'))
        self.board.press(1, 8, 127, 3.)
        self.board.frame(4.)
        self.assertTrue(soon(lambda: self.hass.states['light.strip']['state'] == 'off'))  # (while the pad is still down)
        self.board.release(1, 8, 5.)
        asked = self.hass.asked
        self.assertTrue(soon(lambda: self.hass.asked >= asked + 2))
        self.assertEqual(self.called(), [('light', 'toggle', {}), ('light', 'turn_off', {})])  # (once, and the lamp is left alone)

    def test_nothing_shows_on_a_hold_unless_the_owner_chose_it_and_the_tap_acts_at_the_press(self):
        self.place(lamp='light.desk')
        self.board.press(1, 8, 127, 2.)
        self.assertEqual(level(self.board.frame(3.)[18]), 0)  # (no controls come up)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['state'] == 'on'))  # (and the tap acts at the press)

    # ---- a pad's own subpage
    def test_a_pads_own_subpage_shows_while_it_is_held_and_a_tap_still_does_its_thing(self):
        self.place(fan={'thing': 'switch.fan', 'hold': 'page', 'sub': {'5,5': {'thing': 'scene.movie'}, '1,8': {'thing': 'light.desk'}}})
        self.tap(1, 8, 2.)
        self.assertTrue(soon(lambda: self.hass.states['switch.fan']['state'] == 'off'))
        self.board.press(1, 8, 127, 3.)
        self.assertEqual(level(self.board.frame(3.1)[55]), 0)
        self.board.frame(4.)
        self.assertEqual(self.labels(), {'1,8': 'Fan', '5,5': 'Movie'})  # (held: its subpage, and the pad held stays what it is)
        self.tap(5, 5, 4.1)
        self.assertTrue(soon(lambda: ('scene', 'turn_on', {}) in self.called()))
        self.board.release(1, 8, 5.)
        self.assertEqual(level(self.board.frame(5.1)[55]), 0)
        self.assertEqual(self.hass.states['switch.fan']['state'], 'off')  # (held, not tapped: not switched again)

    def test_a_subpage_opened_with_a_tap_stays_until_its_pad_is_pressed_again(self):
        self.place(fan={'thing': 'switch.fan', 'tap': 'page', 'sub': {'5,5': {'thing': 'scene.movie'}}}, desk='light.desk')
        self.tap(1, 8, 2.)
        self.board.frame(3.)
        self.assertEqual(self.labels(), {'1,8': 'Fan', '5,5': 'Movie'})  # (let go long since: it stays)
        self.tap(5, 5, 3.)
        self.assertTrue(soon(lambda: ('scene', 'turn_on', {}) in self.called()))
        self.tap(1, 8, 4.)
        self.board.frame(5.)
        self.assertEqual(self.labels(), {'1,8': 'Fan', '2,8': 'Desk lamp'})
        self.assertEqual(self.hass.states['switch.fan']['state'], 'on')  # (its taps open and close the subpage: nothing switched)

    def test_a_subpage_shown_on_a_hold_can_stay_when_the_pad_is_let_go(self):
        self.place(fan={'thing': 'switch.fan', 'hold': 'page', 'stays': True, 'sub': {'5,5': {'thing': 'scene.movie'}}})
        self.board.press(1, 8, 127, 2.)
        self.board.frame(3.)
        self.board.release(1, 8, 3.1)
        self.board.frame(4.)
        self.assertEqual(self.labels(), {'1,8': 'Fan', '5,5': 'Movie'})
        self.tap(1, 8, 5.)
        self.board.frame(6.)
        self.assertEqual(self.labels(), {'1,8': 'Fan'})
        self.assertEqual(self.hass.states['switch.fan']['state'], 'on')  # (held to open, pressed to close: never switched)

    def test_a_pad_needs_no_device_to_open_a_subpage_and_a_hold_alone_is_enough_for_one(self):
        self.place(scenes={'tap': 'page', 'name': 'Scenes', 'sub': {'5,5': {'thing': 'scene.movie'}}},
                   strip={'thing': 'light.strip', 'tap': 'none', 'hold': 'off'}, nothing={'name': 'Nothing'})
        self.board.frame(1.)
        self.assertEqual(self.labels(), {'1,8': 'Scenes', '2,8': 'Strip'})  # (a pad with no device and no subpage is no pad)
        self.tap(1, 8, 2.)
        self.board.frame(3.)
        self.assertEqual(self.labels(), {'1,8': 'Scenes', '5,5': 'Movie'})
        self.tap(1, 8, 4.)
        self.tap(2, 8, 5.)  # (its tap does nothing)
        self.board.press(2, 8, 127, 6.)
        self.board.frame(7.)
        self.assertTrue(soon(lambda: self.hass.states['light.strip']['state'] == 'off'))
        self.assertEqual(self.called(), [('light', 'turn_off', {})])

    def test_a_pad_whose_settings_no_longer_fit_does_nothing_rather_than_something_else(self):
        self.place(fan={'thing': 'switch.fan', 'tap': 'unlock'}, lamp={'thing': 'light.desk', 'tap': 'none', 'of': 'sensor.gone', 'hold': 'off'},
                   odd={'thing': 'light.strip', 'tap': [], 'hold': {}})
        for column in (1, 2, 3):
            self.tap(column, 8, 2. + column)
            self.board.press(column, 8, 127, 6. + column)
            self.board.frame(7. + column)
            self.board.release(column, 8, 7.5 + column)
        asked = self.hass.asked
        self.assertTrue(soon(lambda: self.hass.asked >= asked + 6))
        self.assertEqual(self.hass.calls, [])
        self.assertIsNone(self.board.state()['plugins']['home_assistant']['error'])

    def test_a_pad_pressed_on_a_subpage_that_stays_keeps_its_press_when_the_opener_is_let_go(self):
        self.place(fan={'thing': 'switch.fan', 'hold': 'page', 'stays': True, 'sub': {'4,4': {'thing': 'light.desk', 'hold': 'controls'}}})
        self.board.press(1, 8, 127, 2.)
        self.board.frame(3.)
        self.board.press(4, 4, 127, 3.)
        self.board.release(1, 8, 3.05)
        self.board.release(4, 4, 3.1)  # (a tap on it)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['state'] == 'on'))

    def test_a_pad_of_a_subpage_can_be_held_for_its_controls_but_has_no_subpage_of_its_own(self):
        self.place(fan={'thing': 'switch.fan', 'hold': 'page', 'sub': {
            '4,4': {'thing': 'light.desk', 'hold': 'controls'}, '5,4': {'thing': 'light.strip', 'hold': 'page', 'sub': {'1,1': {'thing': 'scene.movie'}}}}})
        self.board.press(1, 8, 127, 2.)
        self.board.press(4, 4, 127, 3.)
        self.board.frame(4.)
        self.assertEqual(self.labels()['8,1'], '100 %')
        self.tap(8, 1, 4.1)
        self.board.release(4, 4, 4.5)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['attributes'].get('brightness') == 255))
        self.tap(5, 4, 5.)  # (its subpage is not opened from here: a tap, and it is switched)
        self.board.frame(6.)
        self.assertEqual(self.labels(), {'1,8': 'Fan', '4,4': 'Desk lamp', '5,4': 'Strip'})
        self.board.release(1, 8, 7.)
        self.assertTrue(soon(lambda: self.hass.states['light.strip']['state'] == 'off'))

    # ---- the rest
    def test_the_page_learns_what_each_pad_is_just_now_also_while_one_is_held(self):
        self.place(desk={'thing': 'light.desk', 'hold': 'controls'}, mood={'thing': 'select.mood', 'tap': 'none', 'hold': 'controls'})
        self.board.frame(2.)
        self.assertEqual(self.labels(), {'1,8': 'Desk lamp', '2,8': 'Mood'})
        seen = self.board.state()['revision']
        self.board.press(2, 8, 127, 3.)
        self.board.frame(4.)  # (held: its options are on the pads, and nothing else but the pad held)
        self.assertEqual(self.labels(), {'2,8': 'Mood', '1,1': 'Calm', '2,1': 'Party'})
        self.assertGreater(self.board.state()['revision'], seen)  # (the page hears that there is something new to fetch)
        self.board.release(2, 8, 5.)
        self.board.press(1, 8, 127, 6.)
        self.board.frame(7.)
        self.assertEqual(self.labels()['8,1'], '100 %')

    def test_one_round_button_puts_out_the_lights_on_the_page(self):
        self.tap(1, 8, 2.)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['state'] == 'on'))
        self.board.press(8, 0, 127, 3.)
        self.assertTrue(soon(lambda: self.hass.states['light.desk']['state'] == 'off'))
        self.assertEqual(self.hass.states['switch.fan']['state'], 'on')  # (not a light: left alone)
        self.place(fan={'thing': 'switch.fan', 'of': 'light.strip', 'hold': 'on'})  # (a light that only a hold switches is one too)
        self.board.press(8, 0, 127, 4.)
        self.assertTrue(soon(lambda: self.hass.states['light.strip']['state'] == 'off'))

    def test_a_refused_token_and_a_home_assistant_that_is_away_are_said_and_it_comes_back(self):
        self.hass.pending = threading.Event()
        self.addCleanup(self.hass.pending.set)
        self.set(token='wrong')
        self.assertEqual(self.status(), 'Checking connection…')
        self.hass.pending.set()
        self.assertTrue(soon(lambda: 'refused the access token' in self.status()), self.status())
        self.tap(1, 8, 2.)  # (nothing to switch: nothing asked)
        self.set(token='good')
        self.assertTrue(soon(lambda: self.status() == 'Connected.'))
        settings = self.board.config
        settings['plugins']['home_assistant']['settings']['address'] = 'http://127.0.0.1:9'
        self.board.configure(settings)
        self.assertTrue(soon(lambda: 'No answer' in self.status()), self.status())
        self.assertEqual(self.hass.calls, [])
        self.assertIsNone(self.board.state()['plugins']['home_assistant']['error'])

    def test_an_empty_bank_still_checks_that_home_assistant_answers(self):
        self.lay({})
        self.hass.api_reply = {'ok': True}
        self.assertTrue(soon(lambda: 'Home Assistant API' in self.status()), self.status())

    def test_the_user_can_distinguish_a_forbidden_token_from_an_unreachable_server(self):
        self.set(token='restricted')
        self.assertTrue(soon(lambda: 'permission' in self.status()), self.status())

    def test_the_page_is_told_what_is_missing_until_home_assistant_answers(self):
        needs = lambda: self.board.state()['plugins']['home_assistant']['state']['setup']
        self.assertIsNone(needs())
        self.set(token='')
        self.assertIn('access token', needs())  # (said at once, not after the next look)
        self.set(token='wrong')
        self.assertTrue(soon(lambda: 'refused' in (needs() or '')), needs())
        self.set(token='good')
        self.assertTrue(soon(lambda: needs() is None))

    def test_dark_pads_suspend_entity_polling_and_refresh_on_wake(self):
        self.board.set_power(False)
        self.set(token='wrong')
        self.assertTrue(soon(lambda: 'refused' in self.status()))
        seen = self.hass.seen.copy()
        self.set(token='good')
        self.assertTrue(soon(lambda: self.status() == 'Connected.'))
        self.assertEqual(self.hass.seen, seen)
        self.board.set_power(True)
        self.assertTrue(soon(lambda: self.hass.seen['light.desk'] > seen['light.desk']))

    def test_switched_off_it_stops_asking_home_assistant(self):
        settings = self.board.config
        settings['plugins']['home_assistant']['enabled'] = False
        self.board.configure(settings)
        time.sleep(.2)
        asked = self.hass.asked
        time.sleep(.3)
        self.assertEqual(self.hass.asked, asked)
