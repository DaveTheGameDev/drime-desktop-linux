"""Flatpak: the file-based contract between the app and its background service.

systemd cannot be used from the sandbox, so `drime-desktop --daemon` (daemon.py)
holds the drive and runs the sync. The GUI/CLI tell it what is enabled through a
small JSON file, it reports back through a status file and a log, a lock file says
whether it is alive, and touch files carry one-shot requests. Pure stdlib, no GTK.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import APP_ID, backend

CONFIG_FILE = backend.CONFIG_DIR / "daemon.json"   # {"mount": bool, "sync": bool}
LOG_FILE = backend.CACHE_DIR / "daemon.log"         # service, mount and sync output
DEFAULT_CONFIG = {"mount": False, "sync": False}


def _run_dir() -> Path:
    """Where lock, status and triggers live: a directory every instance of the app
    shares. $XDG_RUNTIME_DIR/app/<id> is bind-mounted into each Flatpak instance."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        d = Path(runtime) / ("app/" + APP_ID if backend.is_flatpak() else "drime-desktop")
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except OSError:
            pass
    return backend.CACHE_DIR / "run"


RUN_DIR = _run_dir()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# --- what is enabled (written by the GUI/CLI, read by the service) ------------

def read_config() -> dict:
    return {**DEFAULT_CONFIG, **{k: bool(v) for k, v in _read_json(CONFIG_FILE).items()
                                 if k in DEFAULT_CONFIG}}


def write_config(**changes: bool) -> dict:
    cfg = {**read_config(), **{k: bool(v) for k, v in changes.items()}}
    _write_json(CONFIG_FILE, cfg)
    return cfg


# --- what is happening (written by the service) -----------------------------

def read_status() -> dict:
    return _read_json(RUN_DIR / "status.json")


def write_status(status: dict) -> None:
    _write_json(RUN_DIR / "status.json", status)


# --- liveness ------------------------------------------------------------------

class DaemonLock:
    """Held by the running service. Instances cannot see each other's PIDs (separate
    PID namespaces), so an flock on a shared file is the liveness signal."""

    def __init__(self, path: Path | None = None):
        self.path = path or RUN_DIR / "daemon.lock"
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.release()


def daemon_alive() -> bool:
    lock = DaemonLock()
    if lock.acquire():
        lock.release()
        return False
    return True


# --- one-shot requests -----------------------------------------------------------

def trigger(name: str) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / name).touch()


def consume(name: str) -> bool:
    try:
        (RUN_DIR / name).unlink()
        return True
    except OSError:
        return False


def pending(name: str) -> bool:
    return (RUN_DIR / name).exists()


# --- log -------------------------------------------------------------------------

def log_tail(lines: int = 60) -> str:
    """The last `lines` lines of the service log ('' when there is none)."""
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = b""
            pos = size
            while pos > 0 and chunk.count(b"\n") <= lines:
                step = min(8192, pos)
                pos -= step
                f.seek(pos)
                chunk = f.read(step) + chunk
    except OSError:
        return ""
    text = chunk.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


# --- starting and stopping the service -------------------------------------------

def spawn_command() -> list[str]:
    """How to start the service so that it outlives the caller. In a Flatpak every
    child dies with the instance's main process, so `flatpak-spawn` (the Flatpak
    portal's Spawn, not --host) starts a fresh instance of this app instead."""
    if backend.is_flatpak():
        return ["flatpak-spawn", "drime-desktop", "--daemon"]
    return [sys.executable, "-m", "drime_desktop.cli", "--daemon"]


def ensure_daemon(timeout: float = 10) -> bool:
    """Start the service unless it is running; True once it holds the lock."""
    if daemon_alive():
        return True
    try:
        subprocess.Popen(spawn_command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon_alive():
            return True
        time.sleep(0.25)
    return daemon_alive()


def stop_daemon(timeout: float = 15) -> bool:
    """Ask the service to exit; True once the lock is free."""
    if not daemon_alive():
        return True
    trigger("stop")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not daemon_alive():
            consume("stop")
            return True
        time.sleep(0.25)
    return not daemon_alive()
