## 📥 Which file do I download?

- **Fedora**: `drime-desktop-<version>-1.fcNN.noarch.rpm` — double-click it (GNOME Software installs it) or run `sudo dnf install ./drime-desktop-*.rpm`.
- **Ubuntu 24.04+ / Debian 13+**: `drime-desktop_<version>_all.deb` — first install rclone 1.73 or newer from [rclone.org](https://rclone.org/install/) (`curl https://rclone.org/install.sh | sudo bash`; the rclone in the Ubuntu/Debian archives is too old), then run `sudo apt install ./drime-desktop_*.deb`.
- **Any other distribution (Flatpak)**: `drime-desktop-<version>.flatpak` — open it with your software center or run `flatpak install --user ./drime-desktop-<version>.flatpak` (it offers to add Flathub for the GNOME runtime). rclone is bundled. The virtual drive needs `fuse3` installed on your system, and the drive and the sync run in Drime's own background service instead of systemd units — see [Flatpak limitations](https://github.com/DaveTheGameDev/drime-desktop-linux#flatpak-limitations).

Afterwards open **Drime** from your applications and follow the wizard.

The other files are not needed for a normal install:
- `*.src.rpm` — source package, for rebuilding the RPM
- `*.tar.gz` — plain source code

Requirements and manual steps: see the [README](https://github.com/DaveTheGameDev/drime-desktop-linux#install).

---
