import logging
import threading
import wave
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Callable

import sounddevice

from bark_monitor.base_sync import BaseSync
from bark_monitor.recorders.recording import Recording
from bark_monitor.recorders.session_report import SessionReport


class BaseRecorder(ABC):
    def __init__(
        self,
        sync: BaseSync,
        output_folder: Path,
        framerate: int = 44100,
        chunk: int = 4096,
        send_text_callback: Callable[[str], None] | None = None,
        session_started_callback: Callable[[Path], None] | None = None,
    ) -> None:
        self.running = False
        self.is_paused = False

        self._chunk = chunk  # Record in chunks of 1024 samples
        self._channels = 1
        self._fs = framerate

        self._frames: list[bytes] = []  # Initialize array to store frames

        self._t: threading.Thread | None = None

        self._stream: sounddevice.InputStream | None = None

        self._bark_logger = logging.getLogger("bark_monitor")

        self.output_folder = output_folder
        self._session_audio_folder: Path | None = None

        self._bark_logger.info("Starting bot")
        self._sync = sync

        self.send_text_callback = send_text_callback
        self.session_started_callback = session_started_callback

    @property
    def sync(self) -> BaseSync:
        return self._sync

    @property
    def audio_folder(self) -> Path:
        audio_path = Path(self.output_folder, "audio")
        audio_path.mkdir(parents=True, exist_ok=True)
        return audio_path

    @property
    def session_audio_folder(self) -> Path:
        if self._session_audio_folder is not None:
            return self._session_audio_folder

        session_folders = [
            folder for folder in self.audio_folder.iterdir() if folder.is_dir()
        ]
        if session_folders:
            return max(session_folders, key=lambda folder: folder.stat().st_mtime)
        return self.audio_folder

    @property
    def today_audio_folder(self) -> Path:
        return self.session_audio_folder

    @property
    def _filename(self) -> Path:
        now = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")
        filename = self.session_audio_folder / (now + ".wav")
        filename.parent.mkdir(parents=True, exist_ok=True)
        return filename.absolute()

    def _init(self):
        session_name = datetime.now().strftime("%d-%m-%Y_%H%M")
        self._session_audio_folder = self.audio_folder / session_name
        suffix = 1
        while True:
            try:
                self._session_audio_folder.mkdir()
                break
            except FileExistsError:
                self._session_audio_folder = self.audio_folder / (
                    f"{session_name}_{suffix}"
                )
                suffix += 1
        if self.session_started_callback is not None:
            self.session_started_callback(self._session_audio_folder)

        recording = Recording.read(self.output_folder, sync_service=self._sync)
        session_started_at = datetime.now()
        recording.start = session_started_at
        SessionReport.start(self._session_audio_folder, session_started_at)
        self.running = True

    def record(self) -> None:
        self._init()
        self._record()

    def stop(self) -> None:
        self.running = False
        self._stop()
        recording = Recording.read(self.output_folder, sync_service=self._sync)
        ended_at = datetime.now()
        recording.end(ended_at)

        assert self._session_audio_folder is not None
        SessionReport.finish(self._session_audio_folder, ended_at)

        # Sync
        recording.upload(self._sync)
        self._sync.save_audio(self.audio_folder)

    def _stop(self) -> None:
        if self._t is None:
            return
        self._t.join()

    def _save_recording(self, frames: list[bytes], prefix: str | None = None) -> Path:
        """Save a recording of `frames` to `self._filename`.

        :return: the path at which the recording is saved.
        """
        filepath = self._filename
        if prefix is not None:
            filepath = filepath.with_name(prefix + " " + filepath.name)
        file = self._save_recording_to(frames, filepath)
        if self.send_text_callback is not None:
            self.send_text_callback(
                "Save file: "
                + str(filepath)
                + ".\nDownload it with \n\n ```\n/audio "
                + filepath.name
                + "\n```"
            )
        return file

    def _save_recording_to(self, frames: list[bytes], filepath: Path) -> Path:
        """Save a recording of `frames` to `filepath`.

        :return: the path at which the recording is saved.
        """
        # Save the recorded data as a WAV file
        wf = wave.open(str(filepath), "wb")
        wf.setnchannels(self._channels)
        wf.setsampwidth(2)
        wf.setframerate(self._fs)
        wf.writeframes(b"".join(frames))
        wf.close()
        return filepath

    def _start_stream(self) -> None:
        self._stream = sounddevice.InputStream(
            samplerate=self._fs,
            channels=self._channels,
            dtype="int16",
            blocksize=self._chunk,
        )
        self._stream.start()

    def _stop_stream(self) -> None:
        if self._stream is not None:
            # Stop and close the stream
            self._stream.stop()
            self._stream.close()

    def _record(self) -> None:
        self._t = threading.Thread(target=self._record_loop)
        self._t.start()

    @abstractmethod
    def _record_loop(self) -> None:
        raise NotImplementedError("abstract")
