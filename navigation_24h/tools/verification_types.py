"""Typed CTest inventory, exact selection and observed assertion records."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, ClassVar, Literal

from project import Model, RunRecord
from pydantic import ConfigDict, JsonValue, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from pathlib import Path


class InventoryProperty(Model):
    """One CTest property from its JSON inventory."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    name: str
    value: JsonValue = None


class CompiledTest(Model):
    """Executable invocation and registered case labels."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    name: str
    command: tuple[str, ...] = ()
    properties: tuple[InventoryProperty, ...] = ()

    def case_ids(self) -> tuple[str, ...]:
        """Resolve acceptance identifiers from explicit labels and test names."""
        identifiers: set[str] = set()
        named = re.match(r"^(?:core_)?(TC\d{2})(?:_|$)", self.name)
        if named:
            identifiers.add(named.group(1))
        for item in self.properties:
            if item.name == "LABELS":
                labels = TypeAdapter(tuple[str, ...]).validate_python(item.value)
                identifiers.update(label for label in labels if re.fullmatch(r"TC\d{2}", label))
        return tuple(sorted(identifiers))


class Inventory(Model):
    """CTest's available compiled assertions."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    tests: tuple[CompiledTest, ...]


class Selection(Model):
    """Exact test selection and case environment requirements."""

    tests: tuple[CompiledTest, ...]
    unmapped: tuple[str, ...] = ()


class FixtureAssertion(Model):
    """One fixture's machine-readable observable outcome."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    case_id: str = ""
    cases: tuple[str, ...] = ()
    fixture: str = ""
    suite: str = ""
    scope: str = "host"
    result: Literal["passed", "failed"]
    detail: str = ""


class TestOutcome(Model):
    """Actual CTest JUnit status with the executable's retained output."""

    name: str
    result: Literal["passed", "failed", "skipped"]
    stdout: str
    fixtures: tuple[FixtureAssertion, ...] = ()


class ExecutedTest(Model):
    """A selected test joined to its real invocation and output evidence."""

    name: str
    command: tuple[str, ...]
    case_ids: tuple[str, ...]
    executable_sha256: str
    outcome: TestOutcome


class HostReport(Model):
    """Complete evidence for one requested set of acceptance identifiers."""

    run_id: str
    observed_at: str
    requested_ids: tuple[str, ...]
    mode: Literal["host", "device", "mixed"]
    exit_code: int
    status: Literal["executed", "waiting_condition", "failed"]
    tests: tuple[ExecutedTest, ...] = ()
    unmapped: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    records: tuple[RunRecord, ...] = ()
    junit: str = ""
    report: str


def select_tests(inventory_json: str, requested: tuple[str, ...]) -> Selection:
    """Select exact registered CTest names for the requested case identifiers."""
    inventory = Inventory.model_validate_json(inventory_json)
    wanted = set(requested)
    selected = tuple(test for test in inventory.tests if wanted.intersection(test.case_ids()))
    mapped = {case for test in selected for case in test.case_ids()}
    return Selection(
        tests=selected, unmapped=tuple(case for case in requested if case not in mapped)
    )


def read_junit(path: Path) -> tuple[TestOutcome, ...]:
    """Read locally generated CTest JUnit and its structured fixture output."""
    document = ET.fromstring(path.read_text())  # noqa: S314 - CTest owns this run's XML file.
    outcomes: list[TestOutcome] = []
    for item in document.iter("testcase"):
        output = item.findtext("system-out", "")
        fixtures: list[FixtureAssertion] = []
        for line in output.splitlines():
            try:
                assertion = FixtureAssertion.model_validate_json(line)
            except ValidationError:
                continue
            fixtures.append(assertion)
        failed = item.find("failure") is not None or item.find("error") is not None
        failed = failed or any(assertion.result == "failed" for assertion in fixtures)
        skipped = item.find("skipped") is not None or item.get("status") in {"notrun", "disabled"}
        outcome: Literal["passed", "failed", "skipped"] = (
            "failed" if failed else "skipped" if skipped else "passed"
        )
        outcomes.append(
            TestOutcome(
                name=item.get("name", ""), result=outcome, stdout=output, fixtures=tuple(fixtures)
            ),
        )
    return tuple(outcomes)
