from __future__ import annotations

import importlib
import logging
import math
import queue
import subprocess
import tempfile
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import numpy as np


class _VideoWriter(Protocol):
    def isOpened(self) -> bool: ...

    def write(self, frame: np.ndarray) -> None: ...

    def release(self) -> None: ...


@dataclass(frozen=True)
class _AudioPacket:
    data: bytes
    sample_rate: int
    start_time: float

    @property
    def end_time(self) -> float:
        return self.start_time + len(self.data) / (2 * self.sample_rate)


@dataclass
class _PendingClip:
    temporary_directory: tempfile.TemporaryDirectory[str]
    video_path: Path
    output_path: Path
    started_at: float
    duration: float
    audio_packets: list[_AudioPacket]


class MotionVideoRecorder:
    """Record local webcam MP4 clips with audio on motion or bark events."""

    def __init__(
        self,
        output_folder: Path,
        camera_index: int = 0,
        recording_seconds: float = 10.0,
        minimum_motion_area: float = 5000.0,
        motion_threshold: int = 25,
        fps: float = 20.0,
        startup_timeout: float = 10.0,
    ) -> None:
        if recording_seconds <= 0:
            raise ValueError("recording_seconds must be greater than zero")
        if minimum_motion_area <= 0:
            raise ValueError("minimum_motion_area must be greater than zero")
        if not 0 < motion_threshold < 256:
            raise ValueError("motion_threshold must be between 1 and 255")
        if fps <= 0:
            raise ValueError("fps must be greater than zero")
        if startup_timeout <= 0:
            raise ValueError("startup_timeout must be greater than zero")

        self.output_folder = output_folder
        self.camera_index = camera_index
        self.recording_seconds = recording_seconds
        self.minimum_motion_area = minimum_motion_area
        self.motion_threshold = motion_threshold
        self.fps = fps
        self.startup_timeout = startup_timeout

        self._logger = logging.getLogger("bark_monitor")
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._trigger_lock = threading.Lock()
        self._audio_queue: queue.SimpleQueue[_AudioPacket] = queue.SimpleQueue()
        self._audio_history: deque[_AudioPacket] = deque()
        self._audio_sample_rate: int | None = None
        self._record_until = 0.0
        self._thread: threading.Thread | None = None
        self._mux_threads: list[threading.Thread] = []
        self._error: Exception | None = None

    def start(self) -> None:
        """Start the webcam worker and fail if required local tools are missing."""
        if self._thread is not None:
            raise RuntimeError("Video recorder can only be started once")

        for module_name, feature in (
            ("cv2", "OpenCV webcam capture"),
            ("imageio_ffmpeg", "local audio/video muxing"),
        ):
            try:
                importlib.import_module(module_name)
            except ModuleNotFoundError as error:
                if error.name != module_name:
                    raise
                raise RuntimeError(
                    f"{feature} requires the video extra; install with "
                    "`uv sync --extra video`"
                ) from error

        cv2_module = importlib.import_module("cv2")
        self._thread = threading.Thread(
            target=self._record_loop,
            args=(cv2_module,),
            name="bark-monitor-video",
            daemon=True,
        )
        self._thread.start()

        if not self._ready_event.wait(self.startup_timeout):
            self._stop_event.set()
            self._thread.join(timeout=self.startup_timeout)
            raise TimeoutError("Timed out while opening the webcam")
        if self._error is not None:
            self._thread.join()
            raise RuntimeError("Could not start webcam recording") from self._error

    def trigger(self) -> None:
        """Start or extend video recording, for example after an audio bark."""
        if self._stop_event.is_set():
            return

        deadline = time.monotonic() + self.recording_seconds
        with self._trigger_lock:
            self._record_until = max(self._record_until, deadline)

    def add_audio_chunk(self, data: bytes, sample_rate: int) -> None:
        """Queue one mono int16 PCM chunk from the existing microphone stream."""
        if self._stop_event.is_set():
            return
        if sample_rate <= 0 or len(data) == 0 or len(data) % 2:
            raise ValueError("Audio chunks must contain mono int16 PCM samples")
        if (
            self._audio_sample_rate is not None
            and sample_rate != self._audio_sample_rate
        ):
            raise ValueError("Audio sample rate changed while video recording")

        self._audio_sample_rate = sample_rate
        end_time = time.monotonic()
        packet = _AudioPacket(
            data=data,
            sample_rate=sample_rate,
            start_time=end_time - len(data) / (2 * sample_rate),
        )
        self._audio_queue.put(packet)

    def stop(self) -> None:
        """Stop capture and wait for queued clips to be muxed."""
        if self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=self.startup_timeout)
        if self._thread.is_alive():
            raise TimeoutError("Timed out while stopping webcam recording")

        for mux_thread in self._mux_threads:
            mux_thread.join()
        if self._error is not None:
            raise RuntimeError("Webcam recording failed") from self._error

    def _record_loop(self, cv2_module: ModuleType) -> None:
        capture = None
        writer: _VideoWriter | None = None
        temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        video_path: Path | None = None
        output_path: Path | None = None
        clip_started_at = 0.0
        frame_count = 0
        clip_audio_packets: list[_AudioPacket] = []
        pending_clips: list[_PendingClip] = []
        previous_gray: np.ndarray | None = None
        latest_audio_end = 0.0
        try:
            capture = cv2_module.VideoCapture(self.camera_index)
            if not capture.isOpened():
                raise RuntimeError(f"Could not open webcam {self.camera_index}")

            capture.set(cv2_module.CAP_PROP_FPS, self.fps)
            camera_fps = capture.get(cv2_module.CAP_PROP_FPS)
            output_fps = (
                camera_fps if math.isfinite(camera_fps) and camera_fps > 0 else self.fps
            )
            self._ready_event.set()
            self._logger.info("Started local webcam motion detection")

            while not self._stop_event.is_set():
                latest_audio_end = max(
                    latest_audio_end,
                    self._drain_audio_queue(
                        writer is not None,
                        clip_audio_packets,
                        pending_clips,
                    ),
                )
                self._flush_ready_clips(pending_clips, latest_audio_end)

                success, frame = capture.read()
                if not success or frame is None:
                    raise RuntimeError("Could not read a frame from the webcam")

                gray = cv2_module.cvtColor(frame, cv2_module.COLOR_BGR2GRAY)
                gray = cv2_module.GaussianBlur(gray, (21, 21), 0)
                if previous_gray is not None:
                    difference = cv2_module.absdiff(previous_gray, gray)
                    thresholded = cv2_module.threshold(
                        difference,
                        self.motion_threshold,
                        255,
                        cv2_module.THRESH_BINARY,
                    )[1]
                    thresholded = cv2_module.dilate(thresholded, None, iterations=2)
                    contours = cv2_module.findContours(
                        thresholded,
                        cv2_module.RETR_EXTERNAL,
                        cv2_module.CHAIN_APPROX_SIMPLE,
                    )[0]
                    if any(
                        cv2_module.contourArea(contour) >= self.minimum_motion_area
                        for contour in contours
                    ):
                        self.trigger()
                previous_gray = gray

                latest_audio_end = max(
                    latest_audio_end,
                    self._drain_audio_queue(
                        writer is not None,
                        clip_audio_packets,
                        pending_clips,
                    ),
                )
                recording_active = self._is_recording_active()

                if recording_active:
                    if writer is None:
                        (
                            writer,
                            temporary_directory,
                            video_path,
                            output_path,
                        ) = self._open_writer(cv2_module, frame, output_fps)
                        clip_started_at = time.monotonic()
                        frame_count = 0
                        clip_audio_packets = list(self._audio_history)
                    writer.write(frame)
                    frame_count += 1
                elif writer is not None:
                    assert temporary_directory is not None
                    assert video_path is not None
                    assert output_path is not None
                    writer.release()
                    writer = None
                    pending_clips.append(
                        _PendingClip(
                            temporary_directory=temporary_directory,
                            video_path=video_path,
                            output_path=output_path,
                            started_at=clip_started_at,
                            duration=frame_count / output_fps,
                            audio_packets=clip_audio_packets,
                        )
                    )
                    temporary_directory = None
                    video_path = None
                    output_path = None
                    clip_audio_packets = []
                    self._logger.info("Stopped local webcam recording")

                self._flush_ready_clips(pending_clips, latest_audio_end)
        except Exception as error:
            self._error = error
            self._logger.exception("Local webcam recording stopped unexpectedly")
        finally:
            self._ready_event.set()
            latest_audio_end = max(
                latest_audio_end,
                self._drain_audio_queue(
                    writer is not None,
                    clip_audio_packets,
                    pending_clips,
                ),
            )
            if writer is not None:
                writer.release()
                assert temporary_directory is not None
                assert video_path is not None
                assert output_path is not None
                pending_clips.append(
                    _PendingClip(
                        temporary_directory=temporary_directory,
                        video_path=video_path,
                        output_path=output_path,
                        started_at=clip_started_at,
                        duration=frame_count / output_fps,
                        audio_packets=clip_audio_packets,
                    )
                )
            self._flush_ready_clips(pending_clips, latest_audio_end, force=True)
            if capture is not None:
                capture.release()
            self._logger.info("Stopped local webcam motion detection")

    def _is_recording_active(self) -> bool:
        with self._trigger_lock:
            return time.monotonic() < self._record_until

    def _drain_audio_queue(
        self,
        clip_is_open: bool,
        clip_packets: list[_AudioPacket],
        pending_clips: list[_PendingClip],
    ) -> float:
        newest_audio_end = 0.0
        while True:
            try:
                packet = self._audio_queue.get_nowait()
            except queue.Empty:
                break

            self._audio_history.append(packet)
            newest_audio_end = max(newest_audio_end, packet.end_time)
            if clip_is_open:
                clip_packets.append(packet)
            for pending_clip in pending_clips:
                clip_end = pending_clip.started_at + pending_clip.duration
                if (
                    packet.end_time > pending_clip.started_at
                    and packet.start_time < clip_end
                ):
                    pending_clip.audio_packets.append(packet)
            while (
                self._audio_history
                and newest_audio_end - self._audio_history[0].end_time > 5.0
            ):
                self._audio_history.popleft()
        return newest_audio_end

    def _flush_ready_clips(
        self,
        pending_clips: list[_PendingClip],
        latest_audio_end: float,
        force: bool = False,
    ) -> None:
        ready_clips = [
            clip
            for clip in pending_clips
            if force or latest_audio_end >= clip.started_at + clip.duration
        ]
        for clip in ready_clips:
            pending_clips.remove(clip)
            if self._audio_sample_rate is None:
                clip.temporary_directory.cleanup()
                self._error = RuntimeError(
                    "Cannot add audio to video because no microphone audio was received"
                )
                self._logger.error("%s", self._error)
                continue
            try:
                pcm_data = self._make_pcm_audio(clip)
            except Exception as error:
                clip.temporary_directory.cleanup()
                self._error = error
                self._logger.exception("Could not prepare audio for video clip")
                continue
            mux_thread = threading.Thread(
                target=self._mux_clip,
                args=(clip, pcm_data, self._audio_sample_rate),
                name="bark-monitor-video-mux",
                daemon=True,
            )
            self._mux_threads.append(mux_thread)
            mux_thread.start()

    def _make_pcm_audio(self, clip: _PendingClip) -> bytes:
        assert self._audio_sample_rate is not None
        sample_rate = self._audio_sample_rate
        target_samples = round(clip.duration * sample_rate)
        pcm_data = bytearray()

        for packet in sorted(clip.audio_packets, key=lambda item: item.start_time):
            if packet.sample_rate != sample_rate:
                raise RuntimeError("Audio sample rate changed while video recording")

            packet_offset = round((packet.start_time - clip.started_at) * sample_rate)
            source_start = max(0, -packet_offset)
            target_start = max(0, packet_offset)
            source_end = min(
                len(packet.data) // 2,
                target_samples - target_start + source_start,
            )
            if source_end <= source_start:
                continue

            current_samples = len(pcm_data) // 2
            if target_start > current_samples:
                pcm_data.extend(b"\x00\x00" * (target_start - current_samples))
            elif target_start < current_samples:
                source_start += current_samples - target_start

            if source_start < source_end:
                pcm_data.extend(packet.data[source_start * 2 : source_end * 2])

            if len(pcm_data) // 2 >= target_samples:
                del pcm_data[target_samples * 2 :]
                break

        if len(pcm_data) // 2 < target_samples:
            pcm_data.extend(b"\x00\x00" * (target_samples - len(pcm_data) // 2))
        return bytes(pcm_data)

    def _mux_clip(self, clip: _PendingClip, pcm_data: bytes, sample_rate: int) -> None:
        try:
            imageio_ffmpeg = importlib.import_module("imageio_ffmpeg")
            executable = imageio_ffmpeg.get_ffmpeg_exe()
            audio_path = Path(clip.temporary_directory.name, "audio.wav")
            with wave.open(str(audio_path), "wb") as audio_file:
                audio_file.setnchannels(1)
                audio_file.setsampwidth(2)
                audio_file.setframerate(sample_rate)
                audio_file.writeframes(pcm_data)

            subprocess.run(
                [
                    executable,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(clip.video_path),
                    "-i",
                    str(audio_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(clip.output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self._logger.info("Saved video with audio to %s", clip.output_path)
        except Exception as error:
            self._error = error
            clip.output_path.unlink(missing_ok=True)
            self._logger.exception("Could not mux audio into video clip")
        finally:
            clip.temporary_directory.cleanup()

    def _open_writer(
        self, cv2_module: ModuleType, frame: np.ndarray, fps: float
    ) -> tuple[
        _VideoWriter,
        tempfile.TemporaryDirectory[str],
        Path,
        Path,
    ]:
        height, width = frame.shape[:2]
        date_folder = self.output_folder / "audio" / datetime.now().strftime("%d-%m-%Y")
        date_folder.mkdir(parents=True, exist_ok=True)
        filename = datetime.now().strftime("video_%d-%m-%Y_%H-%M-%S-%f.mp4")
        output_path = date_folder / filename
        temporary_directory = tempfile.TemporaryDirectory(
            prefix=".bark-monitor-video-",
            dir=date_folder,
        )
        video_path = Path(temporary_directory.name, "video.mp4")

        writer = cv2_module.VideoWriter(
            str(video_path),
            cv2_module.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )
        if not writer.isOpened():
            writer.release()
            temporary_directory.cleanup()
            raise RuntimeError(f"Could not create temporary video file {video_path}")

        self._logger.info("Started local webcam recording")
        return writer, temporary_directory, video_path, output_path
