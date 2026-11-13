"""
Runtime configuration helpers for running IMDBTraktSyncer outside of a desktop
(servers, containers, ARM64 machines such as Oracle Cloud Ampere A1).

Everything here is opt-in through environment variables. When none of them are
set, the script behaves exactly as before: data lives in the package directory
and a portable "Chrome for Testing" build is downloaded automatically.

Environment variables
---------------------
IMDBTRAKTSYNCER_DATA_DIR
    Directory for credentials.txt, log.txt, IMDB exports, the Chrome profile
    and caches. Use this to keep state on a persistent volume.
IMDBTRAKTSYNCER_CHROME_BINARY / IMDBTRAKTSYNCER_CHROMEDRIVER
    Paths to a system installed Chrome/Chromium and its matching chromedriver.
IMDBTRAKTSYNCER_USE_SYSTEM_CHROME
    "1"/"true" to auto-detect a system Chromium + chromedriver on PATH.
    This is enabled automatically on Linux ARM64, because Google does not
    publish Chrome for Testing builds for that platform.
IMDBTRAKTSYNCER_BLOCK_IMAGES
    "1"/"true" to stop the browser from downloading images (less CPU,
    memory and bandwidth per page).
IMDBTRAKTSYNCER_SKIP_VERSION_CHECK
    "1"/"true" to skip the PyPI update check on start-up.

Credentials and settings can also be supplied through the environment (see
ENV_SETTINGS below). Values from the environment are written into
credentials.txt on start-up, so the interactive prompts are skipped.
"""
import json
import os
import platform
import shutil
import sys

PACKAGE_DIRECTORY = os.path.dirname(os.path.realpath(__file__))

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in TRUE_VALUES


def get_data_directory():
    """Directory where all user data and caches are stored."""
    directory = os.environ.get("IMDBTRAKTSYNCER_DATA_DIR", "").strip()
    if not directory:
        return PACKAGE_DIRECTORY
    directory = os.path.abspath(os.path.expanduser(directory))
    os.makedirs(directory, exist_ok=True)
    return directory


def get_credentials_path():
    return os.path.join(get_data_directory(), "credentials.txt")


def is_linux_arm64():
    machine = platform.machine().lower()
    return sys.platform.startswith("linux") and machine in ("aarch64", "arm64", "armv8l", "armv8b")


def use_system_chrome():
    if os.environ.get("IMDBTRAKTSYNCER_CHROME_BINARY") or os.environ.get("IMDBTRAKTSYNCER_CHROMEDRIVER"):
        return True
    return env_flag("IMDBTRAKTSYNCER_USE_SYSTEM_CHROME", default=is_linux_arm64())


def _first_on_path(names):
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def get_system_chrome_paths():
    """
    Returns (chrome_binary, chromedriver_binary) for a system installed browser.
    Raises FileNotFoundError with install instructions when they cannot be found.
    """
    chrome = os.environ.get("IMDBTRAKTSYNCER_CHROME_BINARY") or _first_on_path(
        ["chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "chrome"]
    )
    driver = os.environ.get("IMDBTRAKTSYNCER_CHROMEDRIVER") or _first_on_path(["chromedriver"])

    # Debian/Ubuntu chromium-driver package location (not always on PATH)
    if not driver and os.path.exists("/usr/lib/chromium/chromedriver"):
        driver = "/usr/lib/chromium/chromedriver"

    missing = []
    if not chrome or not os.path.exists(chrome):
        missing.append("Chromium/Chrome browser")
    if not driver or not os.path.exists(driver):
        missing.append("chromedriver")
    if missing:
        raise FileNotFoundError(
            f"System {' and '.join(missing)} not found. "
            "On Debian/Ubuntu install them with: sudo apt-get install -y chromium chromium-driver "
            "(Ubuntu: chromium-browser chromium-chromedriver), or set IMDBTRAKTSYNCER_CHROME_BINARY "
            "and IMDBTRAKTSYNCER_CHROMEDRIVER. Google does not publish Chrome for Testing builds "
            "for Linux ARM64, so a system browser is required there."
        )
    return chrome, driver


def get_system_chrome_user_data_directory():
    directory = os.path.join(get_data_directory(), "chrome-profile")
    os.makedirs(directory, exist_ok=True)
    return directory


# Environment variable -> (credentials.txt key, type)
ENV_SETTINGS = {
    "TRAKT_CLIENT_ID": ("trakt_client_id", str),
    "TRAKT_CLIENT_SECRET": ("trakt_client_secret", str),
    "IMDB_USERNAME": ("imdb_username", str),
    "IMDB_PASSWORD": ("imdb_password", str),
    "SYNC_WATCHLIST": ("sync_watchlist", bool),
    "SYNC_RATINGS": ("sync_ratings", bool),
    "SYNC_REVIEWS": ("sync_reviews", bool),
    "SYNC_WATCH_HISTORY": ("sync_watch_history", bool),
    "REMOVE_WATCHED_FROM_WATCHLISTS": ("remove_watched_from_watchlists", bool),
    "MARK_RATED_AS_WATCHED": ("mark_rated_as_watched", bool),
    "REMOVE_WATCHLIST_ITEMS_OLDER_THAN_DAYS": ("watchlist_days_to_remove", int),
}


def seed_credentials_from_env():
    """
    Write credentials/settings provided through environment variables into
    credentials.txt so that the interactive prompts are skipped.
    Returns the list of keys that were set from the environment.
    """
    updates = {}
    for env_name, (key, value_type) in ENV_SETTINGS.items():
        raw = os.environ.get(env_name)
        if raw is None or raw.strip() == "":
            continue
        raw = raw.strip()
        if value_type is bool:
            lowered = raw.lower()
            if lowered in TRUE_VALUES:
                updates[key] = True
            elif lowered in FALSE_VALUES:
                updates[key] = False
            else:
                print(f"Ignoring {env_name}={raw!r}: expected true/false.")
        elif value_type is int:
            try:
                days = int(raw)
            except ValueError:
                print(f"Ignoring {env_name}={raw!r}: expected a whole number of days (0 disables).")
                continue
            updates["remove_watchlist_items_older_than_x_days"] = days > 0
            updates[key] = days if days > 0 else None
        else:
            updates[key] = raw

    if not updates:
        return []

    file_path = get_credentials_path()
    data = {}
    if os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}

    changed = {k: v for k, v in updates.items() if k not in data or data[k] != v}
    if changed:
        # A changed Trakt app invalidates the stored tokens
        if "trakt_client_id" in changed or "trakt_client_secret" in changed:
            for token_key in ("trakt_access_token", "trakt_refresh_token", "trakt_token_expires"):
                if data.get(token_key) not in (None, "empty"):
                    data[token_key] = "empty"
        data.update(changed)
        write_json_atomic(file_path, data)
    return list(updates.keys())


def write_json_atomic(file_path, data):
    """Write JSON to a temp file then rename, so a crash never leaves a half written file."""
    directory = os.path.dirname(file_path) or "."
    tmp_path = os.path.join(directory, f".{os.path.basename(file_path)}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, separators=(", ", ": "))
    os.replace(tmp_path, file_path)
    try:
        os.chmod(file_path, 0o600)
    except OSError:
        pass
