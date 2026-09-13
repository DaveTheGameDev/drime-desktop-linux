# Fallback when python3-devel is not installed (local --nodeps builds).
%{!?python3_sitelib: %global python3_sitelib %(python3 -c "import sysconfig as s; print(s.get_path('purelib', 'rpm_prefix' if 'rpm_prefix' in s.get_scheme_names() else None))")}

Name:           drime-desktop
Version:        0.5.0
Release:        1%{?dist}
Summary:        Unofficial Drime cloud desktop app (virtual drive, sync folder, web app)
License:        MIT
URL:            https://github.com/DaveTheGameDev/drime-desktop-linux
Source0:        %{url}/releases/download/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib

Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       webkitgtk6.0
Requires:       rclone >= 1.73
Requires:       fuse3
Requires:       hicolor-icon-theme
Recommends:     PackageKit-glib
Recommends:     python3-rpm

%description
Recreates the Drime desktop experience on Linux using the native Drime rclone
backend: a virtual drive mounted at ~/Drime, a two-way synced ~/DrimeSync
folder, and the Drime web app embedded in the same window (WebKitGTK) with
status, updates and setup built in. Unofficial, not affiliated with Drime.

%prep
%autosetup -n %{name}-%{version}

%build
# Nothing to build.

%install
install -d %{buildroot}%{python3_sitelib}/drime_desktop
install -m 0644 src/drime_desktop/*.py %{buildroot}%{python3_sitelib}/drime_desktop/
sed -i 's/@VERSION@/%{version}/' %{buildroot}%{python3_sitelib}/drime_desktop/__init__.py
install -D -m 0755 bin/drime-desktop %{buildroot}%{_bindir}/drime-desktop
install -D -m 0644 systemd/rclone-drime-mount.service %{buildroot}%{_userunitdir}/rclone-drime-mount.service
install -D -m 0644 systemd/drime-bisync.service       %{buildroot}%{_userunitdir}/drime-bisync.service
install -D -m 0644 systemd/drime-bisync.timer         %{buildroot}%{_userunitdir}/drime-bisync.timer
install -D -m 0644 desktop/io.github.davethegamedev.DrimeDesktop.desktop %{buildroot}%{_datadir}/applications/io.github.davethegamedev.DrimeDesktop.desktop
install -D -m 0644 assets/drime.png %{buildroot}%{_datadir}/icons/hicolor/512x512/apps/drime-desktop.png
install -D -m 0644 assets/io.github.davethegamedev.DrimeDesktop.metainfo.xml \
    %{buildroot}%{_metainfodir}/io.github.davethegamedev.DrimeDesktop.metainfo.xml

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/*.desktop
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/*.xml

%files
%license LICENSE
%doc README.md
%{_bindir}/drime-desktop
%{python3_sitelib}/drime_desktop/
%{_userunitdir}/rclone-drime-mount.service
%{_userunitdir}/drime-bisync.service
%{_userunitdir}/drime-bisync.timer
%{_datadir}/applications/io.github.davethegamedev.DrimeDesktop.desktop
%{_datadir}/icons/hicolor/512x512/apps/drime-desktop.png
%{_metainfodir}/io.github.davethegamedev.DrimeDesktop.metainfo.xml

%changelog
* Sun Sep 13 2026 DaveTheGameDev - 0.5.0-1
- New Flatpak bundle (drime-desktop-<version>.flatpak) for every other
  distribution, next to the RPM and the DEB. It bundles rclone 1.75.1 and runs
  on the GNOME 50 runtime; install it with your software center or
  flatpak install --user
- In the Flatpak the drive and the sync folder are run by Drime's own
  background service (drime-desktop --daemon) instead of systemd user units,
  started at login through the desktop's background permission and restarted
  by the app if it dies. The virtual drive mounts through the host's
  fusermount3, so it needs fuse3 installed on the system; without it the
  wizard and Settings offer the sync folder and the web app only
- Flatpak updates: the app downloads the new bundle and opens it in your
  software center (no PackageKit inside the sandbox)
- Configuration, cache and web data locations follow the XDG base directory
  variables (unchanged on the RPM and DEB; ~/.var/app/<id>/ in the Flatpak)

* Sun Aug 30 2026 DaveTheGameDev - 0.4.1-1
- The window no longer freezes for a quarter of an hour after the computer
  wakes from sleep. WebKit funnels every request through one connection to
  Drime that is dead after a suspend, and the previous recovery retried on
  that same connection; the app now drops the connections when the system
  wakes (and when the page notices a sleep, or the network comes back after
  an outage) and refetches on fresh ones
- The virtual drive keeps its directory listing warm for an hour instead of a
  minute, so file dialogs (which walk ~/Drime through the sidebar bookmarks)
  no longer stall for a few seconds after a moment of inactivity

* Sun Aug 30 2026 DaveTheGameDev - 0.4.0-1
- New Ubuntu/Debian package (drime-desktop_<version>_all.deb) for Ubuntu 24.04+
  and Debian 13+. It needs rclone 1.73 or newer from rclone.org, because the
  rclone in the Ubuntu and Debian archives is too old for the Drime backend
- Install, upgrade and uninstall hints, the update download and the version
  comparison now follow the distribution (dnf on Fedora, apt on Ubuntu/Debian)
- The release workflow builds and attaches both packages and runs the tests

* Sun Aug 30 2026 DaveTheGameDev - 0.3.11-1
- "Download and install" now installs the update itself through PackageKit
  (you only enter your password) and then offers the restart; handing the RPM
  to GNOME Software silently did nothing on Fedora 44, whose Software uses
  dnf5 and has no PackageKit plugin
- The web app no longer stays stuck on a loading skeleton after the computer
  wakes from sleep: requests hanging on dead connections are aborted and
  refetched, API requests time out instead of hanging, and the page reloads
  by itself if it is still stuck
- Recommends PackageKit-glib instead of gnome-software

* Sat Aug 29 2026 DaveTheGameDev - 0.3.10-1
- The virtual drive now recovers by itself if rclone crashes: the mount unit
  clears the stale "Transport endpoint is not connected" mountpoint before
  starting (previously every restart failed and ~/Drime stayed broken until a
  manual fusermount3 -uz)
- The status pill and --status report such a stale drive as "not mounted"
  instead of green "mounted"
- Turning the drive off or removing the setup uses a lazy unmount, so it
  works while files are open

* Sat Aug 29 2026 DaveTheGameDev - 0.3.9-1
- Refresh the folder listing right after a move, undo, rename, delete or
  upload completes, instead of up to a minute later (Drime's web app misses
  the cache update for items moved back to the top folder)

* Sat Aug 29 2026 DaveTheGameDev - 0.3.8-1
- Really fix dragging files and folders inside the Drime window: WebKitGTK
  never delivers the page's own drops, so the app now drives the drag itself
  (the item no longer stays grayed out and dragging keeps working)
- Refresh the folder listing when the window regains focus and about every
  minute, so changes made in a browser, on your phone or through ~/Drime show
  up without reloading

* Sat Aug 29 2026 DaveTheGameDev - 0.3.7-1
- Fix dragging files and folders inside the Drime window (e.g. moving a file
  into a folder): the drop-to-upload feature no longer intercepts the web app's
  own drags, which left the item grayed out and blocked all further dragging
  until the app was restarted

* Sat Aug 29 2026 DaveTheGameDev - 0.3.6-1
- Keep the Drime session alive and renew its CSRF token, so creating, renaming
  or deleting things no longer fails with "CSRF token mismatch" after the window
  has been idle for a couple of hours

* Fri Aug 28 2026 DaveTheGameDev - 0.3.5-1
- Drag and drop files from the file manager onto the Drime window to upload
  them (WebKitGTK's own drop handling did not deliver files to the web app).

* Fri Aug 28 2026 DaveTheGameDev - 0.3.4-1
- Notice when a newer version has been installed under the running app and
  offer to restart it, instead of silently keeping the old code running.

* Fri Aug 28 2026 DaveTheGameDev - 0.3.3-1
- Check for a new release automatically a few seconds after the app opens and
  ask to download and install it (Later / Skip this version / Download and install).

* Fri Aug 28 2026 DaveTheGameDev - 0.3.2-1
- Fix the setup wizard reappearing on every launch when the drive or the
  sync folder had been skipped: only the account connection is required now.

* Fri Aug 28 2026 DaveTheGameDev - 0.3.1-1
- Fix the missing icon in the dock: the launcher is now named after the
  application ID so GNOME Shell can match the window to it.

* Fri Aug 28 2026 DaveTheGameDev - 0.3.0-1
- Single "Drime" app: the web app is now embedded with WebKitGTK; no
  Chromium/Flatpak needed and the separate "Drime Web" launcher is gone.
- Drive/sync status in the title bar, settings dialog, downloads to ~/Downloads.

* Fri Aug 28 2026 DaveTheGameDev - 0.2.0-1
- First RPM release: GTK4/libadwaita setup app, packaged systemd user units,
  self-update via GitHub Releases.
