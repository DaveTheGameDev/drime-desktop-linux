"""`drime-desktop --daemon`: the background service of the Flatpak.

Does what the systemd units do on RPM/DEB: holds `rclone mount` for ~/Drime
(restarting it with backoff when it dies) and runs `rclone bisync` for
~/DrimeSync every 15 minutes. What is enabled comes from daemon.json, status
goes to status.json, output to daemon.log (see sandbox.py). One instance at a
time (flock); exits by itself when nothing is enabled, on the `stop` trigger, or
on SIGTERM. The `Daemon` class is driven by tick() and has no GTK/GLib
dependency, so it is unit-testable; main() wires it to a GLib main loop.
"""
from __future__ import annotations

import os
import queue
import random
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__, backend, sandbox

BISYNC = ["rclone", "bisync", str(backend.SYNC_DIR), backend.SYNC_REMOTE_PATH,
          "--size-only", "--create-empty-src-dirs", "--check-access", "--resilient", "--recover"]
MOUNT_CMD = ["rclone", "mount", f"{backend.REMOTE}:", str(backend.MOUNT),
             "--vfs-cache-mode", "full", "--vfs-cache-max-size", "10G",
             "--dir-cache-time", "1h", "--umask", "022"]
UNMOUNT = ["fusermount3", "-uz", str(backend.MOUNT)]

FIRST_RUN_DELAY = 120      # drime-bisync.timer OnBootSec=2min
SYNC_INTERVAL = 900        # OnUnitActiveSec=15min
SYNC_JITTER = 60           # RandomizedDelaySec=1min
OFFLINE_RETRY = 60
BACKOFF = (10, 20, 40, 80, 160, 300)   # rclone-drime-mount.service RestartSec=10, growing
HEALTHY_AFTER = 60         # seconds of uptime after which the backoff resets
MOUNT_READY_TIMEOUT = 30
MOUNT_PROBE_EVERY = 30
STOP_GRACE = 10
SYNC_STOP_GRACE = 20
STATUS_EVERY = 30
LOG_MAX = 1024 * 1024
TICK = 2


class Log:
    """Append-only log with ISO timestamps and a one-file rotation."""

    def __init__(self, path: Path = sandbox.LOG_FILE):
        self.path = path
        self._lock = threading.Lock()

    def __call__(self, line: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8", errors="replace") as f:
                    f.write(f"{stamp} {line}\n")
            except OSError:
                pass

    def rotate(self) -> None:
        try:
            if self.path.stat().st_size > LOG_MAX:
                os.replace(self.path, self.path.with_name(self.path.name + ".1"))
        except OSError:
            pass


class Daemon:
    def __init__(self, clock: Callable[[], float] = time.time,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 online: Callable[[], bool] = lambda: True,
                 log: Callable[[str], None] | None = None,
                 mounted: Callable[[], bool] = backend.is_mounted,
                 run: Callable[[list[str]], int] | None = None):
        self.clock, self.popen, self.online = clock, popen, online
        self.log = log or Log()
        self.mounted = mounted
        self.run = run or (lambda cmd: subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                                      stderr=subprocess.DEVNULL).returncode)
        self.started = clock()
        self.cfg = sandbox.read_config()
        self._cfg_mtime = self._config_mtime()
        self.stopping = False
        self.finished = False
        # mount
        self.mount_proc: subprocess.Popen | None = None
        self.mount_since: float | None = None
        self.mount_ready = False
        self.mount_restart_at: float | None = None
        self.mount_failures = 0
        self.mount_last_exit: int | None = None
        self._mount_probe_at = 0.0
        self._foreign_mount_logged = False
        # sync
        self.sync_thread: threading.Thread | None = None
        self.sync_results: queue.Queue = queue.Queue()
        self.sync_proc: subprocess.Popen | None = None
        self.sync_last_start: int | None = None
        self.sync_last_end: int | None = None
        self.sync_last_result = ""
        self.sync_next: float | None = self.started + FIRST_RUN_DELAY if self.cfg["sync"] else None
        self._status_at = 0.0
        self._dirty = True

    # --- main loop ------------------------------------------------------------

    def tick(self, now: float | None = None) -> bool:
        """One round of supervision; returns False once the service should exit."""
        now = self.clock() if now is None else now
        if self.finished:
            return False
        self._reload_config(now)
        if sandbox.consume("stop"):
            self.log("stop requested")
            self.shutdown()
            return False
        if sandbox.consume("sync-now"):
            if self.sync_running:
                self.log("[sync] already running, sync-now ignored")
            else:
                self.log("[sync] sync-now requested")
                self.run_sync(now)
        self._drain_sync_results(now)
        self._supervise_mount(now)
        self._schedule_sync(now)
        if self._dirty or now - self._status_at >= STATUS_EVERY:
            self.write_status(now)
        if self.idle:
            self.log("nothing enabled, exiting")
            self.shutdown()
            return False
        return True

    @property
    def sync_running(self) -> bool:
        return self.sync_thread is not None and self.sync_thread.is_alive()

    @property
    def idle(self) -> bool:
        return (not self.cfg["mount"] and not self.cfg["sync"] and not self.sync_running
                and self.mount_proc is None and not sandbox.pending("sync-now"))

    def _config_mtime(self) -> float | None:
        try:
            return sandbox.CONFIG_FILE.stat().st_mtime
        except OSError:
            return None

    def _reload_config(self, now: float) -> None:
        mtime = self._config_mtime()
        if mtime == self._cfg_mtime:
            return
        self._cfg_mtime = mtime
        new = sandbox.read_config()
        if new == self.cfg:
            return
        self.log(f"config: mount={'on' if new['mount'] else 'off'} sync={'on' if new['sync'] else 'off'}")
        if new["sync"] and not self.cfg["sync"]:
            self.sync_next = now + FIRST_RUN_DELAY
        if not new["sync"]:
            self.sync_next = None
        if new["mount"] and not self.cfg["mount"]:
            self.mount_failures, self.mount_restart_at = 0, None
        self.cfg = new
        self._dirty = True

    # --- mount ------------------------------------------------------------------

    def _supervise_mount(self, now: float) -> None:
        proc = self.mount_proc
        if not self.cfg["mount"]:
            if proc is not None:
                self._stop_mount()
            return
        if proc is None:
            if self.mount_restart_at is not None and now < self.mount_restart_at:
                return
            if self.mounted():
                if not self._foreign_mount_logged:
                    self.log(f"{backend.MOUNT} is already mounted (another Drime install?); leaving it alone")
                    self._foreign_mount_logged = True
                return
            self._foreign_mount_logged = False
            self._start_mount(now)
            return
        rc = proc.poll()
        if rc is not None:
            self.mount_proc, self.mount_last_exit = None, rc
            healthy = self.mount_ready and self.mount_since is not None and now - self.mount_since >= HEALTHY_AFTER
            self.mount_failures = 0 if healthy else self.mount_failures + 1
            delay = self._backoff()
            self.mount_restart_at = now + delay
            self.log(f"[mount] rclone exited with code {rc}; restarting in {delay} s")
            self.run(UNMOUNT)
            self.mount_ready = False
            self._dirty = True
            return
        if not self.mount_ready:
            if self.mounted():
                self.mount_ready = True
                self.log(f"[mount] {backend.MOUNT} is mounted")
                self._dirty = True
            elif self.mount_since is not None and now - self.mount_since > MOUNT_READY_TIMEOUT:
                self.log("[mount] not mounted after %d s, giving up on this attempt" % MOUNT_READY_TIMEOUT)
                self._kill_mount(proc)
            return
        if now - self._mount_probe_at >= MOUNT_PROBE_EVERY:
            self._mount_probe_at = now
            if backend.is_stale_mount():
                self.log("[mount] mountpoint is stale (transport endpoint not connected); restarting")
                self._kill_mount(proc)

    def _backoff(self) -> int:
        """Delay before the next mount attempt: 10 s after a healthy run or the first
        failure (RestartSec=10), doubling with consecutive failures up to 5 min."""
        return BACKOFF[min(max(self.mount_failures - 1, 0), len(BACKOFF) - 1)]

    def _start_mount(self, now: float) -> None:
        self.run(UNMOUNT)   # a stale mountpoint blocks every new mount, like ExecStartPre=-fusermount3 -uz
        try:
            backend.MOUNT.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.log(f"[mount] cannot create {backend.MOUNT}: {e}")
        self.log("[mount] $ " + " ".join(MOUNT_CMD))
        try:
            self.mount_proc = self.popen(MOUNT_CMD, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as e:
            self.log(f"[mount] cannot start rclone: {e}")
            self.mount_failures += 1
            self.mount_restart_at = now + self._backoff()
            return
        self.mount_since, self.mount_ready, self.mount_restart_at = now, False, None
        self._mount_probe_at = now
        self._pump(self.mount_proc, "[mount] ")
        self._dirty = True

    def _kill_mount(self, proc: subprocess.Popen) -> None:
        self.run(UNMOUNT)
        try:
            proc.terminate()
        except OSError:
            pass
        # the next tick sees the exit and schedules the restart

    def _stop_mount(self) -> None:
        proc = self.mount_proc
        if proc is None:
            return
        self.log("[mount] stopping")
        self.run(UNMOUNT)
        try:
            proc.terminate()
            try:
                proc.wait(STOP_GRACE)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        except OSError:
            pass
        self.mount_proc, self.mount_ready, self.mount_restart_at = None, False, None
        self._dirty = True

    # --- sync -----------------------------------------------------------------

    def _schedule_sync(self, now: float) -> None:
        if not self.cfg["sync"]:
            self.sync_next = None
            return
        if self.sync_next is None:
            self.sync_next = now + FIRST_RUN_DELAY
        if now >= self.sync_next and not self.sync_running:
            if not self.online():
                self.sync_next = now + OFFLINE_RETRY
                return
            self.run_sync(now)

    def run_sync(self, now: float) -> None:
        self.sync_last_start = int(now)
        self.sync_next = None
        self._dirty = True
        self.log("[sync] $ " + " ".join(BISYNC))

        def worker():
            try:
                proc = self.popen(BISYNC, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
            except OSError as e:
                self.log(f"[sync] cannot start rclone: {e}")
                self.sync_results.put(127)
                return
            self.sync_proc = proc
            for line in proc.stdout or ():
                self.log("[sync] " + line.rstrip("\n"))
            self.sync_results.put(proc.wait())
            self.sync_proc = None

        self.sync_thread = threading.Thread(target=worker, daemon=True)
        self.sync_thread.start()

    def _drain_sync_results(self, now: float) -> None:
        while True:
            try:
                rc = self.sync_results.get_nowait()
            except queue.Empty:
                return
            self.sync_last_end = int(now)
            self.sync_last_result = "success" if rc == 0 else f"exit-code {rc}"
            self.log(f"[sync] finished: {self.sync_last_result}")
            if self.cfg["sync"]:
                self.sync_next = now + SYNC_INTERVAL + random.uniform(0, SYNC_JITTER)
            self.log_rotate()
            self._dirty = True

    def log_rotate(self) -> None:
        if isinstance(self.log, Log):
            self.log.rotate()

    # --- output ---------------------------------------------------------------

    def _pump(self, proc: subprocess.Popen, prefix: str) -> None:
        """Forward a child's output to the log from a thread."""
        if proc.stdout is None:
            return

        def reader():
            for line in proc.stdout:
                self.log(prefix + line.rstrip("\n"))
        threading.Thread(target=reader, daemon=True).start()

    def status(self, now: float) -> dict:
        return {
            "version": __version__,
            "pid": os.getpid(),
            "started": int(self.started),
            "updated": int(now),
            "mount": {
                "enabled": self.cfg["mount"],
                "running": self.mount_proc is not None and self.mount_ready,
                "starting": self.mount_proc is not None and not self.mount_ready,
                "since": int(self.mount_since) if self.mount_since else None,
                "restarts": self.mount_failures,
                "last_exit": self.mount_last_exit,
                "retry_at": int(self.mount_restart_at) if self.mount_restart_at else None,
            },
            "sync": {
                "enabled": self.cfg["sync"],
                "running": self.sync_running,
                "last_start": self.sync_last_start,
                "last_end": self.sync_last_end,
                "last_result": self.sync_last_result,
                "next_run": int(self.sync_next) if self.sync_next else None,
            },
        }

    def write_status(self, now: float) -> None:
        sandbox.write_status(self.status(now))
        self._status_at, self._dirty = now, False

    # --- shutdown -----------------------------------------------------------------

    def shutdown(self) -> None:
        if self.finished:
            return
        self.stopping = True
        self._stop_mount()
        if self.sync_running:
            self.log("[sync] waiting for the running sync")
            self.sync_thread.join(SYNC_STOP_GRACE)
            proc = self.sync_proc
            if self.sync_running and proc is not None:
                self.log("[sync] interrupting it (rclone resumes next time with --resilient --recover)")
                try:
                    proc.terminate()
                except OSError:
                    pass
                self.sync_thread.join(5)
        self._drain_sync_results(self.clock())
        self.finished = True
        self.write_status(self.clock())
        self.log("stopped")


def main() -> int:
    """Entry point of `drime-desktop --daemon`."""
    for d in (sandbox.RUN_DIR, backend.CONFIG_DIR, backend.CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    lock = sandbox.DaemonLock()
    if not lock.acquire():
        return 0   # already running
    sandbox.consume("stop")   # a leftover from a previous stop must not kill us at once
    log = Log()
    log.rotate()
    log(f"drime-desktop {__version__} background service starting (pid {os.getpid()})")

    from gi.repository import Gio, GLib

    monitor = Gio.NetworkMonitor.get_default()
    daemon = Daemon(online=monitor.get_network_available, log=log)
    loop = GLib.MainLoop()

    def on_tick():
        if daemon.tick():
            return True
        loop.quit()
        return False

    def on_signal(*_):
        log("signal received")
        daemon.shutdown()
        loop.quit()
        return False

    GLib.timeout_add_seconds(TICK, on_tick)
    GLib.idle_add(on_tick)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, on_signal)
    try:
        loop.run()
    finally:
        daemon.shutdown()
        lock.release()
    return 0
