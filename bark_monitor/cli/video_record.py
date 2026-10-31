import logging
import time
from dataclasses import dataclass
from pathlib import Path

import tyro

from bark_monitor import logger
from bark_monitor.fake_sync import FakeSync
from bark_monitor.recorders.yamnet_lite_recorder import YamnetLiteRecorder
from bark_monitor.video import MotionVideoRecorder


@dataclass(kw_only=True)
class VideoParameters:
    output_folder: Path
    camera_index: int = 0
    recording_seconds: float = 10.0
    minimum_motion_area: float = 5000.0
    motion_threshold: int = 25
    fps: float = 20.0


def main() -> None:
    parameters = tyro.cli(VideoParameters)
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting local audio and video recording")

    video_recorder = MotionVideoRecorder(
        output_folder=parameters.output_folder,
        camera_index=parameters.camera_index,
        recording_seconds=parameters.recording_seconds,
        minimum_motion_area=parameters.minimum_motion_area,
        motion_threshold=parameters.motion_threshold,
        fps=parameters.fps,
    )
    audio_recorder = YamnetLiteRecorder(
        sync=FakeSync(),
        output_folder=parameters.output_folder,
        send_text_callback=logging.info,
        video_trigger_callback=video_recorder.trigger,
        audio_chunk_callback=video_recorder.add_audio_chunk,
    )

    video_recorder.start()
    try:
        audio_recorder.record()
        while audio_recorder.running:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping local audio and video recording")
    finally:
        try:
            if audio_recorder.running:
                audio_recorder.stop()
        finally:
            video_recorder.stop()


if __name__ == "__main__":
    main()
