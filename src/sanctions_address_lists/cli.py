"""Command-line interface for sanctions-address-lists."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TextIO

from sanctions_address_lists.http import DEFAULT_RETRIES, DEFAULT_TIMEOUT, build_client
from sanctions_address_lists.models import SourceResult
from sanctions_address_lists.output import manifest_entry, write_manifest, write_source_outputs
from sanctions_address_lists.sources.base import SourceAdapter
from sanctions_address_lists.sources.registry import ALL_SOURCES, get_source

logger = logging.getLogger("sanctions_address_lists")

_STATUS_ICON = {"success": "✓", "unverified": "~", "unsupported": "-", "failed": "✗"}
_STATUS_COLOR = {"success": "32", "unverified": "33", "unsupported": "90", "failed": "31"}


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    if not verbose:
        # httpx logs a full request/response line -- including any signed
        # redirect URL, which can be extremely long -- at INFO. Keep that (and
        # our own per-match review-signal logs) out of default, human output.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


def _use_color(stream: TextIO, disable: bool) -> bool:
    if disable:
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _colorize(text: str, color_code: str, *, enabled: bool) -> str:
    return f"\033[{color_code}m{text}\033[0m" if enabled else text


def _status_line(result: SourceResult, *, color: bool) -> str:
    icon = _STATUS_ICON.get(result.status, "?")
    line = (
        f"{icon} {result.source_id:<12} {result.status:<11} "
        f"{result.record_count:>6} records  {result.address_count:>4} addresses"
    )
    if result.status == "failed" and result.error:
        line += f"  -- {result.error}"
    return _colorize(line, _STATUS_COLOR.get(result.status, "0"), enabled=color)


def _gates_build(adapter_cls: type[SourceAdapter], result: SourceResult) -> bool:
    """Whether this failed result should fail the run.

    A gating source that was merely unavailable upstream (network error, 5xx
    after all retries) doesn't fail the run: there's nothing to fix on our side,
    and the publish workflow carries its previous files forward.
    """
    return adapter_cls.GATES_BUILD and not result.transient


def _print_summary(
    outcomes: list[tuple[type[SourceAdapter], SourceResult]], *, color: bool
) -> None:
    succeeded = sum(1 for _, result in outcomes if result.status == "success")
    total_addresses = sum(result.address_count for _, result in outcomes)
    failed_ids = [
        result.source_id
        for adapter_cls, result in outcomes
        if result.status == "failed" and _gates_build(adapter_cls, result)
    ]
    unavailable_ids = [
        result.source_id for _, result in outcomes if result.status == "failed" and result.transient
    ]
    summary = f"{succeeded}/{len(outcomes)} sources succeeded, {total_addresses} total addresses"
    if unavailable_ids:
        summary += f" -- UNAVAILABLE (not failing the run): {', '.join(unavailable_ids)}"
    if failed_ids:
        summary += f" -- FAILED: {', '.join(failed_ids)}"
        print(_colorize(summary, _STATUS_COLOR["failed"], enabled=color))
    elif unavailable_ids:
        print(_colorize(summary, _STATUS_COLOR["unverified"], enabled=color))
    else:
        print(_colorize(summary, _STATUS_COLOR["success"], enabled=color))


def _run_command(args: argparse.Namespace) -> int:
    _configure_logging(args.verbose)

    if args.source:
        try:
            adapter_classes = [get_source(source_id) for source_id in args.source]
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        adapter_classes = list(ALL_SOURCES)

    out_dir = Path(args.output_dir)
    manifest_entries: list[dict[str, object]] = []
    outcomes: list[tuple[type[SourceAdapter], SourceResult]] = []
    build_failed = False
    color = _use_color(sys.stdout, args.no_color)
    interactive = sys.stdout.isatty()

    with build_client(timeout=args.timeout) as client:
        for adapter_cls in adapter_classes:
            adapter = adapter_cls()
            if not args.quiet and interactive:
                print(f"  fetching {adapter_cls.SOURCE_ID}...", end="\r", flush=True)

            result = adapter.run(client, retries=args.retries)
            generated_files = write_source_outputs(result, out_dir)
            manifest_entries.append(manifest_entry(result, generated_files))
            outcomes.append((adapter_cls, result))

            if not args.quiet:
                line = _status_line(result, color=color)
                print(f"\033[2K\r{line}" if interactive else line)

            if result.status == "failed" and _gates_build(adapter_cls, result):
                build_failed = True

    write_manifest(manifest_entries, out_dir)

    if not args.quiet:
        _print_summary(outcomes, color=color)

    return 1 if build_failed else 0


def _list_sources_command(_args: argparse.Namespace) -> int:
    for adapter_cls in ALL_SOURCES:
        kind = "gating" if adapter_cls.GATES_BUILD else "non-gating"
        print(
            f"{adapter_cls.SOURCE_ID}\t{adapter_cls.JURISDICTION}\t"
            f"{adapter_cls.SOURCE_URL}\t{kind}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sanctions-address-lists")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="download sources and generate outputs")
    run_parser.add_argument(
        "--source", action="append", help="limit to this source id (repeatable)"
    )
    run_parser.add_argument("--output-dir", default="dist", help="output directory (default: dist)")
    run_parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds"
    )
    run_parser.add_argument(
        "--retries", type=int, default=DEFAULT_RETRIES, help="bounded HTTP retries per request"
    )
    run_parser.add_argument(
        "-v", "--verbose", action="store_true", help="show detailed request and parsing logs"
    )
    run_parser.add_argument(
        "-q", "--quiet", action="store_true", help="suppress per-source status lines and summary"
    )
    run_parser.add_argument(
        "--no-color", action="store_true", help="disable ANSI colors in status output"
    )
    run_parser.set_defaults(func=_run_command)

    list_parser = subparsers.add_parser("list-sources", help="list registered sources")
    list_parser.set_defaults(func=_list_sources_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result
