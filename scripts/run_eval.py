#!/usr/bin/env python
"""Run the shared evaluation case table and write a metrics report (M4-05/06).

Usage (from the repo root):

    uv run --directory apps/server python ../../scripts/run_eval.py
    uv run --directory apps/server python ../../scripts/run_eval.py --report eval/reports/custom.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = REPO_ROOT / "apps" / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.catalog.service import CatalogService  # noqa: E402
from app.contracts.rules import get_rules  # noqa: E402
from app.eval.loader import load_cases  # noqa: E402
from app.eval.runner import EvalResult, run_case  # noqa: E402


def git_sha() -> str:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return f"{sha}-dirty" if dirty else sha
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return round(ordered[index], 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--today",
        default=datetime.now(UTC).date().isoformat(),
        help="Report date prefix (default: today).",
    )
    args = parser.parse_args()

    cases = load_cases()
    catalog = CatalogService().by_kind
    rules = get_rules()
    results: list[EvalResult] = []
    durations: list[float] = []
    solver_times: list[float] = []
    solver_candidates: list[float] = []
    vlm_parse: list[float] = []
    vlm_statuses: Counter[str] = Counter()
    vlm_unconfirmed = 0
    vlm_confirmed = 0
    vlm_unknown = 0
    kinds: Counter[str] = Counter()
    failures: list[dict[str, str]] = []

    for case in cases:
        kinds[case.kind] += 1
        started = time.perf_counter()
        result = run_case(case, catalog, rules)
        duration = (time.perf_counter() - started) * 1000.0
        durations.append(duration)
        results.append(result)
        if result.metrics:
            if "solverElapsedMs" in result.metrics:
                solver_times.append(float(result.metrics["solverElapsedMs"]))
                solver_candidates.append(float(result.metrics["candidates"]))
            if "vlmParseAttempts" in result.metrics:
                vlm_parse.append(float(result.metrics["vlmParseAttempts"]))
                vlm_statuses[str(result.metrics["vlmStatus"])] += 1
                vlm_unconfirmed += int(result.metrics["vlmUnconfirmed"])
                vlm_confirmed += int(result.metrics["vlmConfirmed"])
                vlm_unknown += int(result.metrics["vlmUnknownIds"])
        if not result.passed:
            failures.append({"id": result.case_id, "message": result.message})

    passed = sum(1 for result in results if result.passed)
    by_kind: dict[str, dict[str, int]] = {}
    for case, result in zip(cases, results, strict=True):
        bucket = by_kind.setdefault(case.kind, {"total": 0, "passed": 0})
        bucket["total"] += 1
        if result.passed:
            bucket["passed"] += 1

    false_positive_rate = (
        vlm_unconfirmed / (vlm_confirmed + vlm_unconfirmed)
        if (vlm_confirmed + vlm_unconfirmed) > 0
        else 0.0
    )
    report = {
        "milestone": "M4",
        "kind": "shared-eval-table",
        "recordedAt": args.today,
        "gitSha": git_sha(),
        "environment": {
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "server": "FastAPI + uvicorn single worker, SQLite WAL",
            "solverPool": "n/a (eval runner calls solve_layout directly)",
        },
        "models": {
            "llm": {
                "mode": "scripted",
                "model": os.environ.get("LLM_MODEL", "scripted"),
            },
            "vlm": {
                "mode": "scripted",
                "model": os.environ.get("VLM_MODEL", "scripted-vlm"),
            },
        },
        "totals": {
            "cases": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "passRate": round(passed / len(results), 4) if results else 0.0,
            "byKind": by_kind,
        },
        "metrics": {
            "caseWallClockMs": {
                "p50": percentile(durations, 0.5),
                "p95": percentile(durations, 0.95),
                "max": round(max(durations), 3) if durations else None,
            },
            "solver": {
                "runs": len(solver_times),
                "p50Ms": percentile(solver_times, 0.5),
                "p95Ms": percentile(solver_times, 0.95),
                "maxMs": round(max(solver_times), 3) if solver_times else None,
                "avgCandidates": round(sum(solver_candidates) / len(solver_candidates), 1)
                if solver_candidates
                else None,
            },
            "vlm": {
                "runs": len(vlm_parse),
                "statuses": dict(vlm_statuses),
                "jsonParseRate": round(
                    1.0 - vlm_statuses.get("invalid_json", 0) / len(vlm_parse), 4
                )
                if vlm_parse
                else None,
                "confirmedIssues": vlm_confirmed,
                "unconfirmedIssues": vlm_unconfirmed,
                "unknownIds": vlm_unknown,
                "falsePositiveRate": round(false_positive_rate, 4),
                "note": "Scripted VLM; small sample. Unknown labels are excluded from the confirmed/unconfirmed ratio.",
            },
        },
        "failures": failures,
        "caseKinds": dict(kinds),
    }

    report_path = (
        args.report
        if args.report is not None and args.report.is_absolute()
        else REPO_ROOT / (args.report or Path("eval") / "reports" / f"{args.today}-m4-eval.json")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"cases: {passed}/{len(results)} passed")
    for failure in failures:
        print(f"FAIL {failure['id']}: {failure['message']}")
    print(f"report: {report_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())