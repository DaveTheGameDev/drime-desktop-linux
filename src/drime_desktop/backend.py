"""All system-level operations (rclone, systemd, files).

Pure Python + subprocess. No GTK imports, so it is usable from the CLI, the
GUI, and tests. Every function is idempotent where it makes sense.

Inside a Flatpak (is_flatpak()) there is no systemd and no in-sandbox FUSE:
the unit-facing functions talk to the app's own background service instead
(see sandbox.py / daemon.py), and rclone mounts through the host's fusermount3.
"""
from __future__ import annotations

import functools
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import APP_ID

HOME = Path.home()


def _xdg(var: str, default: str) -> Path:
    """An XDG base directory. Plain ~/.config etc. on RPM/DEB; inside a Flatpak
    the variables point at ~/.var/app/<id>/{config,data,cache} (rclone honours
    them too, so rclone.conf and its caches land there as well)."""
    return Path(os.environ.get(var) or HOME / default)


CONFIG_HOME = _xdg("XDG_CONFIG_HOME", ".config")
DATA_HOME = _xdg("XDG_DATA_HOME", ".local/share")
CACHE_HOME = _xdg("XDG_CACHE_HOME", ".cache")
CONFIG_DIR = CONFIG_HOME / "drime-desktop"   # window.json, updates.json, daemon.json
CACHE_DIR = CACHE_HOME / "drime-desktop"     # web cache, daemon.log

REMOTE = "drime"
MOUNT = HOME / "Drime"
SYNC_DIR = HOME / "DrimeSync"
SYNC_REMOTE_PATH = f"{REMOTE}:Sync"
USER_UNIT_DIR = HOME / ".config/systemd/user"
SYSTEM_UNIT_DIR = Path("/usr/lib/systemd/user")
MOUNT_UNIT = "rclone-drime-mount.service"
SYNC_SERVICE = "drime-bisync.service"
SYNC_TIMER = "drime-bisync.timer"
UNITS = (MOUNT_UNIT, SYNC_SERVICE, SYNC_TIMER)
MIN_RCLONE = (1, 73, 0)
WEB_URL = "https://app.drime.cloud"
ICON_SYSTEM = Path("/usr/share/icons/hicolor/512x512/apps/drime-desktop.png")
ICON_APP = Path("/app/share/icons/hicolor/512x512/apps/drime-desktop.png")   # Flatpak
ICON_USER = DATA_HOME / "icons/drime.png"    # host-visible in a Flatpak (~/.var/app/<id>/data)
LEGACY_LAUNCHER = DATA_HOME / "applications/drime.desktop"
BOOKMARKS = HOME / ".config/gtk-3.0/bookmarks"   # always the host's file (xdg-config/gtk-3.0)
RCLONE_CACHE = CACHE_HOME / "rclone"
WEB_DATA_DIR = DATA_HOME / "drime-desktop/web"   # WebKit cookies/local storage (login)
WEB_CACHE_DIR = CACHE_DIR / "web"

# Set by install.sh/uninstall.sh when running from a git checkout (no package).
SRC_DIR = Path(os.environ["DRIME_DESKTOP_SRC"]) if os.environ.get("DRIME_DESKTOP_SRC") else None

LogCb = Callable[[str], None]


def _noop(_line: str) -> None:
    pass


def child_pids(comm: str, proc: Path = Path("/proc"), parent: int | None = None) -> list[int]:
    """PIDs of our direct children whose command name is `comm` (/proc truncates
    the name to 15 characters, so a prefix match is used)."""
    parent = os.getpid() if parent is None else parent
    pids = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            name = stat[stat.index("(") + 1:stat.rindex(")")]
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError):
            continue
        if ppid == parent and comm.startswith(name):
            pids.append(int(entry.name))
    return sorted(pids)


def kill_children(comm: str) -> bool:
    """SIGKILL our direct children named `comm`; returns whether there were any."""
    pids = child_pids(comm)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return bool(pids)


def run(cmd: list[str], check: bool = False, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    return subprocess.run(cmd, check=check, **kw)


def run_logged(cmd: list[str], log: LogCb = _noop) -> int:
    """Run a command, streaming its combined output line by line to `log`."""
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip("\n"))
    return proc.wait()


def systemctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return run(["systemctl", "--user", *args], check=check)


# --- Preflight ---------------------------------------------------------------

def rclone_version() -> tuple[int, ...] | None:
    if not shutil.which("rclone"):
        return None
    out = run(["rclone", "version"]).stdout
    m = re.search(r"rclone v(\d+)\.(\d+)(?:\.(\d+))?", out)
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


OS_RELEASE = Path("/etc/os-release")
HOST_OS_RELEASE = Path("/run/host/os-release")   # the host's file, bind-mounted by flatpak
FLATPAK_INFO = Path("/.flatpak-info")
RCLONE_ORG_HINT = ("install rclone 1.73 or newer from rclone.org, e.g.  curl https://rclone.org/install.sh | sudo bash  "
                   "- the rclone package of Debian and Ubuntu is too old")


def is_flatpak() -> bool:
    """Running inside the Flatpak sandbox (no systemd, no in-sandbox FUSE, bundled rclone)."""
    return FLATPAK_INFO.is_file()


def distro() -> str:
    """'fedora', 'debian' (Debian, Ubuntu and derivatives) or 'unknown', from /etc/os-release
    (the host's copy inside a Flatpak, where /etc/os-release describes the runtime)."""
    ids: set[str] = set()
    try:
        source = HOST_OS_RELEASE if HOST_OS_RELEASE.is_file() else OS_RELEASE
        for line in source.read_text().splitlines():
            key, _, value = line.partition("=")
            if key in ("ID", "ID_LIKE"):
                ids.update(value.strip().strip('"').split())
    except OSError:
        pass
    if "fedora" in ids:
        return "fedora"
    if ids & {"debian", "ubuntu"}:
        return "debian"
    return "unknown"


def install_hint(package: str, upgrade: bool = False) -> str:
    """How to get `package` on this distribution, for error messages."""
    d = distro()
    if d == "debian" and package == "rclone" and not is_flatpak():
        return RCLONE_ORG_HINT   # Debian and Ubuntu ship rclone 1.60, older than MIN_RCLONE
    if d == "fedora":
        return f"sudo dnf {'upgrade' if upgrade else 'install'} {package}"
    if d == "debian":
        return f"sudo apt install {package}"
    return f"install your distribution's {package} package"


def remove_hint() -> str:
    """The command that uninstalls this application."""
    if is_flatpak():
        return f"flatpak uninstall {APP_ID}"
    return {"fedora": "sudo dnf remove drime-desktop",
            "debian": "sudo apt remove drime-desktop"}.get(distro(), "uninstall the drime-desktop package")


@functools.lru_cache(maxsize=None)
def host_has_fusermount3() -> bool:
    """Flatpak: the drive mounts through the host's fusermount3 (/app/bin/fusermount3 is a
    wrapper around `flatpak-spawn --host`). False when fuse3 is missing on the host or the
    org.freedesktop.Flatpak permission was taken away."""
    try:
        return run(["flatpak-spawn", "--host", "sh", "-c", "command -v fusermount3"]).returncode == 0
    except OSError:
        return False


def drive_problem() -> str | None:
    """Why the virtual drive cannot be enabled in this installation (None = it can).
    Only the Flatpak has a drive-specific limitation; elsewhere preflight() covers fuse3."""
    if is_flatpak() and not host_has_fusermount3():
        return f"The virtual drive needs fuse3 on your system ({install_hint('fuse3')})."
    return None


def preflight() -> list[str]:
    """Return a list of human-readable blocking problems (empty = all good)."""
    problems = []
    ver = rclone_version()
    if is_flatpak():
        # rclone is bundled; fuse3 is only needed for the drive (drive_problem()); no systemd.
        if ver is None:
            problems.append("rclone is missing from this Flatpak build (packaging bug).")
        return problems
    if ver is None:
        problems.append(f"rclone is not installed ({install_hint('rclone')}).")
    elif ver < MIN_RCLONE:
        problems.append(
            "rclone %s is too old; the Drime backend needs %s or newer (%s)."
            % (".".join(map(str, ver)), ".".join(map(str, MIN_RCLONE)), install_hint("rclone", upgrade=True)))
    if not shutil.which("fusermount3"):
        problems.append(f"fuse3 is not installed ({install_hint('fuse3')}).")
    if systemctl("is-system-running").returncode not in (0, 1):
        # 1 = "degraded", which is still a working user session
        problems.append("No systemd user session is available.")
    return problems


# --- rclone remote (API token) -----------------------------------------------

def remote_exists() -> bool:
    return f"{REMOTE}:" in run(["rclone", "listremotes"]).stdout.split()


def create_remote(token: str) -> None:
    token = token.strip()
    if not token:
        raise ValueError("No token provided.")
    cp = run(["rclone", "config", "create", REMOTE, "drime", f"access_token={token}"])
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "rclone config create failed")


def check_remote() -> bool:
    """True when Drime can be reached with the configured token."""
    return run(["rclone", "about", f"{REMOTE}:", "--contimeout", "15s"]).returncode == 0


def delete_remote() -> None:
    run(["rclone", "config", "delete", REMOTE])


# --- Background service (Flatpak) ---------------------------------------------
# The systemd units cannot exist in the sandbox. `drime-desktop --daemon` (daemon.py)
# takes their place: it holds the mount and runs the sync on a timer, reads what is
# enabled from daemon.json and reports through status.json (sandbox.py). It is
# autostarted at login through the Background portal and (re)spawned by the GUI.

_last_watchdog = 0.0


def _settle_daemon(log: LogCb = _noop) -> None:
    """After an enable/disable: make sure the service and its login autostart match
    what is enabled. A denied background permission is reported, not fatal."""
    from . import portal, sandbox
    cfg = sandbox.read_config()
    if cfg["mount"] or cfg["sync"]:
        if not portal.request_background(True):
            log("Note: the desktop did not allow Drime to run in the background; the drive and "
                "the sync only run while the Drime window is open.")
        if not sandbox.ensure_daemon():
            raise RuntimeError("Could not start the Drime background service.")
    else:
        sandbox.stop_daemon()
        portal.request_background(False)


def watchdog() -> None:
    """Flatpak: respawn the background service if it died while something is enabled.
    Called from the GUI's refresh tick; rate-limited to once a minute."""
    global _last_watchdog
    if not is_flatpak() or time.monotonic() - _last_watchdog < 60:
        return
    from . import sandbox
    cfg = sandbox.read_config()
    if (cfg["mount"] or cfg["sync"]) and not sandbox.daemon_alive():
        _last_watchdog = time.monotonic()
        sandbox.ensure_daemon()


def daemon_alive() -> bool | None:
    """Whether the background service runs (None outside a Flatpak)."""
    if not is_flatpak():
        return None
    from . import sandbox
    return sandbox.daemon_alive()


def _wait_for(cond: Callable[[], bool], timeout: float, step: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if cond():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(step)


# --- systemd units -----------------------------------------------------------

def is_enabled(unit: str) -> bool:
    if is_flatpak():
        from . import sandbox
        cfg = sandbox.read_config()
        return {MOUNT_UNIT: cfg["mount"], SYNC_TIMER: cfg["sync"]}.get(unit, False)
    return systemctl("is-enabled", unit).returncode == 0


def is_active(unit: str) -> bool:
    if is_flatpak():
        from . import sandbox
        st = sandbox.read_status()
        if unit == MOUNT_UNIT:
            return sandbox.daemon_alive() and bool(st.get("mount", {}).get("running"))
        if unit == SYNC_SERVICE:
            return sandbox.daemon_alive() and bool(st.get("sync", {}).get("running"))
        return False
    return systemctl("is-active", unit).returncode == 0


def unit_source(unit: str) -> str:
    """'packaged' (/usr/lib/systemd/user), 'user' (~/.config/systemd/user) or 'none'."""
    if is_flatpak():
        return "none"
    frag = systemctl("show", "-p", "FragmentPath", "--value", unit).stdout.strip()
    if not frag:
        return "none"
    if frag.startswith(str(USER_UNIT_DIR)):
        return "user"
    return "packaged"


def packaged_units_available() -> bool:
    return not is_flatpak() and all((SYSTEM_UNIT_DIR / u).is_file() for u in UNITS)


def user_unit_copies() -> list[str]:
    if is_flatpak():
        return []
    return [u for u in UNITS if (USER_UNIT_DIR / u).exists()]


def migrate_user_units(log: LogCb = _noop) -> bool:
    """Replace ~/.config/systemd/user copies with the packaged units.

    Running services keep running; only the unit files and enable-symlinks are
    swapped. Returns True when something was migrated.
    """
    copies = user_unit_copies()
    if is_flatpak() or not copies or not packaged_units_available():
        return False
    log("Migrating systemd units to the packaged versions")
    enabled = [u for u in (MOUNT_UNIT, SYNC_TIMER) if is_enabled(u)]
    for u in enabled:
        systemctl("disable", u)  # without --now: keep the mount alive
    for u in copies:
        (USER_UNIT_DIR / u).unlink()
    systemctl("daemon-reload")
    for u in enabled:
        systemctl("enable", u)
    return True


def ensure_units(log: LogCb = _noop) -> None:
    """Make sure the three units are known to systemd (packaged or user copies)."""
    if is_flatpak():
        return
    if packaged_units_available():
        migrate_user_units(log)
    else:
        if SRC_DIR is None or not (SRC_DIR / "systemd" / MOUNT_UNIT).is_file():
            raise RuntimeError("systemd units not found: install the package or run from a git checkout.")
        USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
        for u in UNITS:
            shutil.copy(SRC_DIR / "systemd" / u, USER_UNIT_DIR / u)
        log(f"Installed units to {USER_UNIT_DIR}")
    systemctl("daemon-reload")


# --- Virtual drive -----------------------------------------------------------

def _in_proc_mounts() -> bool:
    try:
        with open("/proc/mounts") as f:
            target = str(MOUNT).replace(" ", "\\040")
            return any(line.split()[1] == target for line in f)
    except OSError:
        return False


def is_stale_mount() -> bool:
    """The mountpoint is still listed but rclone is gone: every access fails with
    'Transport endpoint is not connected' until it is lazily unmounted."""
    if not _in_proc_mounts():
        return False
    try:
        os.stat(MOUNT)
        return False
    except OSError:
        return True


def _host_mounted() -> bool:
    """Flatpak: ask the host whether ~/Drime is an rclone mount. The mount is created in
    the host's mount namespace and should propagate into the sandbox (bubblewrap makes
    / a slave mount), but this is the fallback in case it does not show in /proc/mounts."""
    try:
        return run(["flatpak-spawn", "--host", "findmnt", "-n", "-T", str(MOUNT),
                    "-t", "fuse.rclone"]).returncode == 0
    except OSError:
        return False


def is_mounted() -> bool:
    """Mounted *and* answering (a stale mountpoint left by a crashed rclone doesn't count)."""
    if is_flatpak():
        return (_in_proc_mounts() or _host_mounted()) and not is_stale_mount()
    return _in_proc_mounts() and not is_stale_mount()


def unmount(lazy: bool = True) -> None:
    if is_flatpak() or _in_proc_mounts():
        # In a Flatpak /app/bin/fusermount3 forwards to the host's; run it unconditionally
        # because the sandbox's /proc/mounts may not show the host-side mount.
        run(["fusermount3", "-uz" if lazy else "-u", str(MOUNT)])


def _mount_enable_flatpak(log: LogCb) -> None:
    from . import sandbox
    problem = drive_problem()
    if problem:
        raise RuntimeError(problem)
    sandbox.write_config(mount=True)
    _settle_daemon(log)
    if not _wait_for(is_mounted, 30):
        raise RuntimeError("The drive did not come up. Last log lines:\n" + sandbox.log_tail(10))
    log(f"Virtual drive mounted at {MOUNT} (held by the Drime background service)")


def _mount_disable_flatpak(log: LogCb) -> None:
    from . import sandbox
    sandbox.write_config(mount=False)
    _wait_for(lambda: not sandbox.read_status().get("mount", {}).get("running"), 10)
    unmount()
    _settle_daemon(log)
    log("Virtual drive disabled")


def mount_enable(log: LogCb = _noop) -> None:
    if is_flatpak():
        return _mount_enable_flatpak(log)
    ensure_units(log)
    if is_stale_mount():
        unmount()
        systemctl("reset-failed", MOUNT_UNIT)
    cp = systemctl("enable", "--now", MOUNT_UNIT)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "Could not start the mount service")
    log(f"Virtual drive mounted at {MOUNT}")


def mount_disable(log: LogCb = _noop) -> None:
    if is_flatpak():
        return _mount_disable_flatpak(log)
    systemctl("disable", "--now", MOUNT_UNIT)
    unmount()
    log("Virtual drive disabled")


def bookmark_add() -> None:
    BOOKMARKS.parent.mkdir(parents=True, exist_ok=True)
    line = f"file://{MOUNT} Drime\n"
    existing = BOOKMARKS.read_text() if BOOKMARKS.exists() else ""
    if f"file://{MOUNT} " not in existing:
        with BOOKMARKS.open("a") as f:
            f.write(line)


def bookmark_remove() -> None:
    if BOOKMARKS.exists():
        lines = [l for l in BOOKMARKS.read_text().splitlines(True)
                 if not l.startswith(f"file://{MOUNT} ")]
        BOOKMARKS.write_text("".join(lines))


def icon_path() -> Path | None:
    if is_flatpak():
        # /app is invisible to the host's file manager: keep a copy in the app's data dir
        # (~/.var/app/<id>/data on the host) for the folder icon.
        try:
            if ICON_APP.is_file() and (not ICON_USER.is_file()
                                       or ICON_USER.stat().st_mtime < ICON_APP.stat().st_mtime):
                ICON_USER.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ICON_APP, ICON_USER)
        except OSError:
            pass
        return ICON_USER if ICON_USER.is_file() else None
    if ICON_SYSTEM.is_file():
        return ICON_SYSTEM
    if SRC_DIR is not None and (SRC_DIR / "assets/drime.png").is_file():
        ICON_USER.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SRC_DIR / "assets/drime.png", ICON_USER)
    return ICON_USER if ICON_USER.is_file() else None


def folder_icon_set() -> bool:
    icon = icon_path()
    if icon is None or not MOUNT.is_dir():
        return False
    return run(["gio", "set", str(MOUNT), "metadata::custom-icon", f"file://{icon}"]).returncode == 0


def folder_icon_unset() -> None:
    run(["gio", "set", "-t", "unset", str(MOUNT), "metadata::custom-icon"])


# --- Sync folder -------------------------------------------------------------

def bisync_initialized() -> bool:
    return bool(glob.glob(str(RCLONE_CACHE / "bisync" / "*DrimeSync*")))


def bisync_baseline(log: LogCb = _noop) -> None:
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    (SYNC_DIR / "RCLONE_TEST").touch()
    if bisync_initialized():
        log("Sync already initialized, skipping the baseline run")
        return
    if run_logged(["rclone", "copy", str(SYNC_DIR / "RCLONE_TEST"), SYNC_REMOTE_PATH + "/"], log) != 0:
        raise RuntimeError("Could not upload the RCLONE_TEST marker")
    rc = run_logged(["rclone", "bisync", str(SYNC_DIR), SYNC_REMOTE_PATH, "--size-only",
                     "--create-empty-src-dirs", "--check-access", "--resync"], log)
    if rc != 0:
        raise RuntimeError("The initial sync (bisync --resync) failed; see the log")
    log("Sync baseline established")


def sync_enable(log: LogCb = _noop) -> None:
    if is_flatpak():
        from . import sandbox
        bisync_baseline(log)
        sandbox.write_config(sync=True)
        _settle_daemon(log)
        log("Sync scheduled every 15 minutes (Drime background service)")
        return
    ensure_units(log)
    bisync_baseline(log)
    cp = systemctl("enable", "--now", SYNC_TIMER)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "Could not enable the sync timer")
    log("Sync timer enabled (every 15 minutes)")


def sync_disable(log: LogCb = _noop) -> None:
    if is_flatpak():
        from . import sandbox
        sandbox.write_config(sync=False)
        _settle_daemon(log)
        log("Sync disabled")
        return
    systemctl("disable", "--now", SYNC_TIMER)
    log("Sync timer disabled")


def sync_now() -> None:
    if is_flatpak():
        from . import sandbox
        sandbox.trigger("sync-now")
        sandbox.ensure_daemon()   # the service runs the pending sync even when the timer is off
        return
    systemctl("start", "--no-block", SYNC_SERVICE)


@dataclass
class SyncStatus:
    running: bool
    last_result: str          # success / exit-code / '' when never run
    last_start: int | None    # unix seconds
    last_end: int | None
    next_run: int | None


def _unix(value: str) -> int | None:
    value = value.strip()
    if value.startswith("@"):
        return int(value[1:])
    return None


def sync_status() -> SyncStatus:
    if is_flatpak():
        from . import sandbox
        s = sandbox.read_status().get("sync", {})
        alive = sandbox.daemon_alive()
        return SyncStatus(
            running=alive and bool(s.get("running")),
            last_result=s.get("last_result") or "",
            last_start=s.get("last_start"),
            last_end=s.get("last_end"),
            next_run=s.get("next_run") if alive else None,
        )
    out = systemctl("show", SYNC_SERVICE, "--timestamp=unix", "-p",
                    "ActiveState,Result,ExecMainStartTimestamp,ExecMainExitTimestamp").stdout
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    next_run = None
    lt = run(["systemctl", "--user", "list-timers", SYNC_TIMER, "--output=json", "--all"])
    try:
        for t in json.loads(lt.stdout or "[]"):
            if t.get("unit") == SYNC_TIMER and t.get("next"):
                next_run = int(t["next"]) // 1_000_000
    except (ValueError, TypeError):
        pass
    return SyncStatus(
        running=props.get("ActiveState") in ("active", "activating"),
        last_result=props.get("Result", ""),
        last_start=_unix(props.get("ExecMainStartTimestamp", "")),
        last_end=_unix(props.get("ExecMainExitTimestamp", "")),
        next_run=next_run,
    )


def sync_log_tail(lines: int = 60) -> str:
    if is_flatpak():
        from . import sandbox
        return sandbox.log_tail(lines)
    return run(["journalctl", "--user", "-u", SYNC_SERVICE, "-n", str(lines),
                "--no-pager", "-o", "short-iso"]).stdout


# --- Aggregate state ---------------------------------------------------------

@dataclass
class State:
    problems: list[str]
    remote: bool
    mount_enabled: bool
    mount_active: bool
    mounted: bool
    sync_enabled: bool
    sync_initialized: bool
    user_unit_copies: list[str]
    packaged_units: bool
    drive_problem: str | None = None    # Flatpak: why the drive cannot be enabled
    daemon_alive: bool | None = None    # Flatpak: the background service runs (None elsewhere)

    @property
    def configured(self) -> bool:
        """The account is connected. Drive and sync are optional (the wizard
        lets you skip them and Settings can toggle them later), so they must
        not decide whether the first-run wizard shows again."""
        return self.remote


def state() -> State:
    return State(
        problems=preflight(),
        remote=remote_exists(),
        mount_enabled=is_enabled(MOUNT_UNIT),
        mount_active=is_active(MOUNT_UNIT),
        mounted=is_mounted(),
        sync_enabled=is_enabled(SYNC_TIMER),
        sync_initialized=bisync_initialized(),
        user_unit_copies=user_unit_copies(),
        packaged_units=packaged_units_available(),
        drive_problem=drive_problem(),
        daemon_alive=daemon_alive(),
    )


def cleanup_legacy() -> bool:
    """Remove the per-user launcher/icon written by the old install.sh
    (they duplicate the packaged ones). Returns True if anything was removed."""
    if is_flatpak() or not ICON_SYSTEM.is_file():
        return False
    removed = False
    if LEGACY_LAUNCHER.exists():
        LEGACY_LAUNCHER.unlink()
        run(["update-desktop-database", str(LEGACY_LAUNCHER.parent)])
        removed = True
    if ICON_USER.exists():
        ICON_USER.unlink()
        folder_icon_set()  # re-point the folder icon at the system icon
        removed = True
    return removed


# --- Full install / uninstall (CLI and wizard share these) --------------------

def install_all(token: str | None, log: LogCb = _noop, with_pydrime: bool = False) -> None:
    problems = preflight()
    if problems:
        raise RuntimeError("\n".join(problems))
    if not remote_exists():
        if not token:
            raise RuntimeError("A Drime API token is required.")
        create_remote(token)
        log(f"Remote '{REMOTE}:' created")
    if not check_remote():
        raise RuntimeError("Cannot reach Drime with the configured token.")
    log("Drime account reachable")

    problem = drive_problem()
    if problem:
        log(f"Virtual drive skipped: {problem}")
    else:
        mount_enable(log)
        bookmark_add()
        if folder_icon_set():
            log("Drime icon applied to the folder")
    cleanup_legacy()

    sync_enable(log)

    if with_pydrime:
        if is_flatpak():
            log("pydrime cannot be installed from inside the Flatpak; run "
                "'python3 -m pip install --user pydrime' on your system instead")
        else:
            run_logged(["python3", "-m", "pip", "install", "--user", "--quiet", "pydrime"], log)
            log("pydrime installed - run 'pydrime init' and paste the same API token")


def uninstall_all(purge_config: bool = False, log: LogCb = _noop) -> None:
    folder_icon_unset()
    if is_flatpak():
        from . import portal, sandbox
        sandbox.write_config(mount=False, sync=False)
        sandbox.stop_daemon()
        unmount()
        portal.request_background(False)   # drops the login autostart entry
        sandbox.CONFIG_FILE.unlink(missing_ok=True)
        for f in (sandbox.LOG_FILE, sandbox.LOG_FILE.with_suffix(".log.1")):
            f.unlink(missing_ok=True)
        log("Drive unmounted, background service stopped")
    else:
        systemctl("disable", "--now", SYNC_TIMER)
        systemctl("stop", SYNC_SERVICE)
        systemctl("disable", "--now", MOUNT_UNIT)
        unmount()
        for u in UNITS:
            (USER_UNIT_DIR / u).unlink(missing_ok=True)
        systemctl("daemon-reload")
        systemctl("reset-failed")
        log("Drive unmounted, services disabled")

    if LEGACY_LAUNCHER.exists():
        LEGACY_LAUNCHER.unlink()
        run(["update-desktop-database", str(LEGACY_LAUNCHER.parent)])
    ICON_USER.unlink(missing_ok=True)
    bookmark_remove()
    log("Launcher, icon and file-manager bookmark removed")

    shutil.rmtree(RCLONE_CACHE / "vfs" / REMOTE, ignore_errors=True)
    shutil.rmtree(RCLONE_CACHE / "vfsMeta" / REMOTE, ignore_errors=True)
    for p in glob.glob(str(RCLONE_CACHE / "bisync" / "*DrimeSync*")):
        Path(p).unlink(missing_ok=True)
    try:
        MOUNT.rmdir()
    except OSError:
        pass
    shutil.rmtree(WEB_DATA_DIR.parent, ignore_errors=True)
    shutil.rmtree(WEB_CACHE_DIR.parent, ignore_errors=True)
    log("rclone caches, sync state and web app data (login) removed")

    if purge_config:
        delete_remote()
        if is_flatpak():
            log("API token (rclone remote) removed")
        else:
            shutil.rmtree(HOME / ".config/pydrime", ignore_errors=True)
            run(["python3", "-m", "pip", "uninstall", "-y", "-q", "pydrime"])
            log("API token (rclone remote) and pydrime configuration removed")
    log(f"Kept: {SYNC_DIR} and everything in your cloud account")
