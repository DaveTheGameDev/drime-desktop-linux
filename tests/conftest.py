import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drime_desktop import backend, sandbox  # noqa: E402


@pytest.fixture
def fake_distro(monkeypatch):
    """Pretend to run on a given distribution family: fake_distro("debian")."""
    def set_distro(name: str):
        monkeypatch.setattr(backend, "distro", lambda: name)
    return set_distro


@pytest.fixture
def fake_flatpak(monkeypatch, tmp_path):
    """Pretend to run inside the Flatpak: /.flatpak-info exists, the service's files live
    under tmp_path, the host has fusermount3 (fake_flatpak(host_fuse=False) to change that)."""
    info = tmp_path / ".flatpak-info"
    info.write_text("[Application]\nname=io.github.davethegamedev.DrimeDesktop\n")
    monkeypatch.setattr(backend, "FLATPAK_INFO", info)
    monkeypatch.setattr(backend, "HOST_OS_RELEASE", tmp_path / "host-os-release")
    monkeypatch.setattr(backend, "ICON_APP", tmp_path / "app-icon.png")
    monkeypatch.setattr(backend, "ICON_USER", tmp_path / "data/icons/drime.png")
    monkeypatch.setattr(sandbox, "CONFIG_FILE", tmp_path / "config/daemon.json")
    monkeypatch.setattr(sandbox, "LOG_FILE", tmp_path / "cache/daemon.log")
    monkeypatch.setattr(sandbox, "RUN_DIR", tmp_path / "run")
    backend.host_has_fusermount3.cache_clear()
    monkeypatch.setattr(backend, "host_has_fusermount3", lambda: True)

    def configure(host_fuse: bool = True):
        monkeypatch.setattr(backend, "host_has_fusermount3", lambda: host_fuse)
    return configure
