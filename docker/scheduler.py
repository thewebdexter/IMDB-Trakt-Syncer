#!/usr/bin/env python3
"""
Container entrypoint for IMDBTraktSyncer.

Commands:
  run    (default) sync now, then every SYNC_INTERVAL_HOURS, forever
  once   run a single sync and exit
  setup  interactive first run (Trakt authorisation, prompts). Use with -it
  health exit 0 if the last sync finished recently, 1 otherwise
  any other arguments are passed straight to IMDBTraktSyncer (e.g. --clear-user-data)

Each sync runs in its own process group with a lower CPU priority. A sync that
runs longer than RUN_TIMEOUT_HOURS is killed together with every Chromium
process it started, so a hung browser can never pile up and eat the server.
"""
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

DATA_DIR = os.environ.get("IMDBTRAKTSYNCER_DATA_DIR", "/data")
CREDENTIALS = os.path.join(DATA_DIR, "credentials.txt")
STATUS_FILE = os.path.join(DATA_DIR, "last_run.json")
SYNC_CMD = [sys.executable, "-m", "IMDBTraktSyncer"]

# Lines IMDBTraktSyncer prints when a run fails. It exits with code 0 even then,
# so the output is checked to report the real result.
FAILURE_MARKERS = (
    "--- ERROR ---",
    "Error: Not signed in to IMDB",
    "Error initializing WebDriver",
)


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        log(f"Invalid {name}={os.environ.get(name)!r}, using {default}")
        return float(default)


def log(message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[scheduler {stamp}] {message}", flush=True)


def setup_complete():
    """True when Trakt has been authorised and the IMDB login is known."""
    try:
        with open(CREDENTIALS, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    required = ["trakt_client_id", "trakt_client_secret", "trakt_refresh_token", "imdb_username", "imdb_password"]
    return all(data.get(k) not in (None, "", "empty") for k in required)


def write_status(status, started, exit_code, duration):
    data = {
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration),
        "exit_code": exit_code,
    }
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STATUS_FILE)


_current = None


def _forward_and_exit(signum, _frame):
    log(f"Received signal {signum}, stopping")
    if _current and _current.poll() is None:
        _kill_group(_current, graceful=True)
    sys.exit(0)


def _kill_group(proc, graceful):
    # start_new_session=True makes the child a group leader, so its PID is the group ID.
    # Using it directly still works after the leader itself has exited and been reaped.
    pgid = proc.pid
    if graceful:
        try:
            os.killpg(pgid, signal.SIGTERM)
            proc.wait(timeout=20)
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _lower_priority():
    niceness = int(os.environ.get("SYNC_NICE", "10"))
    if niceness:
        os.nice(niceness)


def run_sync(extra_args=None, interactive=False):
    is_sync = not extra_args
    global _current
    timeout = env_float("RUN_TIMEOUT_HOURS", 8) * 3600
    cmd = SYNC_CMD + list(extra_args or [])
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    log("Starting sync" if not extra_args else f"Running: IMDBTraktSyncer {' '.join(extra_args)}")

    if interactive:
        # Attach directly to the terminal for prompts
        proc = subprocess.Popen(cmd, start_new_session=False)
        _current = proc
        code = proc.wait()
        _current = None
        return code

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
        start_new_session=True,  # own process group: lets us kill Chromium children too
        preexec_fn=_lower_priority,
    )
    _current = proc
    state = {"failed": False}

    def pump():
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if any(marker in line for marker in FAILURE_MARKERS):
                state["failed"] = True

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout if timeout > 0 else None)
    except subprocess.TimeoutExpired:
        timed_out = True
        log(f"Sync exceeded RUN_TIMEOUT_HOURS ({timeout / 3600:g}h); killing it and its browser")
        _kill_group(proc, graceful=True)
    code = proc.wait()
    # Kill any browser processes left in the group; they would otherwise keep the output pipe open
    _kill_group(proc, graceful=False)
    reader.join(timeout=30)
    failed = state["failed"]
    _current = None

    duration = time.monotonic() - t0
    status = "timeout" if timed_out else ("failed" if failed or code != 0 else "success")
    if is_sync:
        # Only real syncs count for the health check
        write_status(status, started, code, duration)
    log(f"{'Sync' if is_sync else 'Command'} finished: {status} in {duration / 60:.1f} min")
    return 0 if status == "success" else 1


def health():
    interval = env_float("SYNC_INTERVAL_HOURS", 6) * 3600
    timeout = env_float("RUN_TIMEOUT_HOURS", 8) * 3600
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        finished = datetime.fromisoformat(data["finished_at"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        # No run recorded yet: healthy while setup/first run is pending
        return 0
    age = (datetime.now(timezone.utc) - finished).total_seconds()
    # Allow for one interval, one full run and some jitter
    return 0 if age < interval * 1.2 + timeout + 600 else 1


def loop():
    interval = env_float("SYNC_INTERVAL_HOURS", 6) * 3600
    jitter = env_float("SYNC_JITTER_MINUTES", 20) * 60
    initial_delay = env_float("INITIAL_DELAY_SECONDS", 0)
    if interval <= 0:
        log("SYNC_INTERVAL_HOURS must be > 0")
        return 2
    if initial_delay > 0:
        time.sleep(initial_delay)

    while True:
        if not setup_complete():
            log("First-time setup is not complete (Trakt authorisation / IMDB login missing).")
            log("Run once interactively:  docker compose run --rm imdb-trakt-syncer setup")
            log("Checking again in 5 minutes...")
            time.sleep(300)
            continue
        run_sync()
        # Random jitter so the IMDB logins do not happen at an exact, bot-like cadence
        sleep_for = interval + random.uniform(0, jitter)
        log(f"Next sync in {sleep_for / 3600:.2f} h")
        time.sleep(sleep_for)


def main():
    signal.signal(signal.SIGTERM, _forward_and_exit)
    signal.signal(signal.SIGINT, _forward_and_exit)
    os.makedirs(DATA_DIR, exist_ok=True)

    args = sys.argv[1:]
    command = args[0] if args else "run"
    if command == "run":
        return loop()
    if command == "once":
        return run_sync()
    if command == "setup":
        if not sys.stdin.isatty():
            log("setup needs an interactive terminal: docker compose run --rm imdb-trakt-syncer setup")
            return 2
        return run_sync(interactive=True)
    if command == "health":
        return health()
    # Pass-through, e.g. --clear-user-data
    return run_sync(extra_args=args, interactive=sys.stdin.isatty())


if __name__ == "__main__":
    sys.exit(main())
