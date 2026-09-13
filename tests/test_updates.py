import io
import json
import sys
import urllib.request

import pytest

from drime_desktop import updates

ASSETS = [
    {"name": "drime-desktop-0.4.0-1.fc44.noarch.rpm", "browser_download_url": "https://x/rpm"},
    {"name": "drime-desktop-0.4.0-1.fc44.src.rpm", "browser_download_url": "https://x/srpm"},
    {"name": "drime-desktop_0.4.0_all.deb", "browser_download_url": "https://x/deb"},
    {"name": "drime-desktop-0.4.0.tar.gz", "browser_download_url": "https://x/tar"},
    {"name": "drime-desktop-0.4.0.flatpak", "browser_download_url": "https://x/flatpak"},
]


@pytest.fixture
def github_release(monkeypatch):
    data = {"tag_name": "v0.4.0", "html_url": "https://x/release", "assets": ASSETS}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=0: Response(json.dumps(data).encode()))
    return data


@pytest.mark.parametrize("distro, url", [
    ("fedora", "https://x/rpm"),
    ("debian", "https://x/deb"),
    ("unknown", None),
])
def test_fetch_latest_picks_the_asset_for_the_distro(github_release, fake_distro, distro, url):
    fake_distro(distro)
    rel = updates.fetch_latest()
    assert rel.version == "0.4.0"
    assert rel.package_url == url
    assert rel.html_url == "https://x/release"


def test_fetch_latest_picks_the_flatpak_bundle_in_a_flatpak(github_release, fake_flatpak, fake_distro):
    fake_distro("fedora")   # the host distro must not matter
    assert updates.package_family() == "flatpak"
    assert updates.fetch_latest().package_url == "https://x/flatpak"


def test_version_key_orders_numerically():
    key = updates._version_key
    assert key("0.3.11") > key("0.3.9")
    assert key("0.4.0") > key("0.3.11")
    assert key("1.0") > key("0.99.99")


@pytest.fixture
def no_native_compare(monkeypatch):
    """Neither python3-rpm nor python3-apt importable: the pure-Python fallback runs."""
    monkeypatch.setitem(sys.modules, "rpm", None)
    monkeypatch.setitem(sys.modules, "apt_pkg", None)


def test_is_newer_fallback(no_native_compare):
    assert updates.is_newer("0.3.11", "0.3.9")
    assert updates.is_newer("0.4.0", "0.3.11")
    assert not updates.is_newer("0.3.11", "0.3.11")
    assert not updates.is_newer("0.3.9", "0.3.11")


def test_is_newer_with_apt(monkeypatch):
    pytest.importorskip("apt_pkg")
    monkeypatch.setitem(sys.modules, "rpm", None)
    assert updates.is_newer("0.3.11", "0.3.9")
    assert not updates.is_newer("0.3.9", "0.3.11")
    assert not updates.is_newer("0.3.11", "0.3.11")


def test_package_suffixes_match_the_release_filenames():
    assert ASSETS[0]["name"].endswith(updates.PACKAGE_SUFFIX["fedora"])
    assert ASSETS[2]["name"].endswith(updates.PACKAGE_SUFFIX["debian"])
    assert ASSETS[4]["name"].endswith(updates.PACKAGE_SUFFIX["flatpak"])
    assert not ASSETS[1]["name"].endswith(updates.PACKAGE_SUFFIX["fedora"])  # not the src.rpm
