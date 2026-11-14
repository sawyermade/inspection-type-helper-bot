"""The entry-assist logic. Runs once the bot is logged in.

For every jurisdiction in the CSV: open its admin page, run through its
Inspection Types rows (see ``inspection_types``), then move straight on to the
next jurisdiction.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.remote.webdriver import WebDriver

from .auth import ensure_logged_in
from .browser import BROWSER_GONE
from .config import Settings
from .inspection_types import (
    AutomationError,
    SkipJurisdiction,
    StopRun,
    dump_page,
    process_jurisdiction,
)
from .jurisdictions import Jurisdiction

log = logging.getLogger(__name__)


def go_to_start_page(driver: WebDriver, settings: Settings) -> None:
    """Make sure we're on the start page (default /admin) after signing in."""
    if urlparse(driver.current_url).path.startswith(settings.start_path):
        return
    log.info("Going to %s", settings.start_path)
    driver.get(settings.base_url + settings.start_path)


def _on_portal_page(driver: WebDriver, settings: Settings) -> bool:
    """False if we got bounced to the sign-in flow (e.g. the session expired)."""
    url = urlparse(driver.current_url)
    return url.netloc == urlparse(settings.base_url).netloc and not url.path.startswith("/auth")


def open_jurisdiction(driver: WebDriver, settings: Settings, item: Jurisdiction) -> None:
    url = item.url(settings)
    driver.get(url)
    if not _on_portal_page(driver, settings):
        log.info("Session ended; signing in again")
        ensure_logged_in(driver, settings)
        driver.get(url)


def _failure_menu() -> str:
    """After a problem: 'r' (retry this jurisdiction), 'n' (next) or 'q' (quit)."""
    while True:
        try:
            answer = input("  r=retry this jurisdiction  n=next jurisdiction  q=quit > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "q"
        if answer in ("r", "n", "q"):
            return answer


def _resume_hint(item: Jurisdiction, row: int) -> str:
    hint = f"uv run bot --start {item.code}"
    return hint + (f" --row {row + 1}" if row else "")


def run(
    driver: WebDriver,
    settings: Settings,
    jurisdictions: list[Jurisdiction],
    start: int = 0,
    start_row: int = 0,
) -> None:
    go_to_start_page(driver, settings)

    total = len(jurisdictions)
    index, row = start, start_row
    print(f"\n{total} jurisdictions loaded, starting at {index + 1}. Ctrl-C while waiting opens a pause menu.")

    while index < total:
        item = jurisdictions[index]
        label = f"[{index + 1}/{total}] {item.name} ({item.code})"
        print(f"\n{label}")
        try:
            open_jurisdiction(driver, settings, item)
            done = process_jurisdiction(driver, label, start_row=row)
            print(f"  finished - {done} row(s)")
        except SkipJurisdiction:
            print("  skipped the rest of this jurisdiction")
        except StopRun as stop:
            print(f"\nStopped. Resume with: {_resume_hint(item, stop.row)}")
            return
        except AutomationError as exc:
            print(f"  PROBLEM: {exc}")
            try:
                print(f"  page saved to {dump_page(driver, item.code)}")
            except WebDriverException:
                pass
            choice = _failure_menu()
            if choice == "r":
                row = exc.row
                continue
            if choice == "q":
                print(f"\nStopped. Resume with: {_resume_hint(item, exc.row)}")
                return
        except BROWSER_GONE as exc:
            # The browser window was closed, or chromedriver died.
            log.error("Lost contact with the browser (%s) at %s", type(exc).__name__, label)
            print(f"Resume with: {_resume_hint(item, row)}")
            return

        row = 0
        index += 1

    print(f"\nAll {total} jurisdictions done.")
