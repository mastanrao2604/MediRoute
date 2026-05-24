"""Operational report generator from pytest JUnit XML."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def generate_operational_report(
    junit_path: Path,
    out_path: Path,
    *,
    extra_sections: Optional[dict[str, str]] = None,
    failed: bool = False,
) -> str:
    suites: list[dict] = []
    failures: list[dict] = []
    total = errors = failure_count = skipped = 0

    if junit_path.exists():
        root = ET.parse(junit_path).getroot()
        for suite in root.iter("testsuite"):
            s_name = suite.get("name", "unknown")
            s_tests = int(suite.get("tests", 0))
            s_fail = int(suite.get("failures", 0))
            s_err = int(suite.get("errors", 0))
            s_skip = int(suite.get("skipped", 0))
            total += s_tests
            failure_count += s_fail
            errors += s_err
            skipped += s_skip
            suites.append(
                {
                    "name": s_name,
                    "tests": s_tests,
                    "failures": s_fail,
                    "errors": s_err,
                    "skipped": s_skip,
                }
            )
            for case in suite.iter("testcase"):
                for tag in ("failure", "error"):
                    node = case.find(tag)
                    if node is not None:
                        failures.append(
                            {
                                "suite": s_name,
                                "test": case.get("name"),
                                "class": case.get("classname"),
                                "kind": tag,
                                "message": (node.get("message") or "")[:300],
                                "text": (node.text or "")[:500],
                            }
                        )

    passed = total - failure_count - errors - skipped
    status = "FAIL" if failed or failure_count or errors else "PASS"

    lines = [
        "# MediRoute Operational Regression Report",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Result:** {status}",
        "",
        "## Summary",
        "",
        f"| Metric | Count |",
        f"|--------|------:|",
        f"| Total tests | {total} |",
        f"| Passed | {passed} |",
        f"| Failed | {failure_count} |",
        f"| Errors | {errors} |",
        f"| Skipped | {skipped} |",
        "",
    ]

    if failures:
        lines += ["## Failed Tests", ""]
        for f in failures:
            lines.append(f"- **{f['class']}::{f['test']}** ({f['kind']})")
            if f["message"]:
                lines.append(f"  - {f['message']}")
        lines.append("")

    # Categorize failures
    categories = {
        "reconnect": [],
        "stale_state": [],
        "websocket": [],
        "migration": [],
        "serialization": [],
        "lifecycle": [],
        "auth": [],
    }
    for f in failures:
        blob = f"{f['test']} {f['message']} {f['text']}".lower()
        if "reconnect" in blob or "ws_" in blob:
            categories["reconnect"].append(f["test"])
        if "stale" in blob or "ghost" in blob or "reconcile" in blob:
            categories["stale_state"].append(f["test"])
        if "websocket" in blob or "ws" in blob:
            categories["websocket"].append(f["test"])
        if "migration" in blob or "alembic" in blob or "schema" in blob:
            categories["migration"].append(f["test"])
        if "serializ" in blob or "json" in blob:
            categories["serialization"].append(f["test"])
        if "lifecycle" in blob or "confirm" in blob or "no_show" in blob:
            categories["lifecycle"].append(f["test"])
        if "auth" in blob or "role" in blob:
            categories["auth"].append(f["test"])

    lines += ["## Failure Categories", ""]
    for cat, tests in categories.items():
        if tests:
            lines.append(f"### {cat.replace('_', ' ').title()}")
            for t in tests:
                lines.append(f"- {t}")
            lines.append("")

    if extra_sections:
        lines += ["## Additional Notes", ""]
        for title, body in extra_sections.items():
            lines.append(f"### {title}")
            lines.append(body)
            lines.append("")

    lines += ["## Suite Breakdown", ""]
    for s in suites:
        lines.append(
            f"- `{s['name']}`: {s['tests']} tests, "
            f"{s['failures']} failures, {s['errors']} errors, {s['skipped']} skipped"
        )

    content = "\n".join(lines) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return content
