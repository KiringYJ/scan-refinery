"""Command-line interface for inspect, plan, apply, and verify."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from scan_refinery import __version__
from scan_refinery.document import inspect_pdf, write_json
from scan_refinery.workflow import apply_plan, load_plan, make_plan, verify_pdf


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Inspect and align scans while preserving PDF content."
    )
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--verbose", action="store_true", help="write progress to stderr")
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="write inventory, page PNGs, and contact sheets")
    inspect.add_argument("input", type=Path)
    inspect.add_argument(
        "--run-dir", type=Path, required=True, help="new, unused local run directory"
    )
    inspect.add_argument("--dpi", type=int, default=144)
    plan = commands.add_parser("plan", help="measure scan geometry and write an editable JSON plan")
    plan.add_argument("input", type=Path)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--method", choices=("projection", "header-rule"), default="projection")
    plan.add_argument("--align", choices=("none", "center", "anchor"), default="none")
    plan.add_argument("--reference-page", type=int, help="one-based internal alignment reference")
    plan.add_argument("--dpi", type=int, default=144)
    plan.add_argument("--max-angle", type=float, default=2.0)
    plan.add_argument("--min-confidence", type=float, default=0.25)
    apply = commands.add_parser("apply", help="apply a resolved plan to a new PDF and verify it")
    apply.add_argument("input", type=Path)
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify", help="check source content and planned output geometry")
    verify.add_argument("input", type=Path)
    verify.add_argument("output", type=Path)
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--output", dest="report", type=Path, help="write a new report JSON")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    try:
        if args.command == "inspect":
            inventory = inspect_pdf(args.input, args.run_dir, args.dpi)
            result = {"pages": inventory["source"]["page_count"], "inventory": "inventory.json"}
        elif args.command == "plan":
            result = make_plan(
                args.input,
                method=args.method,
                align=args.align,
                reference_page=args.reference_page,
                dpi=args.dpi,
                max_angle=args.max_angle,
                min_confidence=args.min_confidence,
            )
            write_json(args.output, result)
            result = {
                status: sum(page["status"] == status for page in result["pages"])
                for status in ("ready", "skip", "review")
            }
        elif args.command == "apply":
            result = apply_plan(args.input, args.output, load_plan(args.plan))
        else:
            result = verify_pdf(args.input, args.output, load_plan(args.plan))
            if args.report is not None:
                write_json(args.report, result)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get("passed", True) else 2
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
