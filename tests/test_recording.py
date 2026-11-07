import unittest
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import jsonpickle

from bark_monitor.fake_sync import FakeSync
from bark_monitor.recorders.base_recorder import BaseRecorder
from bark_monitor.recorders.recording import Activity, Recording


class _TestRecorder(BaseRecorder):
    def _record_loop(self) -> None:
        pass


class TestRecording(unittest.TestCase):
    def test_each_recording_start_gets_a_timestamped_audio_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            folders: list[Path] = []
            recorder = _TestRecorder(
                sync=FakeSync(),
                output_folder=Path(temp_dir),
                session_started_callback=folders.append,
            )

            recorder._init()
            first_folder = recorder.session_audio_folder
            self.assertEqual(recorder._filename.parent, first_folder)
            recorder.running = False
            recorder._init()
            second_folder = recorder.session_audio_folder

            self.assertNotEqual(first_folder, second_folder)
            self.assertEqual(len(folders), 2)
            self.assertEqual(folders, [first_folder, second_folder])
            self.assertTrue(first_folder.is_dir())
            self.assertTrue(second_folder.is_dir())
            self.assertEqual(first_folder.parent, Path(temp_dir, "audio"))
            self.assertRegex(first_folder.name, r"^\d{2}-\d{2}-\d{4}_\d{4}(?:_\d+)?$")
            self.assertRegex(second_folder.name, r"^\d{2}-\d{2}-\d{4}_\d{4}(?:_\d+)?$")
            datetime.strptime(first_folder.name.split("_")[0], "%d-%m-%Y")
            self.assertRegex(first_folder.name.split("_")[1][:4], r"^\d{4}$")
            self.assertRegex(second_folder.name.split("_")[1][:4], r"^\d{4}$")

    def test_goal(self) -> None:
        sync_service = FakeSync()
        recording = Recording.read(Path("tests/data"), sync_service=sync_service)
        self.assertEqual(recording._time_barked["today"], timedelta(2))
        self.assertEqual(len(recording.start_end), 1)
        self.assertEqual(
            recording.start_end[0],
            (
                datetime(
                    year=2023,
                    month=6,
                    day=25,
                    hour=18,
                    minute=54,
                    second=35,
                    microsecond=878303,
                ),
                datetime(
                    year=2023,
                    month=6,
                    day=25,
                    hour=18,
                    minute=54,
                    second=40,
                    microsecond=678926,
                ),
            ),
        )

    def test_activity(self) -> None:
        sync_service = FakeSync()
        with TemporaryDirectory() as temp_dir:
            output_folder = Path(temp_dir)
            recording = Recording.read(output_folder, sync_service=sync_service)
            recording.clear_activity()
            self.assertEqual(len(recording.activity_tracker), 0)

            recording = Recording.read(output_folder, sync_service=sync_service)
            self.assertEqual(len(recording.activity_tracker), 0)
            time = datetime(year=2023, month=1, day=1)
            recording.add_activity(
                Activity(
                    date=time,
                    label="test activity",
                    duration=timedelta(0),
                    audio_path=Path(""),
                )
            )
            self.assertEqual(recording.activity_tracker[0].date, time)

            recording = Recording.read(output_folder, sync_service=sync_service)
            self.assertEqual(recording.activity_tracker[0].date, time)

            time_second = datetime(year=2023, month=1, day=2)
            recording.add_activity(
                Activity(
                    date=time_second,
                    label="test activity",
                    duration=timedelta(0),
                    audio_path=Path(""),
                )
            )
            self.assertEqual(len(recording.activity_tracker), 2)
            self.assertEqual(recording.activity_tracker[0].date, time_second)

            recording = Recording.read(output_folder, sync_service=sync_service)
            self.assertEqual(len(recording.activity_tracker), 2)
            self.assertEqual(recording.activity_tracker[0].date, time_second)
            self.assertEqual(recording.activity_tracker[1].date, time)

    @mock.patch.object(Recording, "save", return_value="None")
    def test_merge(self, _: Recording) -> None:
        with open("tests/data/recording.json", "r") as file:
            lines = file.read()
        state: "Recording" = jsonpickle.decode(lines, keys=True)
        state2: "Recording" = jsonpickle.decode(lines, keys=True)
        state._activity_tracker = deque()
        state2._activity_tracker = deque()

        state.merge(state2)
        self.assertEqual(len(state.start_end), 1)

        state2.start = datetime.now()
        state2.end(datetime.now())

        state.merge(state2)
        self.assertEqual(len(state.start_end), 2)
