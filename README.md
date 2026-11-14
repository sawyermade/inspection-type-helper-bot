# Inspection type helper bot

Selenium + BeautifulSoup bot for the VIP Inspector Portal
(`https://team.videoinspectionpro.com`). It opens Chrome and signs in automatically.

## Setup

### 1. Install Google Chrome

The bot drives Chrome. You don't need to install chromedriver: Selenium downloads the
matching one on first run.

- **macOS / Windows:** install Chrome from <https://www.google.com/chrome/>.
- **Linux:** install the `google-chrome-stable` `.deb` / `.rpm` from the same page. On a
  machine with no display, set `HEADLESS=true` in `.env` (see below).

### 2. Install uv

[uv](https://docs.astral.sh/uv/) manages Python and the dependencies, so you don't install
either yourself.

**macOS (Homebrew):**

```bash
brew install uv
```

**macOS or Linux (official installer):**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Or, if you use a Windows package manager:

```powershell
winget install --id=astral-sh.uv -e
```

```powershell
scoop install main/uv
```

Then open a new terminal window so `uv` is on your `PATH` (on macOS/Linux you can instead run
`source $HOME/.local/bin/env`), and check it works:

```bash
uv --version
```

Any one method is fine; pick one. To update later, use the same tool you installed with:
`brew upgrade uv` (Homebrew), `winget upgrade --id=astral-sh.uv` (winget), `scoop update uv`
(Scoop), or `uv self update` if you used the official installer script. uv turns `self update`
off for installs from a package manager, so use that manager's own upgrade command instead.

### 3. Get the project

Clone it (or `git pull` for the latest) and move into the folder:

```bash
cd inspection_type_helper_bot
```

There is no separate install step. `uv run bot` creates the virtual environment and installs
the pinned dependencies from `uv.lock` the first time (`uv sync` does the same without running
anything, if you prefer).

**You don't need to install Python** (macOS, Linux and Windows alike). The project needs Python
3.13 or newer; uv uses one it finds on the machine and otherwise downloads one automatically.
The first run on a machine without it needs internet access. Downloaded Pythons live in uv's own
folder (`uv python dir` prints it; on Windows that is `%APPDATA%\uv\data\python`) and don't touch
any system Python. To fetch it ahead of time, run `uv python install 3.13`.

### 4. Create `.env` and add the admin login

`.env` holds the portal login and is gitignored, so it is never committed. Copy the template:

```bash
cp .env.example .env
```

On Windows (PowerShell) the copy command is:

```powershell
Copy-Item .env.example .env
```

Open `.env` in any editor (on Windows, `notepad .env` works) and fill in the admin username and
password:

```
VIP_USERNAME=your-admin-username
VIP_PASSWORD='your-admin-password'
```

- Leave the rest of the file as it is; the other lines are optional (see [Config](#config-env-gitignored)).
- Put the password in **single quotes**. Characters like `!`, `@`, `#` and `$` are then taken
  literally. Don't put spaces around `=`.
- Optional but sensible on macOS/Linux, since the file holds a password, make it readable only
  by you:

  ```bash
  chmod 600 .env
  ```

Don't paste the real credentials into `.env.example`, chat, or a commit. That file is the
template that gets committed.

## Run

```bash
uv run bot
```

First run signs in through the identity provider (skipping the passkey prompt);
later runs reuse the saved session in `.chrome-profile/` and skip the form.

After login it goes to `/admin`, then for each row of `todo.csv` opens
`/admin/jurisdictions/<Id>` and works through that page's **Inspection Types** list:

1. reads the INSPECTION TYPE text of a row and clicks its **Edit** button,
2. pastes that text into **Associated Inspection Master** in the modal,
3. waits for you to choose the rest and **Save** (the bot never clicks Save or Delete),
4. when the modal closes, does the next row; after the last row it opens the next
   jurisdiction on its own.

Press **Ctrl-C** while it is waiting for you to get a pause menu (it only pauses the bot;
chromedriver runs in its own session, so the browser keeps going):

| Key | Action |
| --- | --- |
| Enter | keep waiting |
| `n` | skip the rest of this jurisdiction and go to the next |
| `d` | save the page's HTML + screenshot to `dumps/` |
| `q` | quit, and print the exact command to resume from that row |

```bash
uv run bot --start PALM            # begin at a Code, a GUID, or a 1-based row number
uv run bot --start PALM --row 12   # ...and at the 12th Inspection Types row of that jurisdiction
uv run bot --csv other.csv         # use a different file (columns: Id, DisplayName, Code)
```

If the list or modal isn't where the bot expects, it reports the problem, saves the page to
`dumps/` (gitignored) and offers retry / next jurisdiction / quit instead of carrying on.

## Config (`.env`, gitignored)

| Variable | Purpose |
| --- | --- |
| `VIP_USERNAME`, `VIP_PASSWORD` | Portal login (quote the password with single quotes) |
| `VIP_BASE_URL` | Defaults to the portal URL |
| `HEADLESS` | `true` to hide the browser window |
| `CHROMEDRIVER_PATH` | Optional. Unset = Selenium Manager fetches a matching chromedriver |
| `CHROME_PROFILE_DIR` | Optional. Where the persistent session lives |
| `LOGIN_TIMEOUT` | Seconds to wait for login to finish (default 45) |
| `VIP_IDP_HOST` | Only host the password may be typed into (default `identity.sjursolutions.com`) |

## Layout

- `config.py` - settings from `.env`
- `browser.py` - Chrome/chromedriver setup with a persistent profile
- `auth.py` - login state machine (BeautifulSoup page-state detection)
- `jurisdictions.py` - reads `todo.csv`, builds each jurisdiction's URL
- `inspection_types.py` - finds the Inspection Types list, drives the Edit modal
- `assistant.py` - loops over jurisdictions, handles problems and the resume hints
