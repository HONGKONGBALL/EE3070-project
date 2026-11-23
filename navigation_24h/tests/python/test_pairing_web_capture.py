"""Worker capture integrity across fragment boundaries and periodic saves."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from capture import CaptureLine
from pairing_web_capture import Fragment, Recorder

if TYPE_CHECKING:
    from pathlib import Path


def test_periodic_capture_preserves_sdk_fragments_and_masks_split_credentials(
    tmp_path: Path,
) -> None:
    # Given: an SDK line containing a credential split across transport reads.
    recorder = Recorder()
    recorder.record("read", b"SDK key=sec")
    recorder.record("read", b'ret\r\n{"type":"status"}\nlast')
    recorder.record("read", b"")
    recorder.record("write", b'{"type":"pairing"}\n', 19)

    # When: the same worker writes periodic snapshots before and after another read.
    _ = recorder.persist(tmp_path, (b"secret",))
    recorder.record("read", b"-tail\n")
    files = recorder.persist(tmp_path, (b"secret",))

    # Then: byte offsets, empty reads, SDK text and the completed tail remain reproducible.
    expected = b'SDK key=******\r\n{"type":"status"}\nlast-tail\n'
    assert (tmp_path / "rx.raw").read_bytes() == expected
    fragments = tuple(
        Fragment.model_validate_json(line)
        for line in (tmp_path / "fragments.jsonl").read_text().splitlines()
    )
    assert len(fragments) == 5
    assert fragments[2].bytes == 0
    assert fragments[3].write_result == 19
    assert (
        b"".join(
            base64.b64decode(item.data_base64, validate=True)
            for item in fragments
            if item.direction == "read"
        )
        == expected
    )
    lines = tuple(
        CaptureLine.model_validate_json(line)
        for line in (tmp_path / "session.jsonl").read_text().splitlines()
    )
    assert len(lines) == 3
    assert lines[1].valid_json
    assert lines[2].raw == "last-tail"
    assert b"secret" not in b"".join(path.read_bytes() for path in files)
