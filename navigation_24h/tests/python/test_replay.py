"""Replay CLI reconstructs a completed capture through its public entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from capture import decode_line
from replay import ReplayReport


def test_cli_reconstructs_completed_capture_when_status_and_resource_arrive(tmp_path: Path) -> None:
    # Given a completed capture containing ordered status and resource records.
    source = tmp_path / "completed.jsonl"
    records = (
        decode_line(b'{"type":"status","state":"IDLE","device_monotonic_ms":100}\n', "usb", 1000),
        decode_line(
            b"".join(
                (
                    b'{"type":"resources","monotonic_ms":200,"boot_id":"boot-one",',
                    b'"free_internal":175583,"largest_internal":102400,"free_psram":8386192}\n',
                )
            ),
            "usb",
            1100,
        ),
    )
    _ = source.write_text("".join(record.model_dump_json() + "\n" for record in records))
    executable = Path(__file__).resolve().parents[2] / "tools" / "replay.py"
    # When the actual replay CLI consumes the capture.
    result = subprocess.run(  # noqa: S603 - fixed local CLI and pytest-owned input path.
        [sys.executable, str(executable), str(source)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    # Then the persisted replay contains the observed payloads in their original order.
    assert result.returncode == 0, result.stderr
    report = ReplayReport.model_validate_json((tmp_path / "completed-replay.json").read_text())
    assert report.records == 2
    assert report.chronological is True
    assert report.malformed == 0
    assert report.timeline == tuple(record.payload for record in records)
    assert (tmp_path / "completed-metrics.json").is_file()
