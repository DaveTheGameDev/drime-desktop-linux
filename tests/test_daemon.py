"""The background service's supervision and scheduling, with a fake clock and fake rclone."""
import io
import subprocess
import threading

import pytest

from drime_desktop import daemon, sandbox


class FakeProc:
    """A fake subprocess.Popen: alive until exit() / terminate() / kill() is called."""

    def __init__(self, cmd, **_kw):
        self.cmd = cmd
        self.returncode = None
        self.stdout = io.StringIO("")
        self.terminated = self.killed = False
        self._done = threading.Event()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return self.returncode

    def _finish(self, code):
        self.returncode = code
        self._done.set()

    def terminate(self):
        self.terminated = True
        self._finish(-15)

    def kill(self):
        self.killed = True
        self._finish(-9)

    def exit(self, code):
        self._finish(code)


class Harness:
    def __init__(self, mounted=None, online=lambda: True):
        self.now = 1_000_000.0
        self.procs = []
        self.ran = []
        self.lines = []
        # By default ~/Drime counts as mounted exactly while our fake rclone mount is alive.
        self._mounted = mounted or (lambda: any(p.returncode is None for p in self.mounts))
        self.d = daemon.Daemon(clock=lambda: self.now, popen=self.popen, online=online,
                               log=self.lines.append, mounted=lambda: self._mounted(),
                               run=lambda cmd: self.ran.append(cmd) or 0)

    def popen(self, cmd, **kw):
        p = FakeProc(cmd, **kw)
        self.procs.append(p)
        return p

    def tick(self, advance=0.0):
        self.now += advance
        return self.d.tick(self.now)

    @property
    def mounts(self):
        return [p for p in self.procs if p.cmd[:2] == ["rclone", "mount"]]

    @property
    def syncs(self):
        return [p for p in self.procs if p.cmd[:2] == ["rclone", "bisync"]]


@pytest.fixture
def h(fake_flatpak):
    return Harness()


def finish_sync(h, code=0):
    """Let the fake bisync finish and its worker thread hand back the result."""
    p = h.syncs[-1]
    p.exit(code)
    h.d.sync_thread.join(2)


def test_sync_first_run_and_interval(h):
    sandbox.write_config(sync=True)
    assert h.tick()
    assert h.syncs == []
    assert h.d.sync_next == h.now + daemon.FIRST_RUN_DELAY
    h.tick(daemon.FIRST_RUN_DELAY - 1)
    assert h.syncs == []
    h.tick(1)
    assert len(h.syncs) == 1
    assert h.syncs[0].cmd == daemon.BISYNC
    assert h.d.status(h.now)["sync"]["running"]
    finish_sync(h)
    h.tick(1)
    assert h.d.sync_last_result == "success"
    assert daemon.SYNC_INTERVAL <= h.d.sync_next - h.now <= daemon.SYNC_INTERVAL + daemon.SYNC_JITTER
    finished_at = h.now
    h.tick(daemon.SYNC_INTERVAL + daemon.SYNC_JITTER + 1)
    assert len(h.syncs) == 2
    finish_sync(h, 7)
    h.tick(1)
    assert h.d.sync_last_result == "exit-code 7"
    assert h.d.sync_last_end > finished_at


def test_sync_now_runs_at_once_and_only_once(h):
    sandbox.write_config(sync=True)
    h.tick()
    sandbox.trigger("sync-now")
    h.tick(1)
    assert len(h.syncs) == 1
    h.tick(1)
    assert len(h.syncs) == 1   # consumed
    sandbox.trigger("sync-now")
    h.tick(1)
    assert len(h.syncs) == 1 and "already running" in h.lines[-1]


def test_sync_now_works_with_sync_off_then_the_service_exits(h):
    sandbox.trigger("sync-now")
    assert h.tick()
    assert len(h.syncs) == 1
    finish_sync(h)
    assert not h.tick(1)        # nothing enabled: idle exit
    assert h.d.finished


def test_sync_waits_for_the_network(h):
    online = {"v": False}
    h = Harness(online=lambda: online["v"])
    sandbox.write_config(sync=True)
    h.tick()
    h.tick(daemon.FIRST_RUN_DELAY)
    assert h.syncs == [] and h.d.sync_next == h.now + daemon.OFFLINE_RETRY
    online["v"] = True
    h.tick(daemon.OFFLINE_RETRY)
    assert len(h.syncs) == 1


def test_disabling_sync_clears_the_schedule(h):
    sandbox.write_config(sync=True)
    h.tick()
    sandbox.write_config(sync=False)
    h.tick(1)
    assert h.d.sync_next is None and h.d.status(h.now)["sync"]["next_run"] is None


def test_mount_starts_after_a_stale_cleanup(fake_flatpak):
    state = {"mounted": False}
    h = Harness(mounted=lambda: state["mounted"])
    sandbox.write_config(mount=True)
    h.tick()
    assert h.ran == [daemon.UNMOUNT]              # ExecStartPre=-fusermount3 -uz
    assert len(h.mounts) == 1 and h.mounts[0].cmd == daemon.MOUNT_CMD
    st = h.d.status(h.now)["mount"]
    assert st["starting"] and not st["running"]
    state["mounted"] = True
    h.tick(2)
    assert h.d.status(h.now)["mount"]["running"]


def test_mount_restarts_with_backoff_and_resets_when_healthy(fake_flatpak):
    state = {"mounted": False}
    h = Harness(mounted=lambda: state["mounted"])
    sandbox.write_config(mount=True)
    h.tick()
    delays = []
    for expected in (10, 20, 40, 80, 160, 300, 300):
        h.mounts[-1].exit(1)
        n = len(h.mounts)
        h.tick(1)
        delays.append(h.d.mount_restart_at - h.now)
        h.tick(expected - 1)
        assert len(h.mounts) == n            # not yet
        h.tick(1)
        assert len(h.mounts) == n + 1        # restarted
    assert delays == [10, 20, 40, 80, 160, 300, 300]
    # A mount that stays up for a minute resets the counter.
    state["mounted"] = True
    h.tick(1)
    h.tick(daemon.HEALTHY_AFTER)
    h.mounts[-1].exit(1)
    state["mounted"] = False
    h.tick(1)
    assert h.d.mount_restart_at - h.now == 10 and h.d.mount_failures == 0


def test_mount_gives_up_an_attempt_that_never_mounts(fake_flatpak):
    h = Harness(mounted=lambda: False)
    sandbox.write_config(mount=True)
    h.tick()
    h.tick(daemon.MOUNT_READY_TIMEOUT + 1)
    assert h.mounts[0].terminated
    h.tick(1)
    assert h.d.mount_restart_at is not None


def test_existing_mount_is_left_alone(fake_flatpak):
    h = Harness(mounted=lambda: True)
    sandbox.write_config(mount=True)
    h.tick()
    assert h.mounts == [] and any("already mounted" in l for l in h.lines)


def test_disabling_the_mount_unmounts_and_stops_rclone(fake_flatpak):
    h = Harness()
    sandbox.write_config(mount=True, sync=True)
    h.tick()
    h.tick(2)
    sandbox.write_config(mount=False)
    h.tick(1)
    assert h.mounts[0].terminated and h.ran[-1] == daemon.UNMOUNT
    assert h.d.mount_proc is None and not h.d.status(h.now)["mount"]["running"]


def test_stop_trigger_shuts_down(fake_flatpak):
    h = Harness()
    sandbox.write_config(mount=True)
    h.tick()
    h.tick(2)
    sandbox.trigger("stop")
    assert not h.tick(1)
    assert h.d.finished and h.mounts[0].terminated
    assert not h.tick(1)
    st = sandbox.read_status()
    assert not st["mount"]["running"] and st["mount"]["enabled"]


def test_idle_service_exits(h):
    assert not h.tick()
    assert h.d.finished


def test_status_file_contents(fake_flatpak):
    h = Harness()
    sandbox.write_config(mount=True, sync=True)
    h.tick()
    st = sandbox.read_status()
    assert st["mount"]["enabled"] and st["sync"]["enabled"]
    assert st["sync"]["next_run"] == int(h.now + daemon.FIRST_RUN_DELAY)
    assert st["sync"]["last_result"] == "" and st["sync"]["last_end"] is None
    assert set(st) >= {"version", "pid", "started", "updated", "mount", "sync"}


def test_commands_match_the_systemd_units():
    """The daemon must run exactly what the units run on RPM/DEB."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    def flags(unit):
        text = (root / "systemd" / unit).read_text()
        exec_ = text[text.index("ExecStart="):].split("\n\n")[0]
        exec_ = exec_.replace("\\\n", " ").split("ExecStop")[0]
        return [t for t in exec_.split()[2:] if t.startswith("--")]
    assert [t for t in daemon.MOUNT_CMD if t.startswith("--")] == flags("rclone-drime-mount.service")
    assert [t for t in daemon.BISYNC if t.startswith("--")] == flags("drime-bisync.service")
