"""The version comes from the spec in a checkout and from the stamped __init__.py in a package."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import drime_desktop
from drime_desktop import updates

ROOT = Path(__file__).resolve().parents[1]


def spec_version() -> str:
    return re.search(r"^Version:\s*(\S+)", (ROOT / "drime-desktop.spec").read_text(), re.M).group(1)


def test_checkout_reads_version_from_spec():
    assert drime_desktop.__version__ == spec_version()


def test_checkout_has_no_installed_version():
    assert updates.installed_version() is None


def test_stamped_package_reports_its_version(tmp_path):
    """What the RPM and DEB builds do: substitute @VERSION@ in __init__.py."""
    pkg = tmp_path / "drime_desktop"
    shutil.copytree(ROOT / "src" / "drime_desktop", pkg)
    init = pkg / "__init__.py"
    init.write_text(init.read_text().replace("@VERSION@", "1.2.3"))
    out = subprocess.run(
        [sys.executable, "-c",
         "import drime_desktop, drime_desktop.updates as u; print(drime_desktop.__version__, u.installed_version())"],
        cwd=tmp_path, env={"PYTHONPATH": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["1.2.3", "1.2.3"]


def test_changelog_starts_with_current_version():
    text = (ROOT / "drime-desktop.spec").read_text()
    first = re.search(r"^\* .* - (\S+)-\d+$", text[text.index("%changelog"):], re.M).group(1)
    assert first == spec_version()


def test_flatpak_build_script_stamps_the_version(tmp_path):
    """What the Flatpak build does: flatpak/build-app.sh installs into a prefix."""
    prefix = tmp_path / "app"
    subprocess.run(["sh", "flatpak/build-app.sh", str(prefix)], cwd=ROOT, check=True)
    out = subprocess.run(
        [str(prefix / "bin/drime-desktop"), "--version"],
        env={"PYTHONPATH": str(prefix / "lib/drime-desktop"), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["drime-desktop", spec_version()]
    app_id = "io.github.davethegamedev.DrimeDesktop"
    desktop = (prefix / f"share/applications/{app_id}.desktop").read_text()
    assert f"Icon={app_id}\n" in desktop
    icons = prefix / "share/icons/hicolor/512x512/apps"
    assert (icons / f"{app_id}.png").is_file() and (icons / "drime-desktop.png").is_file()
    assert (prefix / f"share/metainfo/{app_id}.metainfo.xml").is_file()


def test_metainfo_release_matches_the_version():
    text = (ROOT / "assets/io.github.davethegamedev.DrimeDesktop.metainfo.xml").read_text()
    assert re.search(r'<release version="([^"]+)"', text).group(1) == spec_version()
