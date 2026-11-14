"""Auto-login for the VIP Inspector Portal.

How the site authenticates (Auth.js + OpenID Connect):

1. ``team.videoinspectionpro.com`` redirects to ``/auth/signin``.
2. That page immediately forwards the browser to the identity provider
   (``identity.sjursolutions.com/Account/Login``), a server-rendered form with
   ``Username`` / ``Password`` fields.
3. After a correct password the provider may offer to set up a passkey; the bot
   clicks "Skip" on that page.
4. The provider then redirects back to the portal, which sets a session cookie.

Because the Chrome profile is persistent, steps 2-3 are usually skipped on later
runs: the portal already has a session, and ``ensure_logged_in`` returns as soon
as ``/api/auth/session`` reports a user.
"""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .config import Settings

log = logging.getLogger(__name__)

POLL_INTERVAL = 0.4
# How long to sit on /auth/signin waiting for its automatic redirect before
# falling back to clicking a sign-in button.
SIGNIN_REDIRECT_GRACE = 8.0
# Safety valve: if the passkey prompt keeps coming back, something is wrong.
MAX_PASSKEY_SKIPS = 3

_SKIP_WORDS = ("skip", "not now", "maybe later", "no thanks")
_PORTAL_SIGNIN_WORDS = ("sign in", "log in", "continue")
_CONTROL_SELECTOR = "button, a, input[type='submit'], input[type='button']"

_ERROR_SELECTORS = (
    ".validation-summary-errors, .field-validation-error, .auth-error, "
    ".alert-danger, .text-danger, [role='alert']"
)

_FETCH_SESSION_JS = """
const done = arguments[arguments.length - 1];
fetch('/api/auth/session', {credentials: 'same-origin'})
  .then(r => r.json()).then(done).catch(() => done(null));
"""


class LoginError(RuntimeError):
    pass


class PageState(Enum):
    AUTHENTICATED = auto()  # portal reports a signed-in user
    IDP_LOGIN = auto()  # username/password form is showing
    PASSKEY_PROMPT = auto()  # "set up a passkey?" page with a Skip control
    PORTAL_SIGNIN = auto()  # portal /auth/signin, redirect pending
    PORTAL_ERROR = auto()  # portal /auth/error
    TRANSITION = auto()  # mid-redirect / not yet classifiable


def _where(driver: WebDriver) -> str:
    """Host + path only; query strings carry OAuth state and never need logging."""
    u = urlparse(driver.current_url)
    return f"{u.netloc}{u.path}"


def _session_user(driver: WebDriver) -> dict | None:
    """Ask Auth.js who is signed in. Only meaningful on the portal's own origin."""
    try:
        data = driver.execute_async_script(_FETCH_SESSION_JS)
    except WebDriverException:
        return None
    if isinstance(data, dict):
        user = data.get("user")
        return user if isinstance(user, dict) and user else None
    return None


def detect_state(driver: WebDriver, settings: Settings) -> PageState:
    try:
        url = urlparse(driver.current_url)
        soup = BeautifulSoup(driver.page_source, "html.parser")
    except WebDriverException:
        return PageState.TRANSITION  # page was navigating under us

    if soup.select_one("input[name='Password'], input[type='password']"):
        return PageState.IDP_LOGIN

    if url.netloc == settings.idp_host and _has_skip_control(soup):
        return PageState.PASSKEY_PROMPT

    if url.netloc != urlparse(settings.base_url).netloc:
        return PageState.TRANSITION

    if _session_user(driver):
        return PageState.AUTHENTICATED
    if url.path.startswith("/auth/error"):
        return PageState.PORTAL_ERROR
    if url.path.startswith("/auth/signin"):
        return PageState.PORTAL_SIGNIN
    return PageState.TRANSITION


def _form_errors(driver: WebDriver) -> str:
    soup = BeautifulSoup(driver.page_source, "html.parser")
    seen: list[str] = []
    for el in soup.select(_ERROR_SELECTORS):
        text = el.get_text(" ", strip=True)
        if text and text not in seen:
            seen.append(text)
    return "; ".join(seen)


def _submit_credentials(driver: WebDriver, settings: Settings):
    """Fill and submit the identity-provider form. Returns the password element."""
    host = urlparse(driver.current_url).netloc
    if host != settings.idp_host:
        # Never type the password into a host we weren't expecting.
        raise LoginError(
            f"Refusing to enter credentials on unexpected host {host!r} "
            f"(expected {settings.idp_host!r})."
        )

    wait = WebDriverWait(driver, 10)
    user_el = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[name='Username']")))
    pass_el = driver.find_element(By.CSS_SELECTOR, "input[name='Password']")

    user_el.clear()
    user_el.send_keys(settings.username)
    pass_el.clear()
    pass_el.send_keys(settings.password)

    # The page also has a "Sign in with a passkey" submit button, so target the
    # password-login one explicitly.
    buttons = driver.find_elements(By.CSS_SELECTOR, "button[type='submit'][name='button']")
    if buttons:
        buttons[0].click()
    else:
        pass_el.send_keys(Keys.ENTER)
    return pass_el


def _label(text: str | None, value: str | None = None) -> str:
    """Normalised, lower-cased label of a button/link (or an <input>'s value)."""
    return " ".join((text or value or "").lower().split())


def _is_skip_label(label: str) -> bool:
    # "Skip to main content" is an accessibility link, not the passkey skip.
    if label.startswith("skip to") or "main content" in label:
        return False
    return any(word in label for word in _SKIP_WORDS)


def _has_skip_control(soup: BeautifulSoup) -> bool:
    """True on a passkey-setup page: it mentions passkeys and offers a Skip control."""
    if "passkey" not in soup.get_text(" ").lower():
        return False
    return any(
        _is_skip_label(_label(el.get_text(" ", strip=True), el.get("value")))
        for el in soup.select(_CONTROL_SELECTOR)
    )


def _click_control(driver: WebDriver, wanted) -> bool:
    """Click the first visible button/link whose label satisfies ``wanted``."""
    for el in driver.find_elements(By.CSS_SELECTOR, _CONTROL_SELECTOR):
        try:
            if wanted(_label(el.text, el.get_attribute("value"))) and el.is_displayed():
                el.click()
                return True
        except WebDriverException:
            continue
    return False


def _click_portal_signin(driver: WebDriver) -> bool:
    """Fallback if /auth/signin doesn't auto-redirect: click a sign-in control."""
    return _click_control(driver, lambda label: any(w in label for w in _PORTAL_SIGNIN_WORDS))


def _skip_passkey(driver: WebDriver) -> bool:
    """Click Skip on the passkey page, then wait for the page to navigate away."""
    old_page = driver.find_element(By.TAG_NAME, "html")
    if not _click_control(driver, _is_skip_label):
        return False
    try:
        WebDriverWait(driver, 15).until(EC.staleness_of(old_page))
    except TimeoutException:
        pass  # in-page update; the main loop re-checks state either way
    return True


def _describe_page(driver: WebDriver) -> str:
    """Page title and control labels, to make a stuck run diagnosable."""
    try:
        soup = BeautifulSoup(driver.page_source, "html.parser")
    except WebDriverException:
        return "page unavailable"
    labels = [
        _label(el.get_text(" ", strip=True), el.get("value"))
        for el in soup.select(_CONTROL_SELECTOR)
    ]
    labels = [l for l in labels if l][:10]
    title = soup.title.get_text(strip=True) if soup.title else "no title"
    return f"title={title!r}, controls={labels}"


def ensure_logged_in(driver: WebDriver, settings: Settings) -> None:
    """Open the portal and make sure we end up signed in, or raise LoginError."""
    driver.set_script_timeout(10)
    log.info("Opening %s", settings.base_url)
    driver.get(settings.base_url)

    start = time.monotonic()
    deadline = start + settings.timeout
    submitted = False
    passkey_skips = 0
    signin_since: float | None = None
    clicked_signin = False
    state = PageState.TRANSITION

    while time.monotonic() < deadline:
        state = detect_state(driver, settings)

        if state is PageState.AUTHENTICATED:
            user = _session_user(driver) or {}
            who = user.get("name") or user.get("email") or settings.username
            log.info("Signed in as %s (%.1fs)", who, time.monotonic() - start)
            return

        if state is PageState.IDP_LOGIN:
            if submitted:
                detail = _form_errors(driver) or "the login form is showing again"
                raise LoginError(f"Login was rejected: {detail}")
            log.info("Identity provider login form detected; signing in")
            pass_el = _submit_credentials(driver, settings)
            submitted = True
            try:
                WebDriverWait(driver, 20).until(EC.staleness_of(pass_el))
            except TimeoutException:
                pass  # no navigation; next iteration will report why

        elif state is PageState.PASSKEY_PROMPT:
            if passkey_skips >= MAX_PASSKEY_SKIPS:
                raise LoginError("The passkey prompt keeps reappearing after Skip was clicked")
            log.info("Passkey prompt detected; skipping it")
            if _skip_passkey(driver):
                passkey_skips += 1

        elif state is PageState.PORTAL_ERROR:
            raise LoginError(f"Portal returned an auth error page at {_where(driver)}")

        elif state is PageState.PORTAL_SIGNIN:
            signin_since = signin_since or time.monotonic()
            if not clicked_signin and time.monotonic() - signin_since > SIGNIN_REDIRECT_GRACE:
                log.info("No automatic redirect from /auth/signin; trying its sign-in button")
                clicked_signin = _click_portal_signin(driver)

        else:
            signin_since = None

        time.sleep(POLL_INTERVAL)

    raise LoginError(
        f"Timed out after {settings.timeout:.0f}s waiting for login "
        f"(last state: {state.name} at {_where(driver)}; {_describe_page(driver)})"
    )
