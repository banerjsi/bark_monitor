import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from bark_monitor.fake_sync import FakeSync
from bark_monitor.recorders.base_recorder import BaseRecorder
from bark_monitor.recorders.session_report import SessionReport


class _TestRecorder(BaseRecorder):
    def _record_loop(self) -> None:
        pass


class TestSessionReport(unittest.TestCase):
    def test_recorder_stop_creates_report_in_session_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            recorder = _TestRecorder(FakeSync(), Path(temp_dir))
            recorder._init()
            session_folder = recorder.session_audio_folder

            recorder.stop()

            self.assertTrue((session_folder / "session_report.html").is_file())

    def test_report_shows_activity_percentage_and_separate_sound_graph(self) -> None:
        with TemporaryDirectory() as temp_dir:
            session_folder = Path(temp_dir)
            started_at = datetime(2026, 1, 1, 12, 0)
            SessionReport.start(session_folder, started_at)
            SessionReport.add_detection(
                session_folder,
                started_at + timedelta(seconds=10),
                started_at + timedelta(seconds=20),
                "motion",
                "Motion",
            )
            SessionReport.add_detection(
                session_folder,
                started_at + timedelta(seconds=15),
                started_at + timedelta(seconds=25),
                "sound",
                "Bark",
            )
            SessionReport.add_detection(
                session_folder,
                started_at + timedelta(seconds=80),
                started_at + timedelta(seconds=90),
                "sound",
                "Howl",
            )
            SessionReport.finish(session_folder, started_at + timedelta(seconds=100))

            report_path = session_folder / "session_report.html"
            self.assertTrue(report_path.is_file())
            report = report_path.read_text(encoding="utf-8")
            self.assertIn(
                '<span class="card-label">Total session time</span>\n'
                '      <strong class="card-value">0:01:40</strong>',
                report,
            )
            self.assertIn(
                '<span class="card-label">Time with activity</span>\n'
                '      <strong class="card-value">25.0%</strong>',
                report,
            )
            self.assertIn(
                '<span class="card-label">Designated sounds</span>\n'
                '      <strong class="card-value">20.0%</strong>',
                report,
            )
            self.assertIn("sound over time", report)
            self.assertIn("Designated sounds", report)
            self.assertIn("Bark", report)
            self.assertIn("Howl", report)
            self.assertNotIn("telegram", report.lower())

    def test_report_escapes_labels_and_handles_no_detections(self) -> None:
        with TemporaryDirectory() as temp_dir:
            session_folder = Path(temp_dir)
            started_at = datetime(2026, 1, 1, 12, 0)
            SessionReport.start(session_folder, started_at)
            SessionReport.add_detection(
                session_folder,
                started_at,
                started_at + timedelta(seconds=1),
                "sound",
                "<script>alert(1)</script>",
            )
            SessionReport.finish(session_folder, started_at + timedelta(seconds=10))
            report = (session_folder / "session_report.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("&lt;script&gt;", report)
            self.assertNotIn("<script>", report)

        with TemporaryDirectory() as temp_dir:
            session_folder = Path(temp_dir)
            started_at = datetime(2026, 1, 1, 12, 0)
            SessionReport.start(session_folder, started_at)
            SessionReport.finish(session_folder, started_at + timedelta(seconds=10))
            report = (session_folder / "session_report.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("0.0%", report)
            self.assertIn("No designated sounds detected.", report)


if __name__ == "__main__":
    unittest.main()
