# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run bot                          # run (creates the venv and installs deps on first use)
uv run bot --start PALM --row 12    # begin at a Code / GUID / 1-based number, and a row
uv run bot --paste-type             # also paste each row's text into "Associated Inspection Master"
uv run bot --clear-resume           # delete .resume.json and exit (no browser, no login)
```

There is no test suite, linter or formatter configured. Credentials come from `.env` (copy `.env.example`; setup is in README.md).

## What it does

A semi-automated entry assistant for the VIP Inspector Portal. It logs in, then for each GUID in `todo.csv` opens `/admin/jurisdictions/<Id>`, and for every row in the **Inspection Types** list clicks **Edit** and waits for the *human* to fill in the modal and Save. It never saves for them. When the modal closes it moves to the next row, then the next jurisdiction.

## Architecture

Flow: `__init__.main` (args, validation, resume position) -> `auth.ensure_logged_in` -> `assistant.run` (jurisdiction loop, failure/pause handling) -> `inspection_types.process_jurisdiction` (row loop).

- **Login** (`auth.py`) is a state machine over page state: authenticated (checked with `fetch('/api/auth/session')`), IdP login, passkey prompt (clicks Skip), portal sign-in, error. The password is only ever typed on `settings.idp_host` (`identity.sjursolutions.com`). Sessions persist in `.chrome-profile/`.
- **Page parsing** (`inspection_types.py`) is the fragile part. The list is a Tailwind **div grid, not a `<table>`**: a 7-cell header row (Inspection Type, Code, Certifications, Master, External, Fee, Actions), with `analyze_grid` locating rows from the header via BeautifulSoup and XPath. Each row has Edit and Delete side by side; `_find_edit_button` requires the label to start with "edit".
- **The modal** is a `div.fixed.inset-0` overlay, not `role=dialog`; Escape doesn't close it. The page also has a permanent empty fixed overlay, so "modal closed" is detected by tracking the Associated Inspection Master field's `position:fixed` ancestor going stale or hidden (`_wait_until_closed`). That field's label has no `for`; its input is a sibling with placeholder "Search inspection types...".
- **Resume** (`resume.py`): `.resume.json` stores the *last completed* 1-based jurisdiction and row plus the jurisdiction Code, written atomically after every row. A saved position past the end means "finished": the file is removed and the bot exits. A Code mismatch against `todo.csv`, or a corrupt file, is refused rather than guessed. `--start`/`--row` override it.
- **Ctrl-C** during a wait opens a pause menu (continue / skip jurisdiction / dump / quit). This only works because chromedriver is started in its own session/process group (`browser.py`); otherwise SIGINT kills it too. `BROWSER_GONE` covers chromedriver or window death so it exits cleanly.

## Rules to keep

- **Never click** page-level destructive buttons: "Reset to Master", "Overwrite with Masters", "+ Add", "Edit jurisdiction name", or any row's Delete. They sit on the same page as Edit. The bot must also never click Save.
- `.env`, `.chrome-profile/`, `.resume.json`, `dumps/` are gitignored. Never put real credentials in `.env.example`.
- Don't run experiments against the live portal's data. Test against a local mock page that copies the structure above. If you must inspect the live page, attach read-only to the running Chrome and never `driver.quit()` the user's browser.
- Windows process-group handling in `browser.py` is untested.
