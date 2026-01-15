from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyze import analyze_demos


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="titan-hltv")
    p.add_argument(
        "demos",
        nargs="+",
        help="Path(s) to CS2 .dem file(s).",
    )
    p.add_argument(
        "--debug-schema",
        action="store_true",
        help="Include event schema samples in output (for parser debugging).",
    )
    p.add_argument(
        "--max-rounds",
        type=int,
        default=0,
        help="If >0, analyze only first N rounds (faster iteration).",
    )
    p.add_argument(
        "--progress",
        action="store_true",
        help="Print progress to stderr while parsing.",
    )
    p.add_argument(
        "--out",
        default="-",
        help="Output path. Use '-' for stdout. (default: -)",
    )
    p.add_argument(
        "--format",
        choices=["json"],
        default="json",
        help="Output format. (default: json)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(sys.argv[1:] if argv is None else argv)
    demo_paths = [Path(p).expanduser().resolve() for p in ns.demos]

    try:
        report = analyze_demos(
            demo_paths,
            debug_schema=bool(ns.debug_schema),
            max_rounds=int(ns.max_rounds or 0),
            progress=bool(ns.progress),
        )
    except ModuleNotFoundError as e:
        # Typical missing dependency is demoparser2 in Codespaces first run.
        print(
            "Missing dependency. Install with:\n"
            "  python -m pip install -e .\n\n"
            f"Original error: {e}",
            file=sys.stderr,
        )
        return 2

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if ns.out == "-":
        print(payload)
        return 0

    out_path = Path(ns.out).expanduser().resolve()
    out_path.write_text(payload, encoding="utf-8")
    return 0

