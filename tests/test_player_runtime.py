"""Headless runtime regressions; no serial device or real user settings needed."""

import csv
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import QTimer
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import QApplication


class MemorySettings:
    """Keep test runs from reading or changing the user's saved preferences."""

    def __init__(self, *args):
        self.values = {}

    def value(self, key, default=None, type=None):
        value = self.values.get(key, default)
        return type(value) if type is not None else value

    def setValue(self, key, value):
        self.values[key] = value


with mock.patch("PyQt6.QtCore.QSettings", MemorySettings):
    PLAYER = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "brainwave_player_v8(1).py"),
        run_name="brainwave_player_runtime_tests",
    )

DataRecorder = PLAYER["DataRecorder"]
BrainwaveVideoPlayer = PLAYER["BrainwaveVideoPlayer"]
WaveformWidget = PLAYER["WaveformWidget"]


class FakeMediaPlayer:
    def __init__(self, on_state_changed):
        self.state = QMediaPlayer.PlaybackState.StoppedState
        self.on_state_changed = on_state_changed
        self.play_calls = 0
        self.pause_calls = 0
        self.rate = 1.0

    def playbackState(self):
        return self.state

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.on_state_changed(state)

    def play(self):
        self.set_state(QMediaPlayer.PlaybackState.PlayingState)
        self.play_calls += 1

    def pause(self):
        self.set_state(QMediaPlayer.PlaybackState.PausedState)
        self.pause_calls += 1

    def stop(self):
        self.set_state(QMediaPlayer.PlaybackState.StoppedState)

    def setPlaybackRate(self, rate):
        self.rate = rate

    def playbackRate(self):
        return self.rate


def init_fake_player(window):
    window.media_player = FakeMediaPlayer(window.on_playback_state_changed)
    window.audio_output = mock.Mock()


class RecorderTests(unittest.TestCase):
    def test_long_recording_keeps_bounded_preview_and_full_precision_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "session.csv"
            recorder = DataRecorder()
            recorder.start(filename)
            self.addCleanup(recorder.stop)
            samples = [(index % 101) / 10 for index in range(10000)]
            for index, attention in enumerate(samples):
                self.assertTrue(recorder.record(attention, 0, index % 3 == 0, 1.25))

            self.assertEqual(recorder.record_count, 10000)
            self.assertLessEqual(len(recorder.data), 200)
            self.assertEqual(len(recorder.get_preview(20)), 20)
            self.assertEqual(recorder.get_preview(1)[0][1], samples[-1])
            stats = recorder.get_statistics()
            self.assertEqual(stats["count"], 10000)
            self.assertAlmostEqual(stats["avg_attention"], sum(samples) / len(samples))
            self.assertAlmostEqual(stats["worn_ratio"], 3334 * 100 / 10000)

            recorder.stop()
            self.assertFalse(recorder.record(90, 0, True, 1.5))
            with filename.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(len(rows), 10001)
            self.assertEqual(float(rows[-1][1]), samples[-1])

    def test_start_failure_does_not_leave_a_recording_session(self):
        recorder = DataRecorder()
        with mock.patch("builtins.open", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                recorder.start("unwritable.csv")
        self.assertFalse(recorder.recording)
        self.assertIsNone(recorder.file)
        self.assertIsNone(recorder.writer)
        self.assertFalse(recorder.record(80, 0, True, 1.0))
        recorder.stop()


class PlayerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with mock.patch.object(BrainwaveVideoPlayer, "init_player", init_fake_player):
            with mock.patch("serial.tools.list_ports.comports", return_value=[]):
                self.player = BrainwaveVideoPlayer()
        self.player.video_loaded = True
        self.addCleanup(self.cleanup_player)

    def cleanup_player(self):
        for timer in self.player.findChildren(QTimer):
            timer.stop()
        self.player.speed_adjust_timer.stop()
        self.player.close()

    def send_attention(self, attention, worn=True):
        self.player.on_brainwave_data(
            {"attention": attention, "poor_signal": 0 if worn else 200, "device_worn": worn}
        )

    def test_invalid_signal_pauses_and_clears_all_smoother_histories(self):
        for name in PLAYER["SMOOTHERS"]:
            with self.subTest(smoother=name):
                self.player.change_smoother(name)
                self.send_attention(100)
                self.player.adjust_playback_speed()
                self.assertTrue(self.player.is_playing)
                self.send_attention(0, worn=False)
                self.assertEqual(self.player.current_attention, 0)
                self.assertEqual(self.player.smoothed_attention, 0)
                self.assertFalse(self.player.device_worn)
                self.assertFalse(self.player.is_playing)
                self.assertFalse(self.player.speed_adjust_timer.isActive())
                self.assertIsNone(self.player.speed_smoother.smoothed)
                self.send_attention(60)
                self.assertEqual(self.player.current_attention, 60)

    def test_manual_pause_survives_later_high_attention_samples(self):
        self.send_attention(100)
        self.assertTrue(self.player.is_playing)
        self.player.toggle_playback()
        self.assertTrue(self.player.manual_paused)
        play_calls = self.player.media_player.play_calls
        for _ in range(20):
            self.send_attention(100)
        self.assertEqual(self.player.media_player.play_calls, play_calls)
        self.assertFalse(self.player.is_playing)
        self.assertFalse(self.player.speed_adjust_timer.isActive())

        self.player.toggle_playback()
        self.assertFalse(self.player.manual_paused)
        self.assertTrue(self.player.is_playing)

    def test_signal_recovery_without_video_clears_invalid_status(self):
        self.player.video_loaded = False
        self.send_attention(0, worn=False)
        self.assertIn("信号无效", self.player.status_label.text())

        self.send_attention(80)
        self.assertEqual(self.player.status_label.text(), "状态: 设备信号正常，请打开视频")
        self.assertEqual(self.player.media_player.play_calls, 0)

    def test_signal_recovery_without_video_preserves_recording_status(self):
        self.player.video_loaded = False
        with tempfile.TemporaryDirectory() as directory:
            self.player.recorder.start(Path(directory) / "signal-recovery.csv")
            try:
                self.player.status_label.setText("状态: 正在记录数据...")
                self.send_attention(80)
                self.assertEqual(self.player.status_label.text(), "状态: 正在记录数据...")

                self.send_attention(0, worn=False)
                self.assertIn("信号无效", self.player.status_label.text())
                self.send_attention(80)
                self.assertEqual(self.player.status_label.text(), "状态: 正在记录数据...")
                self.assertTrue(self.player.recorder.recording)
                self.assertEqual(self.player.media_player.play_calls, 0)
            finally:
                self.player.recorder.stop()

    def test_disconnect_invalidates_previous_attention(self):
        self.send_attention(100)
        self.player.adjust_playback_speed()
        self.player.on_connection_changed(False)
        self.assertEqual(self.player.current_attention, 0)
        self.assertEqual(self.player.smoothed_attention, 0)
        self.assertFalse(self.player.device_worn)
        self.assertFalse(self.player.is_playing)
        self.assertFalse(self.player.speed_adjust_timer.isActive())
        self.assertIsNone(self.player.attention_smoother.smoothed)
        self.assertIsNone(self.player.speed_smoother.smoothed)

    def test_queued_data_cannot_restart_playback_during_disconnect_or_close(self):
        self.send_attention(100)
        thread = mock.Mock()
        thread.isRunning.return_value = True
        with mock.patch.object(self.player, "brainwave_thread", thread):
            self.player.connect_device()
            thread.stop.assert_called_once()
            plays = self.player.media_player.play_calls
            self.send_attention(100)
            self.assertEqual(self.player.media_player.play_calls, plays)
            self.assertFalse(self.player.device_worn)
            self.assertFalse(self.player.is_playing)
        self.player._disconnecting = False
        self.player._closing = True
        self.send_attention(100)
        self.assertEqual(self.player.media_player.play_calls, plays)
        self.assertEqual(self.player.current_attention, 0)

    def test_record_write_failure_stops_session_and_reports_once(self):
        with tempfile.TemporaryDirectory() as directory:
            self.player.recorder.start(Path(directory) / "write-failure.csv")
            output = self.player.recorder.file
            writer = mock.Mock()
            writer.writerow.side_effect = OSError("disk full")
            self.player.recorder.writer = writer
            with mock.patch.object(PLAYER["QMessageBox"], "critical") as alert:
                for _ in range(5):
                    self.send_attention(80)
                self.player.refresh_background_views()
            alert.assert_called_once()
            writer.writerow.assert_called_once()
            self.assertFalse(self.player.recorder.recording)
            self.assertIsNone(self.player.recorder.file)
            self.assertEqual(self.player.recorder.record_count, 0)
            self.assertTrue(output.closed)
            self.assertTrue(self.player.start_record_btn.isEnabled())
            self.assertFalse(self.player.stop_record_btn.isEnabled())

    def test_background_flush_failure_stops_session_and_reports_once(self):
        with tempfile.TemporaryDirectory() as directory:
            self.player.recorder.start(Path(directory) / "flush-failure.csv")
            output = self.player.recorder.file
            failing_output = mock.Mock(wraps=output)
            failing_output.flush.side_effect = OSError("disk disconnected")
            self.player.recorder.file = failing_output
            self.player.recorder.last_flush = 0
            with mock.patch.object(PLAYER["QMessageBox"], "critical") as alert:
                for _ in range(5):
                    self.player.refresh_background_views()
                self.send_attention(80)
            alert.assert_called_once()
            failing_output.flush.assert_called_once()
            self.assertFalse(self.player.recorder.recording)
            self.assertIsNone(self.player.recorder.file)
            self.assertTrue(output.closed)
            self.assertTrue(self.player.start_record_btn.isEnabled())
            self.assertFalse(self.player.stop_record_btn.isEnabled())

    def test_recording_error_dialog_cannot_replay_stale_valid_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            self.player.recorder.start(Path(directory) / "nested-error.csv")
            writer = mock.Mock()
            writer.writerow.side_effect = OSError("disk full")
            self.player.recorder.writer = writer
            self.player.update_threshold(0)
            with mock.patch.object(
                PLAYER["QMessageBox"], "critical",
                side_effect=lambda *args: self.send_attention(0, worn=False),
            ):
                self.send_attention(80)
            self.assertFalse(self.player.device_worn)
            self.assertFalse(self.player.is_playing)
            self.assertEqual(self.player.current_attention, 0)
            self.assertIn("信号无效", self.player.status_label.text())

    def test_play_button_waits_for_valid_signal_while_device_thread_is_active(self):
        thread = mock.Mock()
        thread.isRunning.return_value = True
        with mock.patch.object(self.player, "brainwave_thread", thread):
            self.send_attention(0, worn=False)
            for _ in range(3):
                self.player.toggle_playback()
            self.assertFalse(self.player.is_playing)
            self.assertEqual(self.player.media_player.play_calls, 0)
            self.assertIn("等待有效注意力", self.player.status_label.text())
            self.send_attention(100)
            self.assertTrue(self.player.is_playing)

    def test_debug_log_stays_bounded_and_clear_discards_pending_messages(self):
        for batch in range(15):
            for index in range(1000):
                self.player.on_debug_message(f"packet-{batch}-{index}")
            self.player.refresh_background_views()
            self.assertLessEqual(self.player.debug_log.document().blockCount(), 1000)
        self.assertIn("packet-14-999", self.player.debug_log.toPlainText())
        self.assertNotIn("packet-0-", self.player.debug_log.toPlainText())

        self.player.on_debug_message("queued before clear")
        self.player.toggle_debug_mode(False)
        self.player.refresh_background_views()
        self.assertEqual(self.player.debug_log.toPlainText(), "")
        self.assertFalse(self.player._pending_logs)

    def test_zero_threshold_still_reports_invalid_signal_and_pauses(self):
        self.player.update_threshold(0)
        self.send_attention(100)
        self.send_attention(0, worn=False)
        self.assertFalse(self.player.is_playing)
        self.assertIn("信号无效", self.player.status_label.text())
        self.assertNotIn("<", self.player.status_label.text())

    def test_loading_changed_threshold_preserves_other_saved_preferences(self):
        saved = {
            "threshold": 73,
            "baudrate": "115200",
            "smoother": "中值滤波",
            "legacy_raw_mapping": True,
        }
        expected = dict(saved)

        class SeededSettings(MemorySettings):
            def __init__(self, *args):
                self.values = saved

        with mock.patch.dict(
            BrainwaveVideoPlayer.load_settings.__globals__, {"QSettings": SeededSettings}
        ):
            with mock.patch.object(BrainwaveVideoPlayer, "init_player", init_fake_player):
                with mock.patch("serial.tools.list_ports.comports", return_value=[]):
                    loaded_player = BrainwaveVideoPlayer()
        self.addCleanup(loaded_player.close)

        self.assertEqual(saved, expected)
        self.assertEqual(loaded_player.attention_threshold, 73)
        self.assertEqual(loaded_player.threshold_slider.value(), 73)
        self.assertEqual(loaded_player.baud_combo.currentText(), "115200")
        self.assertEqual(loaded_player.current_smoother_name, "中值滤波")
        self.assertTrue(loaded_player.legacy_raw_checkbox.isChecked())

    def test_speed_curve_uses_independent_right_axis(self):
        waveform = self.player.waveform_widget
        attention_view = waveform.plot_widget.getViewBox()
        self.assertIsNot(waveform.speed_view, attention_view)
        self.assertIs(waveform.curve.getViewBox(), waveform.speed_view)
        self.assertIs(waveform.attention_curve.getViewBox(), attention_view)
        self.assertIs(waveform.plot_widget.getAxis("right").linkedView(), waveform.speed_view)
        for _ in range(1000):
            waveform.update_data(2.0, 90, 60)
        self.assertLessEqual(len(waveform.speed_data), 200)
        self.assertLessEqual(len(waveform.attention_data), 200)
        left_range = attention_view.viewRange()[1]
        right_range = waveform.speed_view.viewRange()[1]
        self.assertGreater(left_range[1], 90)
        self.assertLess(right_range[1], 10)


if __name__ == "__main__":
    unittest.main()
