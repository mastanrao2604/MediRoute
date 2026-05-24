#!/usr/bin/env python3
"""CLI: generate operational report from JUnit XML."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.helpers.report import generate_operational_report


def main() -> int:
    junit = Path(sys.argv[1])
    out = Path(sys.argv[2])
    failed = len(sys.argv) > 3 and sys.argv[3] == "1"
    generate_operational_report(junit, out, failed=failed)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
