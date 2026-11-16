"""M2 operation-runner evaluation tests."""

from __future__ import annotations

from app.catalog.service import CatalogService
from app.eval.loader import load_cases
from app.eval.runner import run_cases


def test_operation_runner_passes_first_cases() -> None:
    cases = load_cases()
    assert len(cases) >= 5
    results = run_cases(cases, CatalogService().by_kind)
    failures = [f"{result.case_id}: {result.message}" for result in results if not result.passed]
    assert not failures, "\n".join(failures)


def test_reanchor_case_reports_warning() -> None:
    case = next(case for case in load_cases() if case.id == "M2-OP-08-reanchor-warning")
    result = run_cases([case], CatalogService().by_kind)[0]
    assert result.passed, result.message
    assert "opening_reanchored" in result.warnings
