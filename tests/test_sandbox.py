"""The file contract between the app and its background service (Flatpak)."""
import sys

from drime_desktop import backend, sandbox


def test_config_defaults_and_round_trip(fake_flatpak):
    assert sandbox.read_config() == {"mount": False, "sync": False}
    assert sandbox.write_config(mount=True) == {"mount": True, "sync": False}
    assert sandbox.write_config(sync=True) == {"mount": True, "sync": True}
    assert sandbox.read_config() == {"mount": True, "sync": True}
    assert not list(sandbox.CONFIG_FILE.parent.glob("*.tmp"))   # written atomically


def test_config_ignores_garbage(fake_flatpak):
    sandbox.CONFIG_FILE.parent.mkdir(parents=True)
    sandbox.CONFIG_FILE.write_text("not json")
    assert sandbox.read_config() == {"mount": False, "sync": False}
    sandbox.CONFIG_FILE.write_text('{"mount": 1, "other": true}')
    assert sandbox.read_config() == {"mount": True, "sync": False}


def test_status_round_trip(fake_flatpak):
    assert sandbox.read_status() == {}
    sandbox.write_status({"mount": {"running": True}})
    assert sandbox.read_status() == {"mount": {"running": True}}


def test_lock_tells_whether_the_service_runs(fake_flatpak):
    assert not sandbox.daemon_alive()
    lock = sandbox.DaemonLock()
    assert lock.acquire()
    assert sandbox.daemon_alive()
    assert not sandbox.DaemonLock().acquire()   # a second service does not start
    lock.release()
    assert not sandbox.daemon_alive()


def test_triggers(fake_flatpak):
    assert not sandbox.consume("sync-now")
    sandbox.trigger("sync-now")
    assert sandbox.pending("sync-now")
    assert sandbox.consume("sync-now")
    assert not sandbox.consume("sync-now")


def test_log_tail(fake_flatpak):
    assert sandbox.log_tail() == ""
    sandbox.LOG_FILE.parent.mkdir(parents=True)
    sandbox.LOG_FILE.write_text("".join(f"line {i}\n" for i in range(500)))
    tail = sandbox.log_tail(3).splitlines()
    assert tail == ["line 497", "line 498", "line 499"]
    assert len(sandbox.log_tail(60).splitlines()) == 60


def test_spawn_command(fake_flatpak, monkeypatch, tmp_path):
    assert sandbox.spawn_command() == ["flatpak-spawn", "drime-desktop", "--daemon"]
    monkeypatch.setattr(backend, "FLATPAK_INFO", tmp_path / "missing")
    assert sandbox.spawn_command() == [sys.executable, "-m", "drime_desktop.cli", "--daemon"]


def test_run_dir_selection(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "rt"))
    monkeypatch.setattr(backend, "FLATPAK_INFO", tmp_path / "missing")
    assert sandbox._run_dir() == tmp_path / "rt/drime-desktop"
    info = tmp_path / ".flatpak-info"
    info.write_text("")
    monkeypatch.setattr(backend, "FLATPAK_INFO", info)
    assert sandbox._run_dir() == tmp_path / "rt/app/io.github.davethegamedev.DrimeDesktop"
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert sandbox._run_dir() == backend.CACHE_DIR / "run"
