import shutil
import subprocess

import pytest

from drime_desktop import backend


@pytest.mark.parametrize("os_release, expected", [
    ('ID=ubuntu\nID_LIKE=debian\n', "debian"),
    ('ID=debian\n', "debian"),
    ('ID=linuxmint\nID_LIKE="ubuntu debian"\n', "debian"),
    ('ID=fedora\n', "fedora"),
    ('ID="rhel"\nID_LIKE="fedora"\n', "fedora"),
    ('ID=arch\n', "unknown"),
])
def test_distro_from_os_release(monkeypatch, tmp_path, os_release, expected):
    f = tmp_path / "os-release"
    f.write_text(f'NAME="Something"\n{os_release}VERSION_ID="1"\n')
    monkeypatch.setattr(backend, "OS_RELEASE", f)
    assert backend.distro() == expected


def test_distro_without_os_release(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "OS_RELEASE", tmp_path / "missing")
    assert backend.distro() == "unknown"


def test_hints_fedora(fake_distro):
    fake_distro("fedora")
    assert backend.install_hint("fuse3") == "sudo dnf install fuse3"
    assert backend.install_hint("rclone") == "sudo dnf install rclone"
    assert backend.install_hint("rclone", upgrade=True) == "sudo dnf upgrade rclone"
    assert backend.remove_hint() == "sudo dnf remove drime-desktop"


def test_hints_debian(fake_distro):
    fake_distro("debian")
    assert backend.install_hint("fuse3") == "sudo apt install fuse3"
    # The archive's rclone is too old for the Drime backend, so point at rclone.org.
    assert "rclone.org" in backend.install_hint("rclone")
    assert "rclone.org" in backend.install_hint("rclone", upgrade=True)
    assert backend.remove_hint() == "sudo apt remove drime-desktop"


def test_hints_unknown(fake_distro):
    fake_distro("unknown")
    assert "fuse3" in backend.install_hint("fuse3")
    assert "drime-desktop" in backend.remove_hint()


@pytest.fixture
def broken_system(monkeypatch):
    """rclone and fuse3 missing, systemd user session fine."""
    monkeypatch.setattr(backend, "rclone_version", lambda: None)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(backend, "systemctl",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))


def test_preflight_wording_debian(broken_system, fake_distro):
    fake_distro("debian")
    problems = backend.preflight()
    assert len(problems) == 2
    assert problems[0].startswith("rclone is not installed") and "rclone.org" in problems[0]
    assert problems[1] == "fuse3 is not installed (sudo apt install fuse3)."


def test_preflight_wording_fedora(broken_system, fake_distro):
    fake_distro("fedora")
    assert backend.preflight() == [
        "rclone is not installed (sudo dnf install rclone).",
        "fuse3 is not installed (sudo dnf install fuse3).",
    ]


def test_preflight_old_rclone(broken_system, fake_distro, monkeypatch):
    monkeypatch.setattr(backend, "rclone_version", lambda: (1, 60, 1))
    fake_distro("debian")
    msg = backend.preflight()[0]
    assert msg.startswith("rclone 1.60.1 is too old; the Drime backend needs 1.73.0 or newer")
    assert "rclone.org" in msg
    fake_distro("fedora")
    assert "(sudo dnf upgrade rclone)" in backend.preflight()[0]


def _fake_proc(tmp_path, procs):
    """procs: {pid: (comm, ppid)} -> a /proc look-alike with stat files."""
    for pid, (comm, ppid) in procs.items():
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1 4194560 0\n")
    (tmp_path / "self").mkdir()
    (tmp_path / "meminfo").write_text("MemTotal: 1 kB\n")
    return tmp_path


def test_child_pids_matches_truncated_comm_of_direct_children(tmp_path):
    proc = _fake_proc(tmp_path, {
        100: ("drime-desktop", 1),
        101: ("WebKitNetworkPr", 100),   # /proc truncates the name to 15 chars
        102: ("bwrap", 100),
        103: ("WebKitWebProces", 102),   # grandchild through the sandbox
        104: ("WebKitNetworkPr", 999),   # another app's
        105: ("WebKit (odd) Pr", 100),   # parentheses in the name must not confuse the parser
    })
    assert backend.child_pids("WebKitNetworkProcess", proc, parent=100) == [101]
    assert backend.child_pids("WebKitWebProcess", proc, parent=100) == []
    assert backend.child_pids("WebKitWebProcess", proc, parent=102) == [103]


def test_child_pids_skips_unreadable_entries(tmp_path):
    proc = _fake_proc(tmp_path, {100: ("WebKitNetworkPr", 7)})
    (tmp_path / "200").mkdir()                      # vanished before its stat was read
    (tmp_path / "300").mkdir()
    (tmp_path / "300" / "stat").write_text("garbage")
    assert backend.child_pids("WebKitNetworkProcess", proc, parent=7) == [100]


# --- Flatpak -------------------------------------------------------------------

def test_is_flatpak(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "FLATPAK_INFO", tmp_path / "missing")
    assert not backend.is_flatpak()
    info = tmp_path / ".flatpak-info"
    info.write_text("[Application]\n")
    monkeypatch.setattr(backend, "FLATPAK_INFO", info)
    assert backend.is_flatpak()


def test_distro_prefers_the_host_os_release(monkeypatch, tmp_path):
    """Inside the sandbox /etc/os-release describes the runtime; the host's copy wins."""
    runtime = tmp_path / "os-release"
    runtime.write_text('ID=org.gnome.platform\n')
    host = tmp_path / "host-os-release"
    host.write_text('ID=ubuntu\nID_LIKE=debian\n')
    monkeypatch.setattr(backend, "OS_RELEASE", runtime)
    monkeypatch.setattr(backend, "HOST_OS_RELEASE", host)
    assert backend.distro() == "debian"
    monkeypatch.setattr(backend, "HOST_OS_RELEASE", tmp_path / "none")
    assert backend.distro() == "unknown"


def test_hints_flatpak(fake_flatpak, fake_distro):
    fake_distro("debian")
    assert backend.remove_hint() == "flatpak uninstall io.github.davethegamedev.DrimeDesktop"
    assert backend.install_hint("fuse3") == "sudo apt install fuse3"   # the host's package manager


def test_preflight_flatpak_skips_fuse_and_systemd(fake_flatpak, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(backend, "systemctl", lambda *a, **k: (_ for _ in ()).throw(AssertionError("systemctl")))
    monkeypatch.setattr(backend, "rclone_version", lambda: (1, 75, 1))
    assert backend.preflight() == []
    monkeypatch.setattr(backend, "rclone_version", lambda: None)
    assert backend.preflight() == ["rclone is missing from this Flatpak build (packaging bug)."]


def test_drive_problem_needs_host_fuse(fake_flatpak, fake_distro):
    fake_distro("fedora")
    assert backend.drive_problem() is None
    fake_flatpak(host_fuse=False)
    assert backend.drive_problem() == "The virtual drive needs fuse3 on your system (sudo dnf install fuse3)."


def test_drive_problem_outside_flatpak(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "FLATPAK_INFO", tmp_path / "missing")
    assert backend.drive_problem() is None


def test_mount_enable_flatpak_refuses_without_host_fuse(fake_flatpak):
    fake_flatpak(host_fuse=False)
    with pytest.raises(RuntimeError, match="fuse3"):
        backend.mount_enable()


def test_units_are_absent_in_flatpak(fake_flatpak):
    assert backend.unit_source(backend.MOUNT_UNIT) == "none"
    assert not backend.packaged_units_available()
    assert backend.user_unit_copies() == []
    assert not backend.migrate_user_units()
    backend.ensure_units()   # no-op, must not raise
    assert not backend.cleanup_legacy()


def test_state_flatpak_reads_config_and_status(fake_flatpak, monkeypatch):
    from drime_desktop import sandbox
    monkeypatch.setattr(backend, "rclone_version", lambda: (1, 75, 1))
    monkeypatch.setattr(backend, "remote_exists", lambda: True)
    monkeypatch.setattr(backend, "is_mounted", lambda: False)
    monkeypatch.setattr(backend, "bisync_initialized", lambda: True)
    sandbox.write_config(mount=True, sync=True)
    st = backend.state()
    assert st.mount_enabled and st.sync_enabled
    assert st.daemon_alive is False and not st.mount_active
    assert st.drive_problem is None and st.user_unit_copies == [] and not st.packaged_units
    # With the service holding the lock and reporting a running mount:
    sandbox.write_status({"mount": {"running": True}, "sync": {"running": False, "last_result": "success",
                                                              "last_start": 1, "last_end": 2, "next_run": 3}})
    with sandbox.DaemonLock() as lock:
        assert lock.acquire()
        assert backend.is_active(backend.MOUNT_UNIT)
        ss = backend.sync_status()
        assert (ss.running, ss.last_result, ss.last_end, ss.next_run) == (False, "success", 2, 3)
    assert backend.sync_status().next_run is None   # service gone: nothing is scheduled


def test_icon_path_flatpak_copies_to_a_host_visible_place(fake_flatpak):
    assert backend.icon_path() is None
    backend.ICON_APP.write_bytes(b"png")
    assert backend.icon_path() == backend.ICON_USER
    assert backend.ICON_USER.read_bytes() == b"png"


def test_xdg_paths_follow_the_environment(monkeypatch, tmp_path):
    import importlib
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "k"))
    try:
        importlib.reload(backend)
        assert backend.CONFIG_DIR == tmp_path / "c/drime-desktop"
        assert backend.WEB_DATA_DIR == tmp_path / "d/drime-desktop/web"
        assert backend.RCLONE_CACHE == tmp_path / "k/rclone"
        assert backend.WEB_CACHE_DIR == tmp_path / "k/drime-desktop/web"
        assert backend.BOOKMARKS == backend.HOME / ".config/gtk-3.0/bookmarks"   # always the host file
    finally:
        monkeypatch.delenv("XDG_CONFIG_HOME"); monkeypatch.delenv("XDG_DATA_HOME"); monkeypatch.delenv("XDG_CACHE_HOME")
        importlib.reload(backend)
    assert backend.CONFIG_DIR == backend.HOME / ".config/drime-desktop"
