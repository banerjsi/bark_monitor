# Bark Monitor

Bark Monitor locally detects dog-related sounds, saves audio recordings, and can
capture webcam video when motion or a bark is detected. Recordings and session
reports are stored on your machine.

## Features

- Analyze microphone audio with the bundled Lite YamNet model.
- Save audio snippets when dog-related sounds are detected.
- Optionally record webcam clips with microphone audio when motion or a bark
  triggers recording.
- Create a local HTML report for each session with sound and motion timelines.
- Run without cloud accounts or external services.

## Requirements

- Python 3.12 or newer.
- A microphone; a webcam is needed for video recording.
- PortAudio on Linux. On Debian or Ubuntu, install it with
  `sudo apt install libportaudio2`.

## Install

From the repository root, create a virtual environment and install the package
with its video dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[video]"
```

For audio-only recording, install without the video extra:

```sh
python -m pip install -e .
```

## Run

### Audio recording

Start local audio detection and recording:

```sh
bark-monitor --local --output-folder ./recordings --config-folder ./config
```

The bundled Lite YamNet model analyzes audio from the default microphone. No
cloud credentials or service configuration are needed. Stop recording with
Ctrl+C.

### Audio and video recording

Start webcam motion detection alongside local audio detection:

```sh
bark-monitor-video --output-folder ./recordings
```

By default, this uses webcam index `0`, records clips for 10 seconds after a
motion or bark trigger, and extends a clip when another trigger occurs before
it ends. The video is saved with audio from the microphone. To adjust recording
settings, pass options such as:

```sh
bark-monitor-video --output-folder ./recordings \
  --camera-index 1 --recording-seconds 20
```

Use `bark-monitor-video --help` to see all available options.

## Local files

Each run creates a timestamped session directory under `recordings/audio/`.
Detected audio snippets, video clips (when enabled), and `session_report.html`
are saved there. The report shows the session timeline, detected sounds, motion,
and activity durations. Overall recording state is stored in
`recordings/recording.json`.

The recordings remain in the output directory; back them up or remove them
according to your needs.

## Upstream attribution

This project is based on [Bark Monitor by Malcolm Mielle](https://codeberg.org/MalcolmMielle/bark_monitor).
This fork has been substantially customized, with an emphasis on local storage
and video detection and recording. The upstream project and its author are
credited here; this README describes this fork.

## Development

Run the test suite with:

```sh
python -m unittest discover -s tests
```

The project is licensed under the GNU Affero General Public License v3.0; see
[LICENSE.md](LICENSE.md).
