"""Per-jurisdiction automation for the "Inspection Types" list.

For each row of the list on a jurisdiction page the bot:

1. reads the displayed text of the INSPECTION TYPE cell,
2. clicks Edit in that row's ACTIONS cell, which opens a modal,
3. pastes that text into the modal's "Associated Inspection Master" field,
4. waits - as long as it takes - for you to pick the rest of the options and
   Save, i.e. until that modal goes away,
5. moves on to the next row.

The bot never clicks Save (or Delete) itself.

The list is a CSS grid of divs, not a <table>, so it's found structurally: the
header row is the element whose children read "Inspection Type ... Actions", and
data rows are the elements after it with the same number of cells and an Edit
button in the Actions cell. Nothing depends on Tailwind class names.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement

from .config import PROJECT_ROOT

log = logging.getLogger(__name__)

DUMP_DIR = PROJECT_ROOT / "dumps"

GRID_TIMEOUT = 20.0  # wait for the list to appear after navigating
ROWS_TIMEOUT = 8.0  # the header can render before its rows arrive
MODAL_TIMEOUT = 10.0  # wait for the Edit modal (and its field) to appear
POLL = 0.35

FIELD_PHRASE = "associated inspection master"
SELECT_ALL = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
_LOWER = "translate({}, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"

# Walk up from the field to the position:fixed overlay that is the modal.
# (This modal isn't role="dialog", and the page also has a permanent empty
# full-screen fixed overlay, so "is any overlay visible" would never be false.)
_MODAL_ROOT_JS = """
let el = arguments[0];
while (el && el !== document.body) {
  if (getComputedStyle(el).position === 'fixed') return el;
  el = el.parentElement;
}
return arguments[0];
"""


class AutomationError(RuntimeError):
    """Something on the page wasn't where we expected. ``row`` is 0-based."""

    def __init__(self, message: str, row: int = 0):
        super().__init__(message)
        self.row = row


class SkipJurisdiction(Exception):
    """User asked to abandon the rest of this jurisdiction."""


class StopRun(Exception):
    """User asked to quit. ``row`` is the 0-based row they were on."""

    def __init__(self, row: int = 0):
        super().__init__("stop")
        self.row = row


# --------------------------------------------------------------------------
# Finding the list (BeautifulSoup does the analysis, Selenium does the acting)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GridLayout:
    cell_count: int  # direct child cells in the header row (and in each data row)
    type_col: int  # 0-based index of the INSPECTION TYPE cell
    actions_col: int  # 0-based index of the ACTIONS cell
    row_count: int  # data rows currently in the DOM


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _is_type_header(text: str) -> bool:
    return re.match(r"inspection type(?!s)", text) is not None


def _is_actions_header(text: str) -> bool:
    return text.startswith("action")


def _is_edit_label(label: str) -> bool:
    return label.startswith("edit")


def _soup_is_row(el, cell_count: int, actions_col: int) -> bool:
    kids = el.find_all(True, recursive=False)
    if len(kids) != cell_count:
        return False
    return any(
        _is_edit_label(_norm(b.get_text(" ", strip=True) or b.get("aria-label")))
        for b in kids[actions_col].find_all("button")
    )


def analyze_grid(html: str) -> GridLayout | None:
    """Find the "Inspection Type ... Actions" header row and count the rows under it."""
    if "inspection type" not in html.lower():
        return None
    soup = BeautifulSoup(html, "html.parser")
    for header in soup.find_all(True):
        kids = header.find_all(True, recursive=False)
        if len(kids) < 3:
            continue
        texts = [_norm(k.get_text(" ", strip=True)) for k in kids]
        type_col = next((i for i, t in enumerate(texts) if _is_type_header(t)), None)
        actions_col = next((i for i, t in enumerate(texts) if _is_actions_header(t)), None)
        if type_col is None or actions_col is None:
            continue

        n = len(kids)
        rows = [e for e in header.find_all_next(True) if _soup_is_row(e, n, actions_col)]
        # Keep only innermost matches (a wrapper that happens to hold exactly n rows
        # would otherwise look like a row itself).
        ids = {id(r) for r in rows}
        rows = [r for r in rows if not any(id(d) in ids for d in r.find_all(True))]
        return GridLayout(n, type_col, actions_col, len(rows))
    return None


def wait_for_grid(driver: WebDriver, timeout: float = GRID_TIMEOUT) -> GridLayout:
    """Wait for the header row, then for rows (an empty list is accepted after a grace period)."""
    deadline = time.monotonic() + timeout
    layout = None
    while layout is None and time.monotonic() < deadline:
        layout = analyze_grid(driver.page_source)
        if layout is None:
            time.sleep(0.5)
    if layout is None:
        raise AutomationError("Couldn't find the Inspection Types list (an 'Inspection Type ... Actions' header row)")

    rows_deadline = time.monotonic() + ROWS_TIMEOUT
    while layout.row_count == 0 and time.monotonic() < rows_deadline:
        time.sleep(0.5)
        layout = analyze_grid(driver.page_source) or layout
    return layout


def _header_xpath(layout: GridLayout) -> str:
    t, a = layout.type_col + 1, layout.actions_col + 1
    text = _LOWER.format("normalize-space(.)")
    return (
        f"//*[count(*)={layout.cell_count}]"
        f"[*[{t}][starts-with({text}, 'inspection type')]]"
        f"[*[{a}][starts-with({text}, 'action')]]"
    )


def _row_xpath(layout: GridLayout) -> str:
    n, a = layout.cell_count, layout.actions_col + 1
    candidate = f"*[count(*)={n}][*[{a}]//button]"
    # innermost only: a row never contains another matching element
    return f"following::{candidate}[not(.//{candidate})]"


def _find_edit_button(cell: WebElement) -> WebElement | None:
    for el in cell.find_elements(By.CSS_SELECTOR, "button, a, [role='button']"):
        label = _norm(" ".join(filter(None, (el.text, el.get_attribute("aria-label"), el.get_attribute("title")))))
        if _is_edit_label(label):
            return el
    return None


def _data_rows(driver: WebDriver, layout: GridLayout) -> list[WebElement]:
    headers = driver.find_elements(By.XPATH, _header_xpath(layout))
    if not headers:
        raise AutomationError("The Inspection Types header row disappeared")
    rows = []
    for row in headers[0].find_elements(By.XPATH, _row_xpath(layout)):
        cells = row.find_elements(By.XPATH, "./*")
        if len(cells) == layout.cell_count and _find_edit_button(cells[layout.actions_col]) is not None:
            rows.append(row)
    return rows


def _cell_text(cell: WebElement) -> str:
    shown = " ".join(cell.text.split())
    # Long names are visually truncated with an ellipsis; the title attribute
    # (when present) carries the whole thing.
    full = " ".join((cell.get_attribute("title") or "").split())
    return full if len(full) > len(shown) else shown


def _read_row(driver: WebDriver, layout: GridLayout, index: int) -> tuple[str, WebElement]:
    """(displayed inspection-type text, its Edit button) for row ``index``."""
    for attempt in range(3):
        try:
            rows = _data_rows(driver, layout)
            if index >= len(rows):
                raise AutomationError(f"Row {index + 1} is no longer in the list", row=index)
            cells = rows[index].find_elements(By.XPATH, "./*")
            text = _cell_text(cells[layout.type_col])
            button = _find_edit_button(cells[layout.actions_col])
            break
        except StaleElementReferenceException:  # list re-rendered while we read it
            if attempt == 2:
                raise
            time.sleep(0.3)
    if not text:
        raise AutomationError(f"Row {index + 1} has an empty INSPECTION TYPE cell", row=index)
    if button is None:
        raise AutomationError(f"Row {index + 1} ({text!r}) has no Edit button", row=index)
    return text, button


# --------------------------------------------------------------------------
# The modal
# --------------------------------------------------------------------------


def _click(driver: WebDriver, element: WebElement) -> None:
    try:
        element.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", element)  # something overlaps it


def _ci_contains(expr: str, phrase: str) -> str:
    return f"contains({_LOWER.format(expr)}, '{phrase}')"


def _find_master_field(driver: WebDriver) -> WebElement | None:
    """The input under the 'Associated Inspection Master' label."""
    for label in driver.find_elements(By.XPATH, f"//label[{_ci_contains('normalize-space(.)', FIELD_PHRASE)}]"):
        if not label.is_displayed():
            continue
        target = label.get_attribute("for")
        if target:
            found = driver.find_elements(By.XPATH, f"//*[@id='{target}']")
            if found:
                return found[0]
        inside = label.find_elements(By.CSS_SELECTOR, "input:not([type='hidden']), textarea")
        if inside:
            return inside[0]
        # Label and input sit in the same wrapper: the next input after the label.
        following = label.find_elements(
            By.XPATH, "following::*[self::input or self::textarea][not(@type='hidden')][1]"
        )
        if following and following[0].is_displayed():
            return following[0]
    return None


def _wait_for_master_field(driver: WebDriver, row: int) -> tuple[WebElement, WebElement]:
    """(field, modal overlay it lives in) once the Edit modal has opened."""
    deadline = time.monotonic() + MODAL_TIMEOUT
    while time.monotonic() < deadline:
        try:
            field = _find_master_field(driver)
            if field is not None:
                return field, driver.execute_script(_MODAL_ROOT_JS, field)
        except StaleElementReferenceException:
            pass
        time.sleep(0.25)
    raise AutomationError("The Edit modal didn't open, or it has no 'Associated Inspection Master' field", row=row)


def _paste(field: WebElement, text: str, row: int) -> None:
    deadline = time.monotonic() + 5
    while True:
        try:
            field.click()
            break
        except WebDriverException:  # modal still animating in
            if time.monotonic() > deadline:
                raise AutomationError("Couldn't focus the 'Associated Inspection Master' field", row=row) from None
            time.sleep(0.25)
    field.send_keys(SELECT_ALL + "a")  # replace whatever is already there
    field.send_keys(text)
    now = field.get_attribute("value") or ""
    if now != text:
        log.warning("Field reads %r after typing %r", now, text)


def _is_open(modal: WebElement) -> bool:
    try:
        return modal.is_displayed()
    except StaleElementReferenceException:  # removed from the page = closed
        return False


def _wait_until_closed(modal: WebElement) -> None:
    """Block until the modal is gone (two checks in a row, to ride out transitions)."""
    quiet = 0
    while quiet < 2:
        quiet = quiet + 1 if not _is_open(modal) else 0
        time.sleep(POLL)


# --------------------------------------------------------------------------
# Pause menu and diagnostics
# --------------------------------------------------------------------------


def dump_page(driver: WebDriver, label: str) -> Path:
    """Save the page's current HTML (and a screenshot) so a mismatch can be diagnosed."""
    DUMP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = DUMP_DIR / f"{stamp}-{re.sub(r'[^A-Za-z0-9_-]+', '-', label).strip('-')}.html"
    path.write_text(driver.page_source, encoding="utf-8")
    try:
        driver.save_screenshot(str(path.with_suffix(".png")))
    except WebDriverException:
        pass
    return path


def _pause_menu(driver: WebDriver, label: str, row: int) -> None:
    """Shown on Ctrl-C while waiting for you. Returns to keep waiting, or raises."""
    print()
    while True:
        try:
            choice = input(
                "Paused. Enter=keep waiting  n=skip rest of this jurisdiction  d=save page dump  q=quit > "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            raise StopRun(row) from None
        if choice == "":
            return
        if choice == "n":
            raise SkipJurisdiction
        if choice == "q":
            raise StopRun(row)
        if choice == "d":
            print(f"  saved {dump_page(driver, label)}")


def _wait_for_save(driver: WebDriver, modal: WebElement, label: str, row: int) -> None:
    while True:
        try:
            _wait_until_closed(modal)
            return
        except KeyboardInterrupt:
            _pause_menu(driver, label, row)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def process_jurisdiction(driver: WebDriver, label: str, start_row: int = 0) -> int:
    """Run every Inspection Types row on the current page. Returns rows handled."""
    layout = wait_for_grid(driver)
    total = layout.row_count
    if total == 0:
        print("  no inspection types listed - nothing to do")
        return 0
    if start_row >= total:
        raise AutomationError(f"--row {start_row + 1} is past the last row ({total})", row=0)

    done = 0
    row = start_row
    while row < total:
        layout = wait_for_grid(driver)  # re-read: the list re-renders after each Save
        text, edit_button = _read_row(driver, layout, row)
        print(f"  row {row + 1}/{total}: {text}")

        _click(driver, edit_button)
        field, modal = _wait_for_master_field(driver, row)
        _paste(field, text, row)
        print("     pasted - choose the rest and Save (Ctrl-C = pause menu)")

        _wait_for_save(driver, modal, label, row)
        done += 1
        row += 1
    return done
