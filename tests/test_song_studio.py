import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from padcade.song_studio import Studio
from tests.kit import Sound


class Songs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.heard, self.clicks = [], []
        self.play = lambda clip, gain: self.heard.append(clip)
        self.studio = Studio(self.path, self.play, self.heard.clear, self.clicks.append)
        self.studio.edit('bpm', 120)
        self.addCleanup(self.studio.flush)

    def record(self):
        s = self.studio
        s.action('record_track:0', 0)
        s.hit('Before count-in', 1)
        s.hit('Kick', 2)
        s.hit('Bass C2', 3)
        s.tick(6)

    def test_two_banks_record_to_one_track_and_loop_from_the_first_downbeat(self):
        self.record()
        self.assertEqual(self.heard[-1], 'Kick')
        self.studio.tick(7)
        self.assertEqual(self.heard[-1], 'Bass C2')
        self.studio.flush()  # A normal shutdown waits for the last recorded layer.
        other = Studio(self.path, self.play, self.heard.clear, self.clicks.append)
        self.assertEqual([e['clip'] for e in other.song['tracks'][0]['events']], ['Kick', 'Bass C2'])
        self.assertEqual(other.state()['recording'], 'off')

    def test_recorded_offsets_repeat_once_per_lap_when_the_clock_runs_to_each_note(self):
        s = self.studio
        s.edit('bpm', 123)
        s.action('record', 0.)
        s.hit('A', 2.04)
        s.hit('B', 2.67)
        s.tick(7.)
        self.assertTrue(s.edit('timing', 'As played', track=0))
        s.action('play', 7.1)
        s.action('play', 10.)
        self.heard.clear()
        for _ in range(6):
            s.tick(s.next_deadline())
        self.assertEqual(self.heard, ['A', 'B'] * 3)

    def test_loop_layer_remains_playable_and_retries_a_failed_save_once(self):
        s = self.studio
        destination = self.path / 'songs.json'
        destination.unlink()
        destination.mkdir()  # No file can be published here until storage is repaired.
        s.action('record', 0)
        s.hit('Kick', 2)
        s.tick(6)
        for _ in range(100):
            s.tick(6)
            if s.state().get('storage_notice'):
                break
            time.sleep(.01)
        self.assertTrue(s.state().get('storage_notice'))
        self.assertEqual(s.state()['recording'], 'off')
        self.assertEqual(self.heard, ['Kick'])
        destination.rmdir()
        for elapsed in range(1, 101):
            s.tick(6 + elapsed)
            if destination.is_file() and not s.state().get('storage_notice'):
                break
            time.sleep(.01)
        self.assertFalse(s.state().get('storage_notice'))
        reopened = Studio(self.path, self.play, self.heard.clear, self.clicks.append)
        self.assertEqual(reopened.state()['tracks'][0]['hits'], 1)

    def test_muting_while_playing_returns_before_storage_sync_and_survives_reopening(self):
        self.record()
        s = self.studio
        s.flush()
        release_disk = threading.Event()
        with patch('os.fsync', side_effect=lambda fd: release_disk.wait(1)):
            try:
                s.action('tap_track:0', 7)
                self.assertTrue(s.state()['saving'])
                self.assertTrue(s.state()['tracks'][0]['muted'])
            finally:
                release_disk.set()
                s.flush()
        reopened = Studio(self.path, self.play, self.heard.clear, self.clicks.append)
        self.assertTrue(reopened.state()['tracks'][0]['muted'])

    def test_the_loop_downbeat_sounds_while_the_completed_layer_is_still_syncing(self):
        heard, release_disk = threading.Event(), threading.Event()
        s = Studio(self.path, lambda clip, gain: heard.set(), lambda: None, self.clicks.append)
        self.addCleanup(s.flush)
        s.action('record', 0)
        s.hit('Kick', 2)
        with patch('os.fsync', side_effect=lambda fd: release_disk.wait(3)):
            try:
                s.tick(6)
                self.assertTrue(heard.is_set())
                self.assertTrue(s.state()['saving'])
            finally:
                release_disk.set()
                s.flush()

    def test_an_overdub_during_a_save_keeps_both_committed_versions_in_order(self):
        writing, resume, published = threading.Event(), threading.Event(), threading.Event()

        def slow_disk():
            writing.set()
            self.assertTrue(resume.wait(3))

        s = Studio(self.path, self.play, self.heard.clear, self.clicks.append, prepare_save=slow_disk)
        s.action('record', 0.)
        s.hit('Kick', 2.)
        original = Path.replace

        def publish(source, target):
            result = original(source, target)
            published.set()
            return result

        def saved_notes():
            book = json.loads((self.path / 'songs.json').read_text())
            return [hit['clip'] for hit in book['slots'][0]['tracks'][0]['events']]

        with patch.object(Path, 'replace', publish):
            try:
                s.tick(6.)
                self.assertTrue(writing.wait(3))
                s.action('record_track:0', 6.1)
                s.hit('Snare', 10.1)
                s.tick(14.)
                resume.set()
                self.assertTrue(published.wait(3))
                self.assertEqual(saved_notes(), ['Kick'])
            finally:
                resume.set()
                s.flush()
        self.assertEqual(saved_notes(), ['Kick', 'Snare'])

    def test_a_quick_gated_tap_between_frames_sounds_on_replay(self):
        sound = Sound()
        s = Studio(self.path, sound.play, sound.heard.clear, self.clicks.append)
        self.addCleanup(s.flush)
        s.action('record', 0)
        s.hit('Warm pad C4', 2.5, 'pad')
        s.release_hit('pad', 2.51)
        s.tick(6)
        s.tick(6.53)
        self.assertEqual([v.clip for v in sound.heard], ['Warm pad C4'])
        self.assertIsNone(sound.heard[0].release_seconds)
        s.tick(6.55)
        self.assertGreater(sound.heard[0].release_seconds, 0)

    def test_a_hit_just_ahead_of_the_first_downbeat_is_captured_on_that_beat(self):
        s = self.studio
        s.action('record', 0)
        s.hit('Count-in practice', 1.5)
        s.hit('Kick', 1.98)
        s.tick(6)
        self.assertEqual(self.heard, ['Kick'])
        self.assertEqual(s.song['tracks'][0]['events'][0]['beat'], 0.)

    def test_a_broken_song_book_is_preserved_and_the_studio_opens_with_a_fresh_song(self):
        song_file = self.path / 'songs.json'
        valid = json.loads(song_file.read_bytes())
        invalid_track = json.loads(song_file.read_bytes())
        invalid_track['slots'][0]['tracks'][1]['events'].append({'beat': [], 'clip': 'Kick', 'take': 1})
        for bad in ({'slots': [], 'slot': 0}, invalid_track, dict(valid, undo={'kind':'song', 'slot':18})):
            with self.subTest(bad=bad):
                raw = json.dumps(bad).encode()
                song_file.write_bytes(raw)
                s = Studio(self.path, self.play, self.heard.clear, self.clicks.append)
                self.assertTrue(s.state()['storage_notice'])
                self.assertEqual(s.state()['title'], 'Song 1')
                self.assertTrue(s.edit('title', 'Recovered'))
                self.assertTrue(any(p.read_bytes() == raw for p in self.path.glob('songs.broken*')))
                self.assertEqual(Studio(self.path, self.play, self.heard.clear, self.clicks.append).state()['title'], 'Recovered')

    def test_track_strip_shows_live_hits_at_their_playback_positions(self):
        s = self.studio
        s.action('record_track:2', 0)
        s.hit('Snare', 2.2)     # Beat 0.4
        s.hit('Clap', 5.96)     # Beat 7.92, just before the loop wraps
        tracks = s.state()['tracks']
        self.assertEqual(tracks[2]['steps'], 1 | 1 << 15)
        self.assertEqual(tracks[0]['steps'], 0)
        s.tick(6)
        s.edit('timing', 'Tight', track=2)
        self.assertEqual(s.state()['tracks'][2]['steps'], 1 | 1 << 1)
        s.action('undo_track:2', 6.1)
        self.assertEqual(s.state()['tracks'][2]['steps'], 0)

    def test_enabled_click_keeps_time_before_and_after_playback(self):
        s = self.studio
        s.action('click', 1)
        s.tick(1)
        s.tick(1.25)
        self.assertEqual(self.clicks, [False])
        s.edit('bpm', 60)  # Half a beat remains at the new tempo.
        s.tick(1.75)
        self.assertEqual(self.clicks, [False, False])
        s.action('play', 1.8)
        s.tick(2.5)
        self.assertEqual(self.clicks, [False, False])
        s.tick(2.75)
        s.action('play', 3)
        s.tick(3.75)
        self.assertEqual(self.clicks, [False] * 4)
        self.assertFalse(s.state()['playing'])
        s.action('click', 4)
        s.tick(5)
        self.assertEqual(self.clicks, [False] * 4)

    def test_record_adopts_the_next_idle_beat_for_its_four_counts(self):
        s = self.studio
        s.action('click', 1)
        s.tick(1)
        s.action('record', 1.1)
        s.tick(1.25)
        self.assertEqual(self.clicks, [False])
        for n in range(1, 6):
            s.tick(1 + n / 2)
        self.assertEqual(self.clicks, [False, True, True, True, True, False])
        self.assertEqual(s.state()['recording'], 'recording')

    def test_click_follows_the_audition_and_can_be_switched_off_in_its_menu(self):
        self.record()
        s = self.studio
        s.action('play', 6.1)
        s.action('click', 7)
        s.action('delete', 7)
        s.action('track:0', 8)
        self.clicks.clear()
        s.tick(8.5)
        self.assertEqual(self.clicks, [False])
        self.assertTrue(s.action('click', 8.7))
        s.tick(9)
        self.assertEqual(self.clicks, [False])

    def test_count_in_replaces_the_normal_click_then_hands_back_on_the_downbeat(self):
        s = self.studio
        start = 1234.56789
        s.edit('bpm', 88)
        s.action('click', start)
        s.action('record', start)
        self.assertEqual(s.state()['count_in'], 4)
        for beat in range(1, 6):
            s.tick(start + beat * 60 / 88)
        self.assertEqual(self.clicks, [True, True, True, True, False, False])
        self.assertFalse(self.heard)

    def test_live_tempo_changes_keep_the_beat_and_queued_song_on_the_loop_boundary(self):
        self.record()  # Starts playing its second lap at 6 seconds, 120 BPM.
        s = self.studio
        s.action('songs', 6.2)
        s.action('duplicate', 6.2)
        s.action('slot:0', 6.2)
        s.action('slot:3', 6.2)
        s.tick(6.25)  # Half a beat into this lap.
        self.assertTrue(s.edit('bpm', 60))
        self.assertAlmostEqual(s.state()['beat'], .5)
        s.tick(6.75)
        self.assertAlmostEqual(s.state()['beat'], 1.)
        self.assertTrue(s.edit('bpm', 150))
        s.tick(7.15)
        self.assertEqual(self.heard[-1], 'Bass C2')
        s.tick(9.54)
        self.assertEqual(s.state()['slot'], 0)
        s.tick(9.55)
        self.assertEqual(s.state()['slot'], 3)
        self.assertEqual(self.heard[-1], 'Kick')

    def test_a_piano_note_keeps_its_recorded_beat_length_when_tempo_changes_mid_note(self):
        sound = Sound()
        s = Studio(self.path, sound.play, sound.heard.clear, self.clicks.append)
        self.addCleanup(s.flush)
        s.action('record', 0)
        s.hit('Piano C4', 2., 'C4')
        s.release_hit('C4', 3.)
        s.tick(6.)  # The two-beat note starts replaying at 120 BPM.
        voice = sound.heard[-1]
        s.tick(6.25)  # Half a beat has elapsed, leaving 1.5 beats at the new tempo.
        self.assertTrue(s.edit('bpm', 60))
        s.tick(7.)
        self.assertIsNone(voice.release_seconds)
        s.tick(7.75)
        self.assertGreater(voice.release_seconds, 0)

    def test_running_loop_waits_then_counts_four_beats_before_recording(self):
        s = self.studio
        s.action('play', 0)
        s.action('record', .3)
        self.assertEqual(s.state()['recording'], 'waiting')
        self.assertIsNone(s.state()['count_in'])
        for when, count in [(2, 4), (2.5, 3), (3, 2), (3.5, 1)]:
            s.tick(when)
            self.assertEqual((s.state()['recording'], s.state()['count_in']), ('count-in', count))
        s.tick(4)
        self.assertEqual((s.state()['recording'], s.state()['count_in']), ('recording', None))

    def test_arming_on_the_first_count_beat_sounds_it_once(self):
        s = self.studio
        s.action('click', 0)
        s.action('play', 0)
        s.action('record', 2)
        s.tick(2)
        self.assertEqual(self.clicks, [False, True])
        self.assertEqual(s.state()['count_in'], 4)

    def test_record_on_a_queued_song_change_arms_the_incoming_song(self):
        s = self.studio
        s.action('songs', 0)
        s.action('slot:1', 0)
        s.action('songs', 0)
        s.action('slot:0', 0)
        s.action('play', 0)
        s.action('songs', .3)
        s.action('slot:1', .3)
        s.action('songs', .4)
        s.action('record', 4)
        self.assertEqual((s.state()['slot'], s.state()['recording']), (1, 'waiting'))

    def test_a_muted_track_is_heard_only_while_a_layer_is_recorded_on_it(self):
        self.record()
        s = self.studio
        s.action('mute:0', 6.1)
        count = len(self.heard)
        s.tick(7)
        self.assertEqual(len(self.heard), count)  # (silent, whatever was touched last)
        s.action('record_track:0', 7.1)
        s.tick(10)
        self.assertEqual(self.heard[-1], 'Kick')  # (what is already on it, to play along with)
        s.tick(14.1)  # (the layer is over)
        count = len(self.heard)
        s.tick(18)
        self.assertEqual(len(self.heard), count)
        self.assertTrue(s.state()['tracks'][0]['muted'])

    def test_every_action_names_its_track_and_nothing_acts_on_one_touched_before(self):
        self.record()
        s = self.studio
        s.action('play', 6.5)
        self.assertFalse(s.action('tap_track:3', 7))  # (a tap on an empty track: nothing)
        self.assertFalse(s.action('track:0', 7))      # (there is no selecting)
        self.assertFalse(s.action('quantise', 7))     # (Quantise alone: nothing)
        self.assertFalse(s.action('undo', 7))         # (Undo alone only brings back what was deleted)
        track = s.state()['tracks'][0]
        self.assertEqual((track['timing'], track['layers'], track['muted']), ('As played', 1, False))
        self.assertNotIn('selected', s.state())
        self.assertTrue(s.action('tap_track:0', 7))
        self.assertTrue(s.state()['tracks'][0]['muted'])
        self.assertTrue(s.action('undo_track:0', 7))
        self.assertEqual(s.state()['tracks'][0]['layers'], 0)

    def test_ending_a_layer_at_the_moment_it_ends_by_itself_is_taken_calmly(self):
        s = self.studio
        s.action('record_track:0', 0)
        s.hit('Kick', 2)
        self.assertTrue(s.action('tap_track:0', 6))  # (eight beats after the count-in: the layer is just over)
        state = s.state()
        self.assertEqual((state['recording'], state['recording_track'], state['tracks'][0]['hits']), ('off', None, 1))

    def test_record_alone_at_a_song_change_means_the_first_empty_track_of_the_song_coming_in(self):
        self.record()
        s = self.studio
        s.action('play', 6.5)
        s.duplicate_song()
        s.action('songs', 7)
        s.action('slot:1', 7)
        s.move_track(0, 1)  # (the copy has its part on the second track, and the first free)
        s.action('songs', 7.1)
        s.action('slot:0', 7.1)
        s.action('play', 8)
        s.action('songs', 9)
        s.action('slot:1', 9)  # (queued for the end of this loop, at 12 seconds)
        s.action('songs', 9.1)
        self.assertTrue(s.action('record', 12))
        self.assertEqual((s.state()['slot'], s.state()['recording_track']), (1, 0))

    def test_record_alone_starts_a_new_part_on_the_first_empty_track(self):
        self.record()
        s = self.studio
        s.action('play', 6.5)
        self.assertTrue(s.action('record', 7))
        self.assertEqual((s.state()['recording'], s.state()['recording_track']), ('count-in', 1))
        s.hit('Clap', 9.2)
        self.assertTrue(s.action('record', 9.5))  # (the same again ends the layer)
        self.assertEqual(([t['hits'] for t in s.state()['tracks'][:3]], s.state()['recording_track']), ([2, 1, 0], None))

    def test_a_muted_first_part_stays_silent_after_a_restart(self):
        self.record()
        self.studio.action('mute:0', 6.1)
        self.studio.flush()
        again = Studio(self.path, self.play, self.heard.clear, self.clicks.append)
        self.addCleanup(again.flush)
        self.heard.clear()
        again.action('play', 7.)
        again.tick(12.)
        self.assertEqual(self.heard, [])

    def test_full_song_switches_on_its_loop_and_an_empty_slot_switches_immediately(self):
        self.record()
        s = self.studio
        s.action('songs', 6.2)
        s.action('duplicate', 6.2)
        s.action('slot:0', 6.2)
        s.action('slot:3', 6.2)
        self.assertEqual(s.state()['queued'], 3)
        s.tick(10)
        self.assertEqual(s.state()['slot'], 3)
        self.assertEqual(self.heard[-1], 'Kick')
        s.action('songs', 10.2)
        s.action('slot:0', 10.2)
        s.action('slot:7', 10.2)
        self.assertEqual(s.state()['slot'], 7)
        self.assertTrue(s.state()['playing'])
        s.tick(14.2)
        self.assertEqual(s.state()['slot'], 7)
        self.assertFalse(self.heard)

    def test_delete_previews_a_muted_track_and_undo_restores_its_recording_and_mute(self):
        self.record()
        s = self.studio
        s.action('play', 6.2)
        s.action('mute:0', 6.3)
        s.action('delete', 6.4)
        s.action('track:0', 6.5)
        self.assertEqual(self.heard, ['Kick'])
        s.tick(7.5)
        self.assertEqual(self.heard, ['Kick', 'Bass C2'])
        s.tick(10.5)
        self.assertFalse(self.heard)
        s.action('delete', 10.6)
        self.assertEqual(s.state()['tracks'][0]['hits'], 0)
        s.action('undo', 10.7)
        self.assertTrue(s.state()['tracks'][0]['muted'])
        self.assertEqual(s.state()['tracks'][0]['hits'], 2)

    def test_deleted_last_song_can_be_restored_in_its_slot_after_restart(self):
        self.record()
        s = self.studio
        s.action('songs', 7)
        s.action('delete', 7)
        s.action('slot:0', 7)
        s.action('delete', 7.2)
        s.flush()
        again = Studio(self.path, self.play, self.heard.clear, self.clicks.append)
        self.assertEqual(again.state()['slots'], [None] * 16)
        again.action('undo', 8)
        self.assertEqual(again.state()['slot'], 0)
        self.assertEqual(again.state()['tracks'][0]['hits'], 2)

    def test_undo_an_overdub_keeps_the_first_take(self):
        self.record()
        s = self.studio
        s.action('record_track:0', 6.1)
        s.hit('Too early', 7)
        s.hit('Clap', 10.1)
        s.action('record', 10.5)
        self.assertEqual(s.state()['tracks'][0]['layers'], 2)
        s.action('undo_track:0', 11)
        self.assertEqual([e['clip'] for e in s.song['tracks'][0]['events']], ['Kick', 'Bass C2'])

    def test_retiming_a_muted_track_keeps_it_muted_and_the_performance_recording(self):
        self.record()
        s = self.studio
        s.action('mute:0', 6.1)
        s.action('performance', 6.3)
        self.assertTrue(s.action('quantise_track:0', 7))
        self.assertEqual(s.state()['tracks'][0]['timing'], 'Gentle')
        self.assertTrue(s.state()['tracks'][0]['muted'])
        self.assertTrue(s.state()['performance']['recording'])

    def test_undoing_a_specific_layer_leaves_a_deleted_track_available_to_restore(self):
        self.record()
        s = self.studio
        s.action('record_track:1', 6.1)
        s.hit('Hat', 10.2)
        s.action('record', 10.5)
        s.action('delete', 11)
        s.action('track:1', 11.1)
        s.action('delete', 11.2)
        self.assertTrue(s.action('undo_track:0', 12))
        self.assertEqual([t['hits'] for t in s.state()['tracks'][:2]], [0, 0])
        self.assertTrue(s.action('undo', 12.1))
        self.assertEqual(s.state()['tracks'][1]['hits'], 1)

    def test_a_track_moved_to_another_place_takes_what_is_its_own_along(self):
        self.record()
        s = self.studio
        s.edit('track_name', 'Drums', track=0)
        self.assertFalse(s.move_track(0, 0))  # (nowhere)
        self.assertFalse(s.move_track(0, 8))  # (there is no ninth place)
        self.assertTrue(s.move_track(0, 2))
        tracks = s.state()['tracks']
        # The two it passed move up. A track that still has its number for a name takes the number of its new place.
        self.assertEqual([t['name'] for t in tracks[:4]], ['Track 1', 'Track 2', 'Drums', 'Track 4'])
        self.assertEqual([t['hits'] for t in tracks[:4]], [0, 0, 2, 0])
        self.heard.clear()
        for _ in range(2):
            s.tick(s.next_deadline())
        self.assertCountEqual(self.heard, ['Kick', 'Bass C2'])  # (it goes on playing from its new place)

    def test_a_cleared_track_comes_back_where_a_move_took_its_place(self):
        self.record()
        s = self.studio
        self.assertTrue(s.action('clear_track:0', 7))
        self.assertEqual(s.state()['tracks'][0]['hits'], 0)
        first = s.state()['restorable']
        self.assertEqual((first['kind'], first['name']), ('track', 'Track 1'))
        self.assertTrue(s.move_track(0, 1))
        self.assertTrue(s.action('restore', 7))
        self.assertEqual([(t['name'], t['hits']) for t in s.state()['tracks'][:2]], [('Track 1', 0), ('Track 2', 2)])
        self.assertIsNone(s.state()['restorable'])

    def test_the_open_song_is_copied_and_deleted_directly_and_comes_back(self):
        self.record()
        s = self.studio
        s.action('play', 6.5)
        self.assertEqual(s.duplicate_song(), 1)
        self.assertEqual(s.state()['slots'][:3], ['Song 1', 'Song 1 (copy)', None])
        self.assertTrue(s.action('delete_song', 7))
        self.assertEqual((s.state()['slot'], s.state()['slots'][0]), (None, None))
        lost = s.state()['restorable']
        self.assertEqual((lost['kind'], lost['name']), ('song', 'Song 1'))
        self.assertTrue(s.action('restore', 7))
        self.assertEqual((s.state()['slot'], s.state()['title'], s.state()['tracks'][0]['hits']), (0, 'Song 1', 2))

    def test_each_thing_cleared_is_told_apart_so_that_a_page_can_put_its_notice_away(self):
        self.record()
        s = self.studio
        s.action('play', 6.5)
        s.action('clear_track:0', 7)
        first = s.state()['restorable']['id']
        s.action('restore', 7)
        s.action('clear_track:0', 7)  # (the same track, cleared a second time: another thing to bring back)
        self.assertNotEqual(s.state()['restorable']['id'], first)

    def test_a_track_is_renamed_by_naming_it(self):
        s = self.studio
        self.assertTrue(s.edit('track_name', 'Bass', track=3))
        self.assertEqual(s.state()['tracks'][3]['name'], 'Bass')
        self.assertFalse(s.edit('track_name', 'Which one'))

    def test_a_first_recording_on_a_track_plays_as_it_was_played(self):
        self.record()
        s = self.studio
        self.assertEqual(s.state()['tracks'][0]['timing'], 'As played')
        s.edit('timing', 'Tight', track=0)
        s.action('play', 6.5)
        s.action('delete', 7)
        s.action('track:0', 7)
        s.action('delete', 7)  # (cleared: what is recorded on it next starts afresh)
        s.action('record_track:0', 8)
        s.hit('Kick', 10.1)
        s.tick(14)
        self.assertEqual(s.state()['tracks'][0]['timing'], 'As played')

    def test_quantise_moves_playback_without_changing_the_recording(self):
        s = self.studio
        s.action('record_track:3', 0)
        s.hit('Clap', 2.2)
        s.tick(6)
        for at in (6.02, 6.03, 6.05):  # (from as played to gentle, to fine, then to tight)
            s.action('quantise_track:3', at)
        self.assertEqual(s.state()['tracks'][3]['timing'], 'Tight')
        self.heard.clear()
        s.tick(6.21)
        self.assertFalse(self.heard)
        s.tick(6.25)
        self.assertEqual(self.heard, ['Clap'])
        self.assertAlmostEqual(s.song['tracks'][3]['events'][0]['beat'], .4)

    def test_fine_timing_keeps_two_quick_hits_apart_where_tight_puts_them_on_one_line(self):
        s = self.studio
        s.action('record_track:3', 0)
        s.hit('Roll', 2.52)   # (just after the second beat)
        s.hit('Roll', 2.555)  # (a thirty-second note later)
        s.tick(6)
        self.assertTrue(s.edit('timing', 'Fine', track=3))
        self.heard.clear()
        heard = []
        for at in (6.49, 6.51, 6.55, 6.57):
            s.tick(at)
            heard.append(len(self.heard))
        self.assertEqual(heard, [0, 1, 1, 2])  # (on the beat, and a thirty-second after it)
        s.edit('timing', 'Tight', track=3)
        self.heard.clear()
        s.tick(10.49)
        s.tick(10.51)
        self.assertEqual(self.heard, ['Roll', 'Roll'])  # (both on the beat)

    def test_record_starts_on_the_empty_first_track_of_each_song(self):
        s = self.studio
        s.action('record', 1)
        self.assertEqual(s.state()['recording'], 'count-in')
        s.hit('Clap', 3.1)
        self.assertEqual(s.state()['tracks'][0]['hits'], 1)
        s.action('play', 3.2)
        s.action('songs', 4.1)
        s.action('slot:1', 4.2)
        s.action('record', 4.3)
        s.hit('Snare', 6.4)
        self.assertEqual(s.state()['tracks'][0]['hits'], 1)
        self.assertEqual(s.state()['recording'], 'recording')
