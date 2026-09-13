# Drime Desktop for Linux (unofficial)

[Drime](https://drime.cloud) only ships its desktop app for Windows and macOS. This project recreates the desktop experience on Linux (Fedora, Ubuntu and Debian) using [rclone's native Drime backend](https://rclone.org/drime/) — an integration [officially announced by Drime](https://drime.cloud/blog-posts/drime-now-supports-native-rclone-integration) — plus a single GNOME app that shows the Drime web app, sets everything up and keeps it running. One package, one icon, no browser to install. No reverse engineering, no Wine: just supported APIs.

<p align="center"><img src="assets/screenshots/main-window.png" alt="The Drime window: the web app with the drive/sync status pill in the title bar" width="800"></p>

## What you get

| Component | What it is | Official-app equivalent |
|---|---|---|
| `~/Drime` | Virtual drive: your whole account mounted on demand with local caching (`rclone mount`, VFS full cache), auto-mounted at login, visible in your file manager's sidebar | The virtual "Drime drive" |
| `~/DrimeSync` | Two-way synced folder mirroring the `Sync` folder of your cloud about every 15 minutes (`rclone bisync` + systemd timer) | Dropbox-style sync folder |
| **Drime** app | One window: the Drime web app (app.drime.cloud, embedded with WebKitGTK) plus status pill and Settings — see [The Drime window](#the-drime-window) | The app's UI + tray menu |
| `pydrime` (optional) | Third-party CLI for scripted uploads/downloads | — |

## Requirements

- **Fedora Workstation** (developed on Fedora 44 / GNOME) — RPM package; or **Ubuntu 24.04 or newer / Debian 13 or newer** with GNOME — DEB package; or **any other distribution with Flatpak** — the `.flatpak` bundle (rclone is included; `fuse3` must be installed on the system for the virtual drive; see [Flatpak limitations](#flatpak-limitations)). Other systemd + GTK4 distributions can also use the [git checkout](#advanced-install-from-a-git-checkout) path.
- A Drime account and an **API token**: app.drime.cloud → **Settings → Developer → create token**
- **rclone 1.73 or newer** (the Drime backend). On Fedora it is pulled in with the RPM. On Ubuntu and Debian the archive's `rclone` is too old (1.60), so install the [official rclone package](https://rclone.org/install/) first: `curl https://rclone.org/install.sh | sudo bash` (or the `.deb` from [rclone.org/downloads](https://rclone.org/downloads/)). The Flatpak bundles its own rclone.
- Everything else (`fuse3`, PyGObject, GTK4/libadwaita, WebKitGTK) is a regular distribution package pulled in automatically when you install the RPM or DEB. Recommended (pulled in by default): PackageKit for one-click updates (`PackageKit-glib` on Fedora, `packagekit` + `gir1.2-packagekitglib-1.0` on Ubuntu/Debian) and `python3-rpm` / `python3-apt` for exact version comparison.

## Install

**Fedora**

1. Download the latest `drime-desktop-<version>.noarch.rpm` from the [Releases page](https://github.com/DaveTheGameDev/drime-desktop-linux/releases).
2. Double-click it — GNOME Software opens and installs it (or run `sudo dnf install ./drime-desktop-*.rpm`).
3. Open **Drime** from your applications. The wizard asks for your API token and turns on the drive and the sync folder. That's it.

**Ubuntu 24.04+ / Debian 13+**

1. Install rclone from rclone.org (the distribution's package is too old): `curl https://rclone.org/install.sh | sudo bash`
2. Download the latest `drime-desktop_<version>_all.deb` from the [Releases page](https://github.com/DaveTheGameDev/drime-desktop-linux/releases).
3. Run `sudo apt install ./drime-desktop_*.deb` (or open the file with your software center).
4. Open **Drime** from your applications and follow the wizard, as above.

**Any other distribution (Flatpak)**

1. Download the latest `drime-desktop-<version>.flatpak` from the [Releases page](https://github.com/DaveTheGameDev/drime-desktop-linux/releases).
2. Open it with your software center, or run `flatpak install --user ./drime-desktop-<version>.flatpak` (it offers to add [Flathub](https://flathub.org/setup) for the GNOME runtime if you don't have it).
3. Install `fuse3` with your package manager if you want the virtual drive (most desktops already have it).
4. Open **Drime** from your applications and follow the wizard. The drive and the sync folder run in Drime's own background service; your desktop may ask whether Drime may run in the background — say yes, or they only run while the window is open. See [Flatpak limitations](#flatpak-limitations).

The token goes straight into `~/.config/rclone/rclone.conf` and nowhere else (in the Flatpak: `~/.var/app/io.github.davethegamedev.DrimeDesktop/config/rclone/rclone.conf`).

## The Drime window

<p align="center"><img src="assets/screenshots/wizard-welcome.png" alt="Setup wizard: welcome" width="260"> <img src="assets/screenshots/wizard-sync.png" alt="Setup wizard: sync folder" width="260"> <img src="assets/screenshots/wizard-done.png" alt="Setup wizard: all set" width="260"></p>

After the first-run wizard, opening **Drime** gives you one window:

- **The web app** fills it: sign in once and you stay signed in (the login is stored under `~/.local/share/drime-desktop`, private to this app). Share links, previews, trash, settings, comments — everything the website does. Files you download land in your Downloads folder (a toast offers to open them); uploads work as on the website, and you can drag files from Files (Nautilus) onto the window to upload them into the folder you are viewing (folders can't be dropped — copy those into `~/Drime` instead). Links that would open a new tab open in your normal browser. Dragging files and folders around inside the page (e.g. into a folder) works as on the website. The window can stay open for days: the app keeps your Drime session alive and renews its security token, so you don't get "CSRF token mismatch" errors after leaving it idle, and it refreshes the folder listing after each change you make, when the window regains focus and about every minute, so moves and undos show up promptly and changes made in a browser, on your phone or through `~/Drime` appear without reloading. (Drime's server takes a few seconds per request, so a move or undo can take that long to show.)
- **The status pill** in the title bar — e.g. *Drive mounted · synced 3 min ago* — is green when all is well and turns orange if the drive is down or the last sync failed. Click it to open Settings. If app.drime.cloud can't be reached, the window shows an offline notice with a *Retry* button.
- **The menu (☰)**: *Open Drime folder*, *Open Sync folder*, *Sync now*, *Open in browser*, *Settings*, *About Drime Desktop*.
- **Settings** (Ctrl+,): switch the virtual drive and the sync folder on/off, see the sync log, check for updates, remove the setup.

The drive and the sync folder run in the background as systemd user services (in the Flatpak: as Drime's own background service) — closing the window doesn't stop them; you only need the window for the web app or to check on things. Shortcuts: Ctrl+R/F5 reload, Ctrl+S sync now, Ctrl+/− zoom (Ctrl+0 resets), Ctrl+Q quit.

The embedded view blocks third-party cookies; if a single-sign-on login fails inside the app, use *Open in browser* for that step.

## Updating

- **Drime Desktop itself**: the app checks GitHub Releases a few seconds after it opens and asks you when a newer version exists (*Download and install*, *Later* or *Skip this version*). You can also check by hand: **Drime → ☰ → Settings → Updates → Check for updates**. If a newer release exists, *Download and install* fetches the package for your distribution (RPM or DEB) to your Downloads folder and installs it through PackageKit — you only confirm with your password. Once the new version is on disk the running window offers to restart. **Flatpak**: the app downloads the new `.flatpak` to your Downloads folder and opens it in your software center (or run `flatpak install --user ~/Downloads/drime-desktop-<version>.flatpak`); close and reopen Drime afterwards. The GNOME runtime itself updates with `flatpak update`.
- **rclone and WebKitGTK** (the web engine) update with your normal system updates. On Ubuntu/Debian, rclone installed from rclone.org is updated by running its install script again. The Flatpak ships its own rclone and WebKitGTK, updated with each Drime release.

Updates replace the systemd units under `/usr/lib/systemd/user/`; the running mount is not interrupted, new unit settings apply at the next login (or `systemctl --user daemon-reload && systemctl --user restart rclone-drime-mount`).

## Everyday use

- Work in `~/Drime` for anything in your account — saves upload automatically (files are cached locally up to 10 GB, so recently used files open instantly).
- Drop things in `~/DrimeSync` for an always-mirrored offline copy of the cloud's `Sync` folder.
- **Do not delete `~/DrimeSync/RCLONE_TEST`** — it's a safety marker; sync aborts rather than mass-deleting if either side ever looks wrong.
- For sharing, trash, account settings and to check on the drive and sync, open the **Drime** window (see [above](#the-drime-window)).

Terminal equivalents:

```bash
drime-desktop --status                       # same info as the app
systemctl --user status rclone-drime-mount   # mount health
systemctl --user start drime-bisync          # sync right now
journalctl --user -u drime-bisync -e         # sync logs
drime-desktop --help                         # --install, --uninstall, --check-update, --version
```

In the Flatpak, prefix the commands with `flatpak run io.github.davethegamedev.DrimeDesktop` (e.g. `flatpak run io.github.davethegamedev.DrimeDesktop --status`); there are no systemd units, the sync log is in **Settings → Sync log** (the file is `~/.var/app/io.github.davethegamedev.DrimeDesktop/cache/drime-desktop/daemon.log`), and `flatpak ps` shows the background service as a second instance of the app.

## Uninstall

1. **Drime → ☰ → Settings → Remove my setup → Remove…** (or `drime-desktop --uninstall`; add `--purge-config` or tick the checkbox to also delete the API token and uninstall pydrime). This unmounts the drive, disables the timer and removes the bookmark, caches, sync state and the web app's login data.
2. Uninstall the *Drime* package in your software center, or `sudo dnf remove drime-desktop` / `sudo apt remove drime-desktop` / `flatpak uninstall io.github.davethegamedev.DrimeDesktop` (add `--delete-data` to also remove the Flatpak's `~/.var/app/…` directory with the token, caches and login).

What is **kept**: `~/DrimeSync` and everything in your cloud account — always; the window-size file `~/.config/drime-desktop/window.json`; plus your API token (rclone remote), pydrime and its config unless you chose to delete them.

## Caveats

- **Drime's API stores no modification times or hashes**, so the sync folder detects changes by file size only. An edit that keeps a file's exact byte size won't be picked up by `~/DrimeSync`. This is rare, but for important work prefer `~/Drime` — the mount always uploads what you save.
- If rclone ever crashes, the drive comes back on its own within about 10 seconds (the status pill shows *Drive not mounted* in the meantime). Files saved just before the crash are kept in the local cache and uploaded once it is back.
- Tune cache size/behavior with `systemctl --user edit rclone-drime-mount` (override `ExecStart`, e.g. `--vfs-cache-max-size`, `--dir-cache-time`; overrides survive updates, edits to the packaged unit don't). Changes made from another device can take up to an hour to appear in `~/Drime` (`--dir-cache-time 1h`; a cold re-listing takes a few seconds and would otherwise stall every app's file-open dialog). Lower it if you need faster cross-device visibility.
- Drime's own desktop apps are beta; the rclone backend is young too. Keep backups of anything irreplaceable.
- The GNOME Files **sidebar** bookmark keeps the generic folder glyph: Nautilus hardcodes bookmark icons, so only the folder in the main view and the app launchers show the Drime logo.
- The Drime logo (`assets/drime.png`) belongs to Drime and is used only to identify the service; it is not covered by this project's MIT license.

### Flatpak limitations

The RPM and DEB are the full integration; the Flatpak has to work around the sandbox (the research behind this is in [docs/flatpak-plan.md](docs/flatpak-plan.md)):

- **No systemd.** The drive and the sync folder are run by Drime's own background service (`drime-desktop --daemon`, a second instance of the app you can see with `flatpak ps`). It restarts rclone with a growing delay if the mount dies, and the Drime window restarts the service itself if it is gone — but there is no `Restart=on-failure` outside the app, and no `systemctl --user edit` to tune the mount's cache size or directory cache time.
- **Login autostart is a permission.** The service is started at login through the desktop's *background* permission; the first time you enable the drive or the sync, GNOME/KDE ask whether Drime may run in the background. If you decline, the drive and the sync only run while the Drime window is open (you can change your mind in the desktop's application settings, e.g. GNOME Settings → Apps → Drime).
- **The drive needs `fuse3` on your system.** FUSE mounts cannot be created inside the sandbox; rclone runs inside it and the mount is created through the host's `fusermount3`. Without `fuse3` the wizard and Settings offer only the sync folder and the web app.
- **Everything lives under `~/.var/app/io.github.davethegamedev.DrimeDesktop/`**: the token (`config/rclone/rclone.conf`), the rclone caches and the bisync state (`cache/rclone/`), the web app's login (`data/drime-desktop/web`) and the service log. `~/Drime` and `~/DrimeSync` are the same as with the packages.
- **Don't run the Flatpak and the RPM/DEB side by side** with the sync folder enabled in both: they don't share the bisync state. The Flatpak's service leaves `~/Drime` alone if the packaged mount already holds it.
- **No pydrime option** in the Flatpak's `--install` (install it outside the sandbox).
- The Flatpak is built by this project and attached to the GitHub Release; it is not on Flathub (that would need a review exception for the host `fusermount3` access).

## Advanced: install from a git checkout

To hack on it without the RPM, or on other distributions:

```bash
sudo dnf install python3-gobject gtk4 libadwaita webkitgtk6.0 rclone fuse3   # Fedora
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0 fuse3  # Ubuntu/Debian (+ rclone from rclone.org)
git clone https://github.com/DaveTheGameDev/drime-desktop-linux.git
cd drime-desktop-linux
./install.sh                 # terminal wizard; add --with-pydrime for the CLI
PYTHONPATH=src DRIME_DESKTOP_SRC=$PWD python3 -m drime_desktop.cli    # the GUI, from the checkout
```

`install.sh` checks the packages with `rpm -q` on RPM-based systems, with `dpkg-query` on Debian-based ones, and skips that check elsewhere; the setup itself only needs a systemd user session, `rclone` and `fusermount3`. The wizard steps can each be skipped and re-run later from Settings.

In this mode the systemd units are copied to `~/.config/systemd/user/`. If you later install the RPM or DEB, the app switches to the packaged units automatically. `./uninstall.sh [--purge-config]` reverses the setup.

<details>
<summary>What the setup does, step by step</summary>

1. `rclone config create drime drime access_token=<TOKEN>` and `rclone about drime:` to verify it
2. `systemctl --user enable --now rclone-drime-mount.service` (units from `/usr/lib/systemd/user/` or `systemd/`)
3. Add `~/Drime` to `~/.config/gtk-3.0/bookmarks`; `gio set ~/Drime metadata::custom-icon file:///usr/share/icons/hicolor/512x512/apps/drime-desktop.png`
4. `mkdir ~/DrimeSync && touch ~/DrimeSync/RCLONE_TEST && rclone copy ~/DrimeSync/RCLONE_TEST drime:Sync/`
5. `rclone bisync ~/DrimeSync drime:Sync --size-only --create-empty-src-dirs --check-access --resync` (the timer runs the same command with `--resilient --recover` instead of `--resync`)
6. `systemctl --user enable --now drime-bisync.timer`

The launcher (`io.github.davethegamedev.DrimeDesktop.desktop`) and the icon are installed system-wide by the package. The web app's cache lives in `~/.cache/drime-desktop`.
</details>

## Building the packages

RPM, on Fedora:

```bash
sudo dnf install rpm-build rpmdevtools rpmlint python3-devel systemd-rpm-macros desktop-file-utils libappstream-glib
make rpm lint            # -> build/RPMS/noarch/drime-desktop-<version>-1.fcNN.noarch.rpm
make install-local       # sudo dnf install it
```

(`make rpm RPMBUILD_OPTS=--nodeps` builds without `python3-devel`.)

DEB, on Ubuntu or Debian:

```bash
sudo apt install build-essential debhelper dh-python lintian desktop-file-utils appstream
make deb deb-lint        # -> build/deb/drime-desktop_<version>_all.deb
make install-local-deb   # sudo apt install it
```

`drime-desktop.spec` is the single source of the version and the changelog: `make deb` generates `debian/changelog` from its `%changelog` (`scripts/deb-changelog.sh`), so it is not committed. Both packages install the same files to the same places; `debian/rules` mirrors the spec's `%install`.

Flatpak, on any distribution with `flatpak` and `flatpak-builder` (and the Flathub remote, for the GNOME 50 SDK):

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
make flatpak             # -> build/flatpak/drime-desktop-<version>.flatpak
make install-local-flatpak
```

The manifest is `flatpak/io.github.davethegamedev.DrimeDesktop.yml`: it bundles the official rclone binary, installs Pika Backup's `fusermount3` wrapper (so the mount goes through the host's `fusermount3` via `flatpak-spawn --host`) and runs `flatpak/build-app.sh`, the Flatpak counterpart of the spec's `%install`.

Tests (pure Python, no GTK needed): `make test` runs `tests/` with pytest (`python3-pytest`).

**Releasing**: bump `Version:` and `%changelog` in `drime-desktop.spec`, commit, then `git tag v<version> && git push --tags`. The [GitHub Actions workflow](.github/workflows/release.yml) runs the tests, builds the RPM in a Fedora container, the DEB on Ubuntu 24.04 (where it also installs it to prove the dependencies resolve) and the Flatpak bundle in the GNOME 50 flatpak-builder container, lints the packages and attaches the `.rpm`, `.deb`, `.flatpak`, `.src.rpm` and source tarball to a GitHub Release. The tag must match the spec version. The release notes are generated by `scripts/release-notes.sh`: the download-guidance header (`.github/release-header.md`) plus that version's `%changelog` entry — so write the changelog for users. (`make release` publishes the same thing by hand if the workflow isn't available; it attaches `build/deb/*.deb` and `build/flatpak/*.flatpak` too when present.) The app's *Check for updates* reads the latest release through the public GitHub API.

## Repo layout

```
drime-desktop.spec          # RPM spec (single source of the version and changelog)
debian/                     # DEB packaging (debian/changelog is generated from the spec)
flatpak/                    # Flatpak manifest, fusermount3 wrapper, install script (make flatpak)
Makefile                    # make rpm / lint / deb / deb-lint / flatpak / test / install-local[-deb|-flatpak]
src/drime_desktop/          # Python package: backend (rclone/systemd), GTK app, embedded web view, wizard, updates, CLI,
                            #   and the Flatpak's background service (daemon, sandbox, portal)
tests/                      # pytest suite for the distribution-independent logic
bin/drime-desktop           # launcher
systemd/                    # user units: mount service, bisync service + timer
desktop/                    # io.github.davethegamedev.DrimeDesktop.desktop (launcher)
assets/                     # icon, AppStream metainfo
install.sh / uninstall.sh   # thin wrappers for git-checkout installs
.github/workflows/          # release build (RPM + DEB + Flatpak)
docs/                       # Flatpak feasibility research
```

## Support and contributing

- **Questions and help**: [Q&A Discussions](https://github.com/DaveTheGameDev/drime-desktop-linux/discussions/categories/q-a)
- **Ideas**: [Ideas Discussions](https://github.com/DaveTheGameDev/drime-desktop-linux/discussions/categories/ideas)
- **Bugs**: [open an issue](https://github.com/DaveTheGameDev/drime-desktop-linux/issues/new/choose)

Pull requests are welcome. Bug reports are most useful with the output of `drime-desktop --status`, `journalctl --user -u rclone-drime-mount -u drime-bisync -n 50`, and your distribution and version. Licensed under the [MIT License](LICENSE).

---

*Unofficial project, not affiliated with Drime. Built on [rclone](https://rclone.org/drime/) and [pydrime](https://pydrime.readthedocs.io/).*
