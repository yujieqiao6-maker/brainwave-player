"""Hardware-free regression tests for the serial stream and shutdown lifecycle."""

from pathlib import Path
import runpy
import unittest
from unittest import mock


MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'brainwave_player_v8(1).py'))
BrainwaveThread = MODULE['BrainwaveThread']


def packet(payload):
    payload = bytes(payload)
    return b'\xaa\xaa' + bytes([len(payload)]) + payload + bytes([(~sum(payload)) & 0xFF])


class SerialStreamTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.clock = mock.patch.object(MODULE['time'], 'monotonic', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.worker = BrainwaveThread()
        self.updates = []
        self.worker.data_updated.connect(self.updates.append)

    def feed_good(self, attention=70):
        self.worker.feed_data(packet([0x02, 0, 0x04, attention]))

    def test_every_split_boundary_and_bytewise_input(self):
        source = packet([0x02, 0, 0x04, 78])
        for split in range(1, len(source)):
            with self.subTest(split=split):
                worker = BrainwaveThread()
                worker.feed_data(source[:split])
                self.assertEqual(worker.total_packets, 0)
                worker.feed_data(source[split:])
                self.assertEqual(worker.attention, 78)
                self.assertEqual(worker.total_packets, 1)
        worker = BrainwaveThread()
        for value in source:
            worker.feed_data(bytes([value]))
        self.assertEqual(worker.attention, 78)

    def test_glued_packets_and_junk_resynchronize(self):
        self.worker.feed_data(b'garbage\xaa' + packet([2, 0, 4, 45]) + packet([4, 81]))
        self.assertEqual(self.worker.total_packets, 2)
        self.assertEqual(self.worker.attention, 81)
        self.assertTrue(self.worker.device_worn)
        self.worker.flush_data(force=True)
        self.assertEqual(self.updates[-1]['attention'], 81)

    def test_checksum_failure_does_not_change_state_and_next_packet_recovers(self):
        damaged = bytearray(packet([2, 0, 4, 90]))
        damaged[-1] ^= 1
        self.worker.feed_data(damaged)
        self.assertEqual(self.worker.total_packets, 0)
        self.assertEqual(self.updates, [])
        self.worker.feed_data(packet([2, 0, 4, 63]))
        self.assertEqual(self.worker.attention, 63)
        self.assertGreater(self.worker.invalid_packets, 0)

    def test_invalid_length_and_missing_checksum_recover(self):
        self.worker.feed_data(b'\xaa\xaa\xff' + packet([2, 0, 4, 75])[:-1])
        self.assertEqual(self.worker.total_packets, 0)
        self.worker.feed_data(packet([2, 0, 4, 75])[-1:])
        self.assertEqual(self.worker.attention, 75)

    def test_raw_eeg_never_becomes_standard_attention(self):
        for value in range(50):
            self.worker.feed_data(packet([0x80, 2, 0x04, value]))
        self.assertEqual(self.worker.total_packets, 50)
        self.assertEqual(self.worker.attention_packets, 0)
        self.assertEqual(self.worker.attention, 0)
        self.assertEqual(self.updates, [])

    def test_unknown_tlv_and_extended_rows_cannot_inject_attention(self):
        self.worker.feed_data(packet([0x83, 4, 2, 0, 4, 99, 0x55, 4, 98, 5, 50]))
        self.assertEqual(self.worker.total_packets, 1)
        self.assertEqual(self.worker.meditation, 50)
        self.assertEqual(self.worker.attention_packets, 0)
        self.assertEqual(self.updates, [])

    def test_packet_fields_commit_atomically_regardless_of_order(self):
        self.worker.feed_data(packet([4, 77, 2, 0]))
        self.assertEqual(self.worker.attention, 77)
        self.assertTrue(self.worker.device_worn)
        self.worker.feed_data(packet([4, 98, 2, 200]))
        self.assertEqual(self.worker.attention, 0)
        self.assertFalse(self.worker.device_worn)
        self.assertEqual(self.updates[-1]['attention'], 0)

    def test_truncated_rows_do_not_partially_commit(self):
        for payload in ([2, 0, 4], [2, 0, 0x80], [2, 0, 0x80, 2, 1], [2, 0, 0x55]):
            with self.subTest(payload=payload):
                self.worker.feed_data(packet(payload))
                self.assertEqual(self.worker.poor_signal, 200)
                self.assertEqual(self.worker.total_packets, 0)
                self.assertEqual(self.updates, [])
        self.feed_good()
        self.assertEqual(self.worker.attention, 70)

    def test_out_of_range_attention_is_rejected(self):
        self.worker.feed_data(packet([2, 0, 4, 101]))
        self.assertEqual(self.worker.poor_signal, 200)
        self.assertEqual(self.worker.total_packets, 0)

    def test_bad_signal_and_unavailable_attention_invalidate_immediately(self):
        self.feed_good()
        self.worker.feed_data(packet([2, 200]))
        self.assertEqual(len(self.updates), 2)
        self.assertFalse(self.updates[-1]['device_worn'])
        self.assertEqual(self.updates[-1]['attention'], 0)
        self.worker.feed_data(packet([2, 0]))
        self.assertFalse(self.worker.device_worn)
        self.now += 0.1
        self.feed_good()
        self.worker.feed_data(packet([4, 0]))
        self.assertFalse(self.updates[-1]['attention_valid'])

    def test_timeout_requires_fresh_attention_and_does_not_repeat(self):
        self.feed_good()
        self.now += 2.9
        self.worker.feed_data(packet([2, 0, 5, 80, 0x80, 2, 0, 50]))
        self.worker.check_timeout()
        self.assertTrue(self.worker.device_worn)
        self.now += 0.1
        self.worker.check_timeout()
        self.assertFalse(self.worker.device_worn)
        self.assertEqual(self.updates[-1]['attention'], 0)
        count = len(self.updates)
        self.now += 10
        self.worker.check_timeout()
        self.assertEqual(len(self.updates), count)

    def test_high_rate_updates_coalesce_latest_at_twenty_hz(self):
        for index in range(1000):
            self.now = 100.0 + index / 1000
            self.feed_good(1 + index % 100)
        self.assertLessEqual(len(self.updates), 20)
        self.assertGreaterEqual(len(self.updates), 19)
        self.now = 101.1
        self.worker.flush_data()
        self.assertEqual(self.updates[-1]['attention'], 100)

    def test_raw_debug_history_is_bounded_and_snapshot_is_independent(self):
        self.worker.set_debug_mode(True)
        messages = []
        self.worker.debug_message.connect(messages.append)
        raw = packet([0x80, 2, 0, 50])
        for _ in range(self.worker.MAX_RAW_PACKETS + 100):
            self.worker.feed_data(raw)
        snapshot = self.worker.raw_packets_snapshot()
        self.assertEqual(len(snapshot), self.worker.MAX_RAW_PACKETS)
        self.assertLessEqual(len(messages), 2)  # one rate-limited log plus one warning
        self.worker.clear_raw_packets()
        self.assertEqual(len(snapshot), self.worker.MAX_RAW_PACKETS)
        self.assertEqual(self.worker.raw_packets_snapshot(), [])
        self.worker.set_debug_mode(False)
        self.worker.feed_data(raw)
        self.assertEqual(self.worker.raw_packets_snapshot(), [])

    def test_legacy_raw_mapping_requires_explicit_opt_in(self):
        legacy = BrainwaveThread(legacy_raw_mapping=True)
        for attention in (10, 80, 20, 90, 30, 70, 40, 60, 50, 99):
            raw = int(attention * 65535 / 100)
            legacy.feed_data(packet([0x80, 2, raw >> 8, raw & 0xFF]))
        self.assertTrue(legacy.device_worn)
        self.assertGreater(legacy.attention, 90)
        self.assertEqual(legacy.sichiray_packets, 10)
        self.assertTrue(legacy.legacy_mapped)
        self.assertEqual(legacy.poor_signal, 200)  # mapping cannot establish signal quality

    def test_standard_attention_takes_priority_over_mixed_raw_stream(self):
        legacy = BrainwaveThread(legacy_raw_mapping=True)
        legacy.feed_data(packet([2, 0, 4, 88]))
        for attention in (10, 80, 20, 90, 30, 70, 40, 60, 50, 15):
            raw = int(attention * 65535 / 100)
            legacy.feed_data(packet([0x80, 2, raw >> 8, raw & 0xFF]))
        self.assertEqual(legacy.attention, 88)
        self.assertEqual(legacy.sichiray_packets, 0)
        self.assertFalse(legacy.legacy_mapped)
        self.now += 3.1
        legacy.check_timeout()
        legacy.feed_data(packet([0x80, 2, 0xFF, 0xFF]))
        self.assertEqual(legacy.attention, 0)
        self.assertFalse(legacy.device_worn)

    def test_stop_cancels_read_without_closing_from_caller(self):
        connection = mock.Mock()
        self.worker.serial_conn = connection
        self.worker.stop()
        self.worker.stop()
        connection.cancel_read.assert_called_once()
        connection.close.assert_not_called()
        self.assertTrue(self.worker._stop_requested.is_set())

    def test_cancel_failure_does_not_interrupt_shutdown(self):
        connection = mock.Mock()
        connection.cancel_read.side_effect = OSError('device disconnected')
        self.worker.serial_conn = connection
        self.worker.stop()
        self.worker.stop()
        connection.cancel_read.assert_called_once()
        connection.close.assert_not_called()

    def test_connection_is_detached_before_close_and_close_error_is_contained(self):
        connection = mock.Mock()
        connection.in_waiting = 0
        connection.read.side_effect = MODULE['serial'].SerialException('unplug')
        connections = []
        self.worker.connection_changed.connect(connections.append)

        def closing():
            self.assertIsNone(self.worker.serial_conn)
            self.worker.stop()
            raise OSError('already disconnected')

        connection.close.side_effect = closing
        with mock.patch.object(MODULE['serial'], 'Serial', return_value=connection):
            self.worker.run()
        connection.cancel_read.assert_not_called()
        self.assertEqual(connections, [True, False])

    def test_run_closes_serial_once_and_reports_unplug(self):
        connection = mock.Mock()
        connection.in_waiting = 0
        connection.read.side_effect = [packet([2, 0, 4, 80]), MODULE['serial'].SerialException('unplug')]
        connections = []
        errors = []
        self.worker.connection_changed.connect(connections.append)
        self.worker.error_occurred.connect(errors.append)
        with mock.patch.object(MODULE['serial'], 'Serial', return_value=connection) as constructor:
            self.worker.run()
        self.assertEqual(constructor.call_args.kwargs['timeout'], 0.1)
        self.assertEqual(connection.read.call_args_list[0], mock.call(1))
        connection.close.assert_called_once()
        self.assertEqual(connections, [True, False])
        self.assertEqual(len(errors), 1)
        self.assertFalse(self.updates[-1]['device_worn'])
        self.assertIsNone(self.worker.serial_conn)

    def test_stop_requested_during_open_does_not_announce_connected(self):
        connection = mock.Mock()
        connections = []
        self.worker.connection_changed.connect(connections.append)

        def opening(**kwargs):
            self.worker.stop()
            return connection

        with mock.patch.object(MODULE['serial'], 'Serial', side_effect=opening):
            self.worker.run()
        self.assertEqual(connections, [False])
        connection.close.assert_called_once()
        connection.read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
