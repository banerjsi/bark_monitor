import threading
import time
import unittest
import wave
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest import mock

from bark_monitor.video import MotionVideoRecorder


class _Frame:
    def __init__(self, value: int) -> None:
        self.value = value
        self.shape: tuple[int, int, int] = (10, 10, 3)


class _Capture:
    def __init__(self) -> None:
        self._frame_index = 0
        self.requested_fps: float | None = None

    def isOpened(self) -> bool:
        return True

    def set(self, property_id: int, value: float) -> None:
        assert property_id == 1
        self.requested_fps = value

    def get(self, property_id: int) -> float:
        assert property_id == 1
        return 20.0

    def read(self) -> tuple[bool, _Frame]:
        time.sleep(0.001)
        frame = _Frame(0 if self._frame_index == 0 else 255)
        self._frame_index += 1
        return True, frame

    def release(self) -> None:
        return


class _Writer:
    def __init__(self) -> None:
        self.wrote_frame = threading.Event()
        self.last_frame: _Frame | None = None
        self.settings: tuple[int, float, tuple[int, int]] | None = None

    def isOpened(self) -> bool:
        return True

    def write(self, frame: _Frame) -> None:
        self.last_frame = frame
        self.wrote_frame.set()

    def release(self) -> None:
        return


class TestMotionVideoRecorder(unittest.TestCase):
    def test_audio_trigger_extends_recording_window(self) -> None:
        recorder = MotionVideoRecorder(
            output_folder=Path("recordings"),
            recording_seconds=10,
        )
        with mock.patch(
            "bark_monitor.video.time.monotonic",
            side_effect=[100.0, 101.0],
        ):
            recorder.trigger()
            recorder.trigger()

        self.assertEqual(recorder._record_until, 111.0)

    def test_rejects_invalid_motion_threshold(self) -> None:
        with self.assertRaises(ValueError):
            MotionVideoRecorder(
                output_folder=Path("recordings"),
                motion_threshold=256,
            )

    def test_motion_records_mp4_under_audio_date_folder(self) -> None:
        capture = _Capture()
        writer = _Writer()
        output_paths: list[Path] = []
        muxed_outputs: list[Path] = []
        muxed_audio: list[tuple[int, bytes]] = []
        mux_complete = threading.Event()
        cv2_module = ModuleType("cv2")
        ffmpeg_module = ModuleType("imageio_ffmpeg")
        setattr(ffmpeg_module, "get_ffmpeg_exe", lambda: "/local/ffmpeg")

        def make_capture(camera_index: int) -> _Capture:
            assert camera_index == 0
            return capture

        def make_writer(
            filename: str,
            fourcc: int,
            fps: float,
            frame_size: tuple[int, int],
        ) -> _Writer:
            output_paths.append(Path(filename))
            writer.settings = (fourcc, fps, frame_size)
            return writer

        def cvt_color(frame: _Frame, conversion: int) -> int:
            assert conversion == 2
            return frame.value

        def gaussian_blur(frame: int, kernel: tuple[int, int], sigma: int) -> int:
            assert kernel == (21, 21)
            assert sigma == 0
            return frame

        def make_fourcc(*codec: str) -> int:
            assert codec == ("m", "p", "4", "v")
            return 0

        def threshold(
            difference: int,
            threshold_value: int,
            maximum_value: int,
            threshold_type: int,
        ) -> tuple[None, int]:
            assert threshold_value == 25
            assert maximum_value == 255
            assert threshold_type == 3
            return None, difference

        def dilate(image: int, kernel: None, iterations: int) -> int:
            assert kernel is None
            assert iterations == 2
            return image

        def find_contours(
            image: int,
            retrieval_mode: int,
            approximation_method: int,
        ) -> tuple[list[int], None]:
            assert retrieval_mode == 4
            assert approximation_method == 5
            return ([image] if image else []), None

        def contour_area(contour: int) -> float:
            return float(contour * 10000)

        for name, value in {
            "CAP_PROP_FPS": 1,
            "COLOR_BGR2GRAY": 2,
            "THRESH_BINARY": 3,
            "RETR_EXTERNAL": 4,
            "CHAIN_APPROX_SIMPLE": 5,
            "VideoCapture": make_capture,
            "VideoWriter_fourcc": make_fourcc,
            "VideoWriter": make_writer,
            "cvtColor": cvt_color,
            "GaussianBlur": gaussian_blur,
            "absdiff": lambda first, second: abs(first - second),
            "threshold": threshold,
            "dilate": dilate,
            "findContours": find_contours,
            "contourArea": contour_area,
        }.items():
            setattr(cv2_module, name, value)

        with TemporaryDirectory() as temp_dir:
            recorder = MotionVideoRecorder(
                output_folder=Path(temp_dir),
                recording_seconds=1,
            )
            recorder.add_audio_chunk(b"\x01\x00" * 16000, 16000)

            def import_module(module_name: str) -> ModuleType:
                if module_name == "cv2":
                    return cv2_module
                if module_name == "imageio_ffmpeg":
                    return ffmpeg_module
                raise ModuleNotFoundError(module_name)

            def run_ffmpeg(arguments: list[str], **options: object) -> None:
                self.assertTrue(options["check"])
                audio_index = arguments.index("-i", arguments.index("-i") + 1) + 1
                with wave.open(arguments[audio_index], "rb") as audio_file:
                    muxed_audio.append(
                        (audio_file.getframerate(), audio_file.readframes(50000))
                    )
                muxed_outputs.append(Path(arguments[-1]))
                mux_complete.set()

            with (
                mock.patch(
                    "bark_monitor.video.subprocess.run",
                    side_effect=run_ffmpeg,
                ),
                mock.patch(
                    "bark_monitor.video.importlib.import_module",
                    side_effect=import_module,
                ),
            ):
                recorder.start()
                self.assertTrue(writer.wrote_frame.wait(timeout=1))
                recorder.stop()

            self.assertTrue(mux_complete.is_set())
            self.assertEqual(len(output_paths), 1)
            self.assertEqual(capture.requested_fps, 20.0)
            self.assertIsNotNone(writer.last_frame)
            self.assertEqual(writer.settings, (0, 20.0, (10, 10)))
            self.assertEqual(output_paths[0].name, "video.mp4")
            self.assertEqual(muxed_audio[0][0], 16000)
            self.assertGreater(len(muxed_audio[0][1]), 0)
            self.assertEqual(muxed_outputs[0].suffix, ".mp4")
            self.assertEqual(
                muxed_outputs[0].parent,
                Path(temp_dir, "audio", datetime.now().strftime("%d-%m-%Y")),
            )
