"""Evidence-backed case results and release coverage summaries."""

from __future__ import annotations

from typing import ClassVar, Literal

from project import ROOT, Model, now, save
from pydantic import ConfigDict, Field, JsonValue, TypeAdapter


class CaseDefinition(Model):
    """Source-backed definition of one acceptance case."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    id: str
    requirement_ids: tuple[str, ...]
    priority: str
    name: str
    expected_result: str
    mode: Literal["host", "device", "mixed"]


class Registry(Model):
    """Parsed collection of the source workbook's acceptance cases."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    cases: tuple[CaseDefinition, ...]


class CaseResult(Model):
    """Observed result with its execution mode and evidence paths."""

    case_id: str
    result: Literal["passed", "failed", "waiting_condition", "partial"]
    mode: Literal["host", "device", "mixed"]
    evidence: tuple[str, ...] = ()
    observed_at: str = Field(default_factory=now)
    observation: str = ""
    operator: str = "AI"
    reviewed_by: str = ""


class Summary(Model):
    """Complete accounting of case outcomes."""

    total: int
    passed: int
    failed: int
    partial: int
    awaiting: int


class RequirementCoverage(Model):
    """Current acceptance outcomes for one numbered requirement."""

    requirement_id: str
    cases: tuple[str, ...]
    results: Summary


def summarize(results: tuple[CaseResult, ...]) -> Summary:
    """Count only evidence-backed complete case assertions as passed."""
    return Summary(
        total=len(results),
        passed=sum(result.result == "passed" and has_evidence(result) for result in results),
        failed=sum(result.result == "failed" for result in results),
        partial=sum(
            result.result == "partial" or (result.result == "passed" and not has_evidence(result))
            for result in results
        ),
        awaiting=sum(result.result == "waiting_condition" for result in results),
    )


def has_evidence(result: CaseResult) -> bool:
    """Require every cited observation to identify a collected project file."""
    if not result.evidence:
        return False
    paths = tuple((ROOT / path).resolve() for path in result.evidence)
    return all(path.is_relative_to(ROOT) and path.is_file() for path in paths)


def registry() -> Registry:
    """Load the versioned acceptance registry."""
    return Registry.model_validate_json((ROOT / "config/test_registry.json").read_text())


def persist(result: CaseResult) -> None:
    """Save the latest result for one case."""
    save(ROOT / "evidence" / result.case_id / "result.json", result)


def results() -> tuple[CaseResult, ...]:
    """Read recorded results and retain pending cases in the report."""
    output: list[CaseResult] = []
    for case in registry().cases:
        path = ROOT / "evidence" / case.id / "result.json"
        output.append(
            CaseResult.model_validate_json(path.read_text())
            if path.is_file()
            else CaseResult(
                case_id=case.id,
                result="waiting_condition",
                mode=case.mode,
                observation=case.expected_result,
            )
        )
    return tuple(output)


def write_summary() -> Summary:
    """Write case totals and requirement coverage from current evidence."""
    cases = results()
    summary = summarize(cases)
    save(
        ROOT / "evidence/acceptance.json",
        {
            "generated_at": now(),
            "summary": summary.model_dump(mode="json"),
            "cases": [case.model_dump(mode="json") for case in cases],
        },
    )
    coverage: list[JsonValue] = []
    for index in range(1, 25):
        requirement = f"R{index:02}"
        relevant_ids = {case.id for case in registry().cases if requirement in case.requirement_ids}
        relevant = tuple(case for case in cases if case.case_id in relevant_ids)
        item = RequirementCoverage(
            requirement_id=requirement,
            cases=tuple(sorted(relevant_ids)),
            results=summarize(relevant),
        )
        coverage.append(TypeAdapter(JsonValue).validate_json(item.model_dump_json()))
    save(ROOT / "evidence/requirement_coverage.json", coverage)
    return summary
