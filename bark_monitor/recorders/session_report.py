from __future__ import annotations

import html
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

_REPORT_LOCK = threading.Lock()
_Source = Literal["motion", "sound"]
_Event = tuple[datetime, datetime, _Source, str]


class SessionReport:
    """Create a local HTML activity report for one recording session."""

    @staticmethod
    def start(session_folder: Path, started_at: datetime) -> None:
        session_folder.mkdir(parents=True, exist_ok=True)
        metadata = {"started_at": started_at.isoformat(), "ended_at": None}
        with _REPORT_LOCK:
            (session_folder / ".session-report.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            (session_folder / ".session-activity.jsonl").write_text(
                "", encoding="utf-8"
            )

    @staticmethod
    def add_detection(
        session_folder: Path,
        started_at: datetime,
        ended_at: datetime,
        source: _Source,
        label: str,
    ) -> None:
        if ended_at < started_at:
            raise ValueError("Detection end time must not precede its start time")
        event = {
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "source": source,
            "label": label,
        }
        with _REPORT_LOCK:
            with (session_folder / ".session-activity.jsonl").open(
                "a", encoding="utf-8"
            ) as activity_file:
                activity_file.write(json.dumps(event) + "\n")

    @classmethod
    def finish(cls, session_folder: Path, ended_at: datetime) -> None:
        with _REPORT_LOCK:
            metadata_path = session_folder / ".session-report.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["ended_at"] = ended_at.isoformat()
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            cls._write_report(session_folder, metadata)

    @classmethod
    def refresh(cls, session_folder: Path) -> None:
        """Refresh after another recorder has finished writing session detections."""
        with _REPORT_LOCK:
            metadata = json.loads(
                (session_folder / ".session-report.json").read_text(
                    encoding="utf-8"
                )
            )
            cls._write_report(session_folder, metadata)

    @classmethod
    def _write_report(
        cls, session_folder: Path, metadata: dict[str, str | None]
    ) -> None:
        started_at = datetime.fromisoformat(str(metadata["started_at"]))
        ended_at_value = metadata["ended_at"]
        ended_at = (
            datetime.fromisoformat(ended_at_value)
            if isinstance(ended_at_value, str)
            else started_at
        )
        events = cls._read_events(session_folder)
        report = cls._render(started_at, ended_at, events)
        report_path = session_folder / "session_report.html"
        temporary_path = report_path.with_suffix(".tmp")
        temporary_path.write_text(report, encoding="utf-8")
        temporary_path.replace(report_path)

    @staticmethod
    def _read_events(session_folder: Path) -> list[_Event]:
        events_path = session_folder / ".session-activity.jsonl"
        if not events_path.exists():
            return []
        events = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            events.append(
                (
                    datetime.fromisoformat(item["started_at"]),
                    datetime.fromisoformat(item["ended_at"]),
                    item["source"],
                    item["label"],
                )
            )
        return events

    @classmethod
    def _render(
        cls, started_at: datetime, ended_at: datetime, events: list[_Event]
    ) -> str:
        session_seconds = max(0.0, (ended_at - started_at).total_seconds())
        session_end = max(started_at, ended_at)

        def clipped(event: _Event) -> tuple[float, float] | None:
            event_start = max(started_at, event[0])
            event_end = min(session_end, event[1])
            if event_end <= event_start:
                return None
            return (
                (event_start - started_at).total_seconds(),
                (event_end - started_at).total_seconds(),
            )

        active_intervals = [
            interval for event in events if (interval := clipped(event)) is not None
        ]
        active_seconds = cls._duration(active_intervals)
        sound_events = [event for event in events if event[2] == "sound"]
        sound_intervals = [
            interval
            for event in sound_events
            if (interval := clipped(event)) is not None
        ]
        active_percent = (
            min(100.0, active_seconds / session_seconds * 100)
            if session_seconds
            else 0.0
        )
        sound_percent = (
            min(100.0, cls._duration(sound_intervals) / session_seconds * 100)
            if session_seconds
            else 0.0
        )
        duration_text = str(timedelta(seconds=round(active_seconds)))
        session_duration_text = str(timedelta(seconds=round(session_seconds)))
        session_text = (
            f"{started_at:%b %d, %Y · %H:%M} – {ended_at:%H:%M}"
            if started_at.date() == ended_at.date()
            else f"{started_at:%b %d, %H:%M} – {ended_at:%b %d, %H:%M}"
        )

        overall_tracks = [
            (
                "Motion",
                [
                    interval
                    for event in events
                    if event[2] == "motion"
                    and (interval := clipped(event)) is not None
                ],
                "#8b5cf6",
            ),
            (
                "Sound",
                sound_intervals,
                "#0e9f8a",
            ),
        ]
        sound_labels = sorted({event[3] for event in sound_events})
        sound_tracks = [
            (
                label,
                [
                    interval
                    for event in sound_events
                    if event[3] == label
                    and (interval := clipped(event)) is not None
                ],
                "#0e9f8a",
            )
            for label in sound_labels
        ]
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Session activity report</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, sans-serif;
      color: #172033;
      background: #f4f6fa;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 40px 20px; }}
    main {{ max-width: 1000px; margin: auto; }}
    header {{ margin-bottom: 28px; }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(1.7rem, 4vw, 2.4rem);
      letter-spacing: -.04em;
    }}
    .subtitle, .note {{ color: #687386; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .card, section {{
      background: white;
      border: 1px solid #e6eaf0;
      border-radius: 16px;
      box-shadow: 0 6px 24px #1720330a;
    }}
    .card {{ padding: 20px; }}
    .card-label {{ color: #687386; font-size: .88rem; }}
    .card-value {{
      display: block;
      margin-top: 8px;
      font-size: 1.9rem;
      font-weight: 700;
      letter-spacing: -.04em;
    }}
    section {{ padding: 22px; margin-bottom: 18px; overflow: hidden; }}
    h2 {{ margin: 0 0 6px; font-size: 1.1rem; }}
    .note {{ margin: 0 0 18px; font-size: .88rem; }}
    .chart {{ width: 100%; height: auto; display: block; overflow: visible; }}
    .chart text {{ font-family: inherit; fill: #687386; font-size: 13px; }}
    .chart .lane {{ fill: #f0f3f8; }}
    .chart .grid {{ stroke: #e6eaf0; stroke-width: 1; }}
    .chart .bar {{ rx: 5; }}
    .empty {{ padding: 20px 0 8px; color: #687386; }}
    @media (max-width: 560px) {{
      body {{ padding: 26px 14px; }}
      section {{ padding: 16px; }}
      .chart text {{ font-size: 11px; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>Session activity</h1>
    <div class="subtitle">{html.escape(session_text)}</div>
  </header>
  <div class="cards">
    <div class="card">
      <span class="card-label">Total session time</span>
      <strong class="card-value">{html.escape(session_duration_text)}</strong>
    </div>
    <div class="card">
      <span class="card-label">Time with activity</span>
      <strong class="card-value">{active_percent:.1f}%</strong>
    </div>
    <div class="card">
      <span class="card-label">Active duration</span>
      <strong class="card-value">{html.escape(duration_text)}</strong>
    </div>
    <div class="card">
      <span class="card-label">Designated sounds</span>
      <strong class="card-value">{sound_percent:.1f}%</strong>
    </div>
  </div>
  <section>
    <h2>Motion and sound over time</h2>
    <p class="note">Activity combines detected motion and designated sound events.</p>
    {cls._chart(started_at, session_seconds, overall_tracks)}
  </section>
  <section>
    <h2>Designated sounds</h2>
    <p class="note">Only configured sound labels are shown.</p>
    {cls._chart(started_at, session_seconds, sound_tracks)}
  </section>
</main>
</body>
</html>
"""

    @staticmethod
    def _duration(intervals: list[tuple[float, float]]) -> float:
        merged: list[list[float]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return sum(end - start for start, end in merged)

    @staticmethod
    def _chart(
        started_at: datetime,
        session_seconds: float,
        tracks: list[tuple[str, list[tuple[float, float]], str]],
    ) -> str:
        if not tracks:
            return '<div class="empty">No designated sounds detected.</div>'
        width = 1000
        left = 150
        right = 18
        chart_width = width - left - right
        row_height = 42
        top = 14
        axis_height = 34
        height = top + row_height * len(tracks) + axis_height
        pieces = [
            f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
            'aria-label="Activity timeline">'
        ]
        tick_count = 4
        for tick in range(tick_count + 1):
            fraction = tick / tick_count
            x = left + fraction * chart_width
            pieces.append(
                f'<line class="grid" x1="{x:.2f}" y1="{top}" '
                f'x2="{x:.2f}" y2="{top + row_height * len(tracks)}"/>'
            )
            tick_time = started_at + timedelta(seconds=session_seconds * fraction)
            pieces.append(
                f'<text x="{x:.2f}" y="{height - 8}" text-anchor="middle">'
                f"{tick_time:%H:%M}</text>"
            )
        for index, (label, intervals, color) in enumerate(tracks):
            y = top + index * row_height
            pieces.append(
                f'<text x="{left - 12}" y="{y + 22}" text-anchor="end">'
                f"{html.escape(label)}</text>"
            )
            pieces.append(
                f'<rect class="lane" x="{left}" y="{y + 4}" '
                f'width="{chart_width}" height="22" rx="5"/>'
            )
            for start, end in intervals:
                if session_seconds <= 0:
                    continue
                x = left + start / session_seconds * chart_width
                bar_width = max(2.0, (end - start) / session_seconds * chart_width)
                pieces.append(
                    f'<rect class="bar" x="{x:.2f}" y="{y + 4}" '
                    f'width="{min(bar_width, left + chart_width - x):.2f}" '
                    f'height="22" fill="{color}">'
                    f"<title>{html.escape(label)} · "
                    f"{started_at + timedelta(seconds=start):%H:%M:%S}</title></rect>"
                )
        pieces.append("</svg>")
        return "\n".join(pieces)
