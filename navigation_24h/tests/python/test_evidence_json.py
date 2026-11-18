"""Structured evidence redaction and JSON serialization boundaries."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from project import RunRecord, save
from pydantic import JsonValue, TypeAdapter

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_save_round_trips_escaped_subprocess_output(tmp_path: Path) -> None:
    # Given: subprocess output containing nested quotes, backslashes and credential forms.
    output = (
        "password = '  \"wifi \\\\ secret\"  '\n"
        'Authorization: Bearer sample-token\n{"password":"two \\"quoted\\" words"}'
    )
    record = RunRecord(
        command=("python", "-m", "pytest"),
        started_at="2026-11-17T18:00:00+08:00",
        duration_ms=1,
        exit_code=1,
        stdout=output,
        stderr='line "quoted" \\ next',
        log="evidence/test.json",
    )
    path = tmp_path / "record.json"
    # When: the evidence boundary saves the typed subprocess observation.
    save(path, record)
    # Then: the JSON and typed record parse with their remaining structure intact.
    restored = RunRecord.model_validate_json(path.read_text())
    assert restored.command == record.command
    assert restored.stderr == record.stderr
    assert restored.exit_code == 1
    assert "sample-token" not in restored.stdout


def test_save_masks_string_credentials_and_preserves_schema_nodes(tmp_path: Path) -> None:
    # Given: mixed evidence values and JSON Schema credential properties.
    value: JsonValue = {
        "password": 'quoted "secret" \\ path',
        "token": 17,
        "authorization": "Bearer sample",
        "schema": {"properties": {"password": {"type": "string"}, "key": {"type": "object"}}},
        "flags": [True, False, None, 1, 2.5],
    }
    path = tmp_path / "structured.json"
    # When: structured evidence crosses the serialization boundary.
    save(path, value)
    # Then: credential values and schema structure retain their respective semantics.
    restored = TypeAdapter(JsonValue).validate_json(path.read_text())
    expected: JsonValue = {
        "password": "[redacted]",
        "token": 17,
        "authorization": "[redacted]",
        "schema": {"properties": {"password": {"type": "string"}, "key": {"type": "object"}}},
        "flags": [True, False, None, 1, 2.5],
    }
    assert restored == expected
    assert value["password"] == 'quoted "secret" \\ path'  # noqa: S105 - Synthetic fixture.


def test_save_masks_environment_value_before_json_escaping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an environment credential whose characters require JSON escaping.
    credential = ' sample "key" \\ end '
    monkeypatch.setenv("AMAP_MAPS_API_KEY", credential)
    value: JsonValue = {"stdout": "prefix " + credential + " suffix", "raw": [credential]}
    path = tmp_path / "environment.json"
    # When: the evidence writer saves the value tree.
    save(path, value)
    # Then: both serialized occurrences use the same redaction marker.
    assert json.loads(path.read_text()) == {
        "stdout": "prefix [redacted] suffix",
        "raw": ["[redacted]"],
    }
