"""Jurisdiction list (todo.csv) and the admin page URL for each one."""

from __future__ import annotations

import csv
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import PROJECT_ROOT, ConfigError, Settings

DEFAULT_CSV = PROJECT_ROOT / "todo.csv"
JURISDICTION_PATH = "/admin/jurisdictions"
_REQUIRED_COLUMNS = {"Id", "DisplayName", "Code"}


@dataclass(frozen=True)
class Jurisdiction:
    id: str
    name: str
    code: str

    def url(self, settings: Settings) -> str:
        return f"{settings.base_url}{JURISDICTION_PATH}/{self.id}"


def load_jurisdictions(path: Path = DEFAULT_CSV) -> list[Jurisdiction]:
    """Read Id / DisplayName / Code rows, validating every Id is a GUID."""
    if not path.is_file():
        raise ConfigError(f"Jurisdiction CSV not found: {path}")

    items: list[Jurisdiction] = []
    # utf-8-sig: the file starts with a byte-order mark that would otherwise
    # corrupt the first column name ("﻿Id").
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ConfigError(f"{path.name} is missing column(s): {', '.join(sorted(missing))}")
        for lineno, row in enumerate(reader, start=2):
            raw = (row["Id"] or "").strip()
            if not raw:
                continue
            try:
                guid = str(uuid.UUID(raw))
            except ValueError:
                raise ConfigError(f"{path.name} line {lineno}: {raw!r} is not a valid GUID") from None
            items.append(
                Jurisdiction(id=guid, name=(row["DisplayName"] or "").strip(), code=(row["Code"] or "").strip())
            )

    if not items:
        raise ConfigError(f"{path.name} has no rows")
    return items


def find_start(items: list[Jurisdiction], spec: str | None) -> int:
    """Index to start from. ``spec`` is a 1-based row number, a Code, or a GUID."""
    if not spec:
        return 0
    spec = spec.strip()
    if spec.isdigit():
        number = int(spec)
        if 1 <= number <= len(items):
            return number - 1
        raise ConfigError(f"--start {number} is out of range (1-{len(items)})")
    wanted = spec.lower()
    for index, item in enumerate(items):
        if wanted in (item.id.lower(), item.code.lower()):
            return index
    raise ConfigError(f"--start {spec!r} doesn't match any row number, Code or Id")
