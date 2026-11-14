"""Entry assist bot for the VIP Inspector Portal."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import assistant, resume
from .auth import LoginError, ensure_logged_in
from .browser import BROWSER_GONE, build_driver
from .config import ConfigError, Settings
from .jurisdictions import DEFAULT_CSV, Jurisdiction, find_start, load_jurisdictions

log = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bot",
        description="Sign in to the VIP Inspector Portal and step through jurisdiction admin pages.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help="CSV with Id, DisplayName, Code columns (default: todo.csv)",
    )
    parser.add_argument(
        "--start",
        metavar="N|CODE|ID",
        help="start at this 1-based row number, jurisdiction Code (e.g. PALM) or GUID "
        "(overrides the saved progress)",
    )
    parser.add_argument(
        "--row",
        type=int,
        metavar="N",
        help="begin at this 1-based Inspection Types row of the first jurisdiction "
        "(overrides the saved progress)",
    )
    parser.add_argument(
        "--paste-type",
        action="store_true",
        help="paste each row's INSPECTION TYPE text into 'Associated Inspection Master' "
        "when its modal opens (default: leave the field alone)",
    )
    parser.add_argument(
        "--clear-resume",
        action="store_true",
        help="delete the saved progress file (.resume.json) and exit, so the next run starts over",
    )
    return parser.parse_args(argv)


def _choose_start(args: argparse.Namespace, jurisdictions: list[Jurisdiction]) -> tuple[int, int] | None:
    """(0-based jurisdiction, 0-based row) to begin at, or None if the saved progress says all done.

    An explicit --start / --row wins; otherwise a saved progress file is used; otherwise the top.
    """
    if args.row is not None and args.row < 1:
        raise ConfigError("--row must be 1 or more")

    if args.start is not None or args.row is not None:
        if resume.load() is not None:
            log.info("--start/--row given, so ignoring the saved progress")
        return find_start(jurisdictions, args.start), (args.row or 1) - 1

    progress = resume.load()
    if progress is None:
        return 0, 0
    position = resume.resolve(progress, jurisdictions)
    if position is None:
        resume.clear()
        print(
            f"The saved progress is past the last jurisdiction (it says {progress.jurisdiction}, "
            f"and there are {len(jurisdictions)}), so everything has been finished.\n"
            "Removed the resume file. Run `uv run bot` again to start over from the beginning."
        )
        return None
    index, row = position
    log.info(
        "Resuming: last completed was jurisdiction %d (%s), row %d - continuing at row %d. "
        "(`uv run bot --clear-resume` to start over)",
        progress.jurisdiction, jurisdictions[index].code, progress.row, progress.row + 1,
    )
    return position


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    # selenium's own logging is noisy at INFO.
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    args = _parse_args(argv)

    if args.clear_resume:  # needs no login, config or browser
        print("Removed the resume file." if resume.clear() else "There was no resume file.")
        return 0

    # Validate config, the CSV and the saved progress before opening a browser, so mistakes fail fast.
    try:
        settings = Settings.from_env()
        jurisdictions = load_jurisdictions(args.csv)
        position = _choose_start(args, jurisdictions)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    if position is None:  # saved progress said everything was already finished
        return 0
    start, start_row = position

    driver = build_driver(settings)
    try:
        ensure_logged_in(driver, settings)
        assistant.run(driver, settings, jurisdictions, start, start_row, paste_type=args.paste_type)
    except LoginError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Progress is saved - run `uv run bot` to continue.")
        return 130
    except BROWSER_GONE as exc:
        log.error("Lost contact with the browser (%s)", type(exc).__name__)
        return 1
    finally:
        try:
            driver.quit()
        except Exception:  # browser may already be gone (window closed by hand)
            pass
    return 0
