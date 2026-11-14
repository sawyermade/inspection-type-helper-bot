"""Entry assist bot for the VIP Inspector Portal."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import assistant
from .auth import LoginError, ensure_logged_in
from .browser import BROWSER_GONE, build_driver
from .config import ConfigError, Settings
from .jurisdictions import DEFAULT_CSV, find_start, load_jurisdictions

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
        help="start at this 1-based row number, jurisdiction Code (e.g. PALM) or GUID",
    )
    parser.add_argument(
        "--row",
        type=int,
        default=1,
        metavar="N",
        help="with --start: begin at this 1-based Inspection Types row of the first jurisdiction",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    # selenium's own logging is noisy at INFO.
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    args = _parse_args(argv)

    # Validate config and the CSV before opening a browser, so mistakes fail fast.
    try:
        settings = Settings.from_env()
        jurisdictions = load_jurisdictions(args.csv)
        start = find_start(jurisdictions, args.start)
        if args.row < 1:
            raise ConfigError("--row must be 1 or more")
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    driver = build_driver(settings)
    try:
        ensure_logged_in(driver, settings)
        assistant.run(driver, settings, jurisdictions, start, args.row - 1)
    except LoginError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
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
