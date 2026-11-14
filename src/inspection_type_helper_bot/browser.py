"""Chrome / chromedriver setup."""

from __future__ import annotations

import os
import subprocess

import urllib3
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from .config import Settings

# What a call on the driver raises when the browser or chromedriver is gone:
# Selenium's own errors, plus the raw connection errors from talking to chromedriver.
BROWSER_GONE = (WebDriverException, urllib3.exceptions.HTTPError, ConnectionError)


def build_driver(settings: Settings) -> webdriver.Chrome:
    """Create a Chrome driver that keeps its session in a dedicated profile.

    The profile directory persists cookies between runs, so after the first
    successful login the bot is usually already authenticated when it opens.
    """
    settings.profile_dir.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument(f"--user-data-dir={settings.profile_dir}")
    options.add_argument("--window-size=1440,960")
    options.add_argument("--disable-notifications")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    if settings.headless:
        options.add_argument("--headless=new")

    # Hide the "Chrome is being controlled..." bar and Chrome's own
    # save-password prompts, which can steal focus from the page.
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option(
        "prefs",
        {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
        },
    )

    # Run chromedriver (and therefore Chrome) in its own session. Otherwise a
    # Ctrl-C in the terminal is delivered to chromedriver as well and kills it,
    # leaving the bot with no browser to drive after you leave the pause menu.
    if os.name == "posix":
        popen_kw = {"start_new_session": True}
    else:  # Windows: a separate process group no longer receives the console's Ctrl-C
        popen_kw = {"creation_flags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}

    # With no explicit path, Selenium Manager downloads/caches a chromedriver
    # that matches the installed Chrome.
    service = Service(executable_path=settings.chromedriver_path, popen_kw=popen_kw) if (
        settings.chromedriver_path
    ) else Service(popen_kw=popen_kw)
    return webdriver.Chrome(service=service, options=options)
