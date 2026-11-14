"""Saved progress, so the next run picks up where the last one stopped.

The file (``.resume.json`` in the project folder) records the last COMPLETED
position, both numbers 1-based:

    {"jurisdiction": 3, "row": 5, "code": "VOLU"}

meaning "jurisdiction 3 (VOLU) is done through its Inspection Types row 5".
``row`` 0 means no row of that jurisdiction is done yet. The bot rewrites it
after every completed row, so quitting, a crash or a closed browser all leave
it current. After the final jurisdiction it points one past the end, which the
next run reports as "finished" before clearing it.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import PROJECT_ROOT, ConfigError
from .jurisdictions import Jurisdiction

RESUME_FILE = PROJECT_ROOT / ".resume.json"
_HINT = "Delete it with `uv run bot --clear-resume`."


@dataclass(frozen=True)
class Progress:
    jurisdiction: int  # 1-based: the jurisdiction last worked on
    row: int  # 1-based: last Inspection Types row completed in it (0 = none yet)
    code: str = ""  # its Code, to catch todo.csv changing underneath the file


def load(path: Path | None = None) -> Progress | None:
    """The saved progress, or None if there is no resume file."""
    path = path or RESUME_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigError(f"Can't read {path.name} ({exc}). {_HINT}") from None
    try:
        data = json.loads(raw)
        jurisdiction, row, code = data["jurisdiction"], data["row"], data.get("code", "")
        if not (isinstance(jurisdiction, int) and isinstance(row, int) and isinstance(code, str)):
            raise TypeError("jurisdiction and row must be whole numbers")
        if jurisdiction < 1 or row < 0:
            raise ValueError("jurisdiction must be 1 or more and row 0 or more")
    except (ValueError, TypeError, KeyError) as exc:
        raise ConfigError(f"{path.name} isn't a valid resume file ({exc!r}). {_HINT}") from None
    return Progress(jurisdiction, row, code)


def save(progress: Progress, path: Path | None = None) -> None:
    """Write atomically, so a crash mid-write can't leave a half-written file."""
    path = path or RESUME_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(progress)) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def clear(path: Path | None = None) -> bool:
    """Delete the resume file. True if there was one."""
    try:
        (path or RESUME_FILE).unlink()
    except FileNotFoundError:
        return False
    return True


def resolve(progress: Progress, items: list[Jurisdiction]) -> tuple[int, int] | None:
    """(0-based jurisdiction index, 0-based row to start at) - or None if the saved
    position is past the last jurisdiction, i.e. everything was finished."""
    if progress.jurisdiction > len(items):
        return None
    item = items[progress.jurisdiction - 1]
    if progress.code and progress.code.lower() != item.code.lower():
        raise ConfigError(
            f"The resume file says jurisdiction {progress.jurisdiction} is {progress.code}, but "
            f"that row of the CSV is now {item.code}, so the list has changed. {_HINT}"
        )
    # `row` rows are done, so the next one (0-based index == row) is where to start.
    return progress.jurisdiction - 1, progress.row
