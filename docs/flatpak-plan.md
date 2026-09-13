# Flatpak / Flathub plan for Drime Desktop

Status: research done 2026-09-13, nothing implemented yet.

## Verdict

Feasible, but not as a straight port. Two of the five host dependencies cannot
exist inside the Flatpak sandbox and have to be redesigned; the other three port
cleanly.

| Current dependency | In a Flatpak | What to do |
|---|---|---|
| `rclone mount` (FUSE via setuid `fusermount3`, `/dev/fuse`) | **Impossible inside the sandbox.** No capabilities, `no_new_privs`, `nosuid`, seccomp blocks user namespaces, and even a successful mount would stay inside the sandbox's private mount namespace (bubblewrap makes `/` `MS_SLAVE`), invisible to Nautilus and every other app. `--device=all` does not help and Flathub never grants it. | Opt-in "Tier 1": the Deja Dup / Pika Backup / GNOME Builder wrapper. `/app/bin/fusermount3` forwards `_FUSE_COMMFD` to the **host's** `fusermount3` through `flatpak-spawn --host`; the host opens `/dev/fuse` and hands the fd back, so rclone's FUSE server runs in the sandbox while the mount lands in the host namespace at `~/Drime`. Needs `--talk-name=org.freedesktop.Flatpak`, a reviewed Flathub exception. |
| systemd user units in `/usr/lib/systemd/user`, `systemctl --user` | **Cannot install or enable unit files.** Writing `~/.config/systemd` is a linter error with no granted precedent; Flatpak deliberately does not export units (flatpak#2787, open since 2019). Controlling units over D-Bus needs `--talk-name=org.freedesktop.systemd1`, itself classed as a sandbox escape (exception-only; precedents Syncthing Tray, CTLDash). | Replace with an in-app `--daemon` mode (bisync scheduler, and the mount holder in Tier 1) registered at login through the Background portal (`RequestBackground(autostart=true, commandline=[...])`). No supervision, restart-on-failure or timers: the daemon must supervise itself. |
| Self-update via GitHub Releases + PackageKit | Not reachable from the sandbox and not needed: Flathub builds every tagged release and ships updates through GNOME Software / Discover / `flatpak update`. | Gate the updater, PackageKit code and unit installer behind `not os.path.exists("/.flatpak-info")`. "Check for updates" can link to the Flathub page. |
| WebKitGTK 6.0, GTK4, libadwaita, PyGObject | Provided by `org.gnome.Platform//50`. | Nothing. WebKit's own bubblewrap sub-sandbox auto-detects Flatpak (`flatpak-spawn --sandbox`); expected to work, not verified in this research. |
| rclone >= 1.73 (Go) | Routine on Flathub: Rclone Shuttle bundles 1.73.1, Deja Dup bundles 1.75.1, both offline-built from vendored Go modules. | Bundle it. This also removes the "Ubuntu's rclone is too old" problem entirely. |

Also: `--filesystem=home` is a linter error (granted only on explanation; the
grandfathered Nextcloud/Celeste/Dropbox grants are not evidence a new app gets
it). Because the app only ever touches `~/Drime` and `~/DrimeSync`, use
`--filesystem=~/Drime:create --filesystem=~/DrimeSync:create`, which need no
exception.

## Recommended architecture: tiered

**Tier 0, zero linter exceptions, works on every distro.** Bundled rclone,
`rclone bisync ~/DrimeSync drime:Sync` inside the sandbox on a 15-minute
in-process timer, headless `drime-desktop --daemon` autostarted through the
Background portal, web app window as today. No virtual drive. Review risk: low.

**Tier 1, opt-in FUSE mount.** Add the fusermount wrapper module and request
`--talk-name=org.freedesktop.Flatpak` via an `exceptions.json` PR citing
Deja Dup / Pika Backup / GNOME Builder and xdg-desktop-portal#695 (no fusermount
portal exists). The daemon then also holds `rclone mount drime: ~/Drime`, visible
to Nautilus. Review risk: moderate (~170 apps hold this exception, case by case,
"potentially unsafe" badge on the store page). Do **not** also request
`org.freedesktop.systemd1`: with `flatpak-spawn` access the app could run
`systemctl --user` anyway, and stacking two escape-class exceptions lowers the
odds.

**Rejected options.** "Flatpak UI + host rclone via `flatpak-spawn --host`":
needs the same exception yet depends on the distro shipping rclone >= 1.73,
which defeats the any-distro goal. "systemd D-Bus transient units": second
exception, only worth revisiting if daemon supervision proves inadequate.

**Keep RPM/DEB** as the full-integration path (systemd-supervised mount that
survives app crashes and starts before any window). Document the difference in
the README.

## Known trade-offs of the Flatpak

- The mount (Tier 1) lives only as long as the daemon process. If it crashes,
  the mount is gone until the daemon restarts; there is no `Restart=on-failure`.
  An in-app watchdog (a second tiny process, or the GUI re-spawning the daemon)
  is the substitute.
- The Background portal shows an "allow running in background" prompt on
  GNOME/KDE; the user can deny it, and `RequestBackground` without `autostart`
  deletes an existing autostart entry.
- rclone.conf and bisync state move to `~/.var/app/<id>/config/rclone` and
  `~/.var/app/<id>/cache/rclone/bisync`. A Flatpak and an RPM/DEB install on the
  same machine would both claim `~/Drime`; the Flatpak should detect the
  existing systemd mount unit (via the mount being present) and refuse to
  double-mount.
- Drime's own trademark: Flathub reviewers may ask that the name/summary say
  "unofficial". The metainfo already does; keep it explicit.

## Step-by-step plan

1. **App ID check.** Flathub verifies `io.github.*` IDs against a public repo
   `github.com/<owner>/<repo>`. The repo is `DaveTheGameDev/drime-desktop-linux`
   but the ID is `io.github.davethegamedev.DrimeDesktop`. Confirm on
   docs.flathub.org/docs/for-app-authors/requirements whether the last component
   must match the repo name; if so, either rename the repo to `DrimeDesktop` or
   change the ID everywhere (desktop file, metainfo, icons). Do this first,
   because the ID cannot change after publication.
2. **Code changes (all behind a `is_flatpak()` check on `/.flatpak-info`).**
   - `backend.py`: locate rclone/fusermount3 via `PATH` (so `/app/bin` wins);
     add a `--daemon` entry point that runs the bisync loop every 15 min with
     the same flags as `drime-bisync.service`, and in Tier 1 starts and
     watches `rclone mount` with the same flags as the unit; expose
     mount/sync status to the GUI over a small local IPC (a Unix socket under
     `$XDG_RUNTIME_DIR/app/<id>/`, or D-Bus on the app's own well-known name).
   - Setup wizard / Settings: replace "enable unit" with a portal call
     `org.freedesktop.portal.Background.RequestBackground(autostart=true,
     commandline=["drime-desktop","--daemon"], reason=...)`; "Sync now"
     signals the daemon instead of `systemctl start`.
   - `updates.py`, PackageKit, unit installer/migration: skipped under Flatpak.
   - `--status` and the status pill read from the daemon, not `systemctl show`.
3. **Vendor rclone.** `git clone rclone && git checkout v1.75.1 && go mod vendor`,
   run `flatpak-builder-tools/go-modules/flatpak-go-mod`, commit the generated
   `go-sources.yml` and `modules.txt`. (Rclone's release tarballs are not
   vendored, and a plain git checkout fails with "inconsistent vendoring", so
   this generator route is required.)
4. **Manifest `io.github.davethegamedev.DrimeDesktop.yml`.**
   ```yaml
   runtime: org.gnome.Platform
   runtime-version: '50'
   sdk: org.gnome.Sdk
   sdk-extensions: [org.freedesktop.Sdk.Extension.golang]
   command: drime-desktop
   finish-args:
     - --socket=wayland
     - --socket=fallback-x11
     - --device=dri
     - --share=ipc
     - --share=network
     - --filesystem=~/Drime:create
     - --filesystem=~/DrimeSync:create
     - --filesystem=xdg-download          # web app downloads
     - --talk-name=org.freedesktop.Notifications
     # Tier 1 only, needs exceptions.json PR:
     # - --talk-name=org.freedesktop.Flatpak
   modules:
     - name: rclone            # buildsystem simple, append-path /usr/lib/sdk/golang/bin,
                               # go build -mod=vendor . ; install -Dm755 -t /app/bin rclone
                               # sources: v1.75.1 archive + go-sources.yml
     - name: fusermount-wrapper  # Tier 1: install Deja Dup's fusermount-wrapper.sh as /app/bin/fusermount3
     - name: drime-desktop     # git source pinned to tag + commit; installs app, desktop file, icons, metainfo
   ```
   Python deps: everything comes from the GNOME runtime; if any pip package is
   needed, generate `python-deps.json` with `flatpak-pip-generator`.
5. **AppStream.** Extend the existing `assets/io.github.davethegamedev.DrimeDesktop.metainfo.xml`
   to Flathub's list: `<id>` = app id, `<name>`, `<summary>` under 35 chars,
   `<description>`, `<launchable type="desktop-id">`, `<project_license>`,
   `<developer id="...">`, `<url type="homepage|bugtracker|vcs-browser">`,
   `<screenshots>` with public https image URLs, dated `<releases>`,
   `<content_rating type="oars-1.1">`, `<branding>` colours. Validate with
   `appstreamcli validate --strict` and
   `flatpak run --command=flatpak-builder-lint org.flatpak.Builder appstream <file>`.
6. **Local build and test.**
   ```
   flatpak run org.flatpak.Builder --force-clean --sandbox --user --install \
     --install-deps-from=flathub --ccache --mirror-screenshots-url=https://dl.flathub.org/media/ \
     --repo=repo builddir io.github.davethegamedev.DrimeDesktop.yml
   flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest io.github.davethegamedev.DrimeDesktop.yml
   flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo
   ```
   Check: wizard writes rclone.conf under `~/.var/app`, bisync runs against
   `~/DrimeSync`, autostart entry appears in `~/.config/autostart`, web app
   logs in and downloads land in Downloads, and (Tier 1, tested with a local
   `--talk-name=org.freedesktop.Flatpak` override) `~/Drime` shows in Nautilus.
   Add a `make flatpak` target and a CI job mirroring the RPM/DEB ones.
7. **Submission.** Fork `github.com/flathub/flathub`, branch off `new-pr`, add
   only the manifest, `go-sources.yml`, `modules.txt`, the wrapper script and
   any `python-deps.json`, open a PR against `new-pr`. flathubbot runs a test
   build; answer reviewer questions. For Tier 1, open the exception PR to
   `flathub-infra/flatpak-builder-lint` `exceptions.json` with a one-line,
   human-written justification (LLM-written PRs are explicitly disallowed).
   Recommended order: ship Tier 0 first, then request the Tier 1 exception as a
   follow-up once the app is listed.
8. **After merge.** Each release: bump the tag in the Flathub repo (add
   `x-checker-data` so flathubbot opens the PR automatically); bump the rclone
   module the same way. README: Flatpak = web app + sync folder (+ mount if the
   exception is granted); RPM/DEB = full persistent mount via systemd.

## Open questions

- Will Flathub grant `org.freedesktop.Flatpak` when a mount-free mode exists?
  Reviewers may ask to ship without it.
- Is the daemon-held mount plus an in-app watchdog acceptable, or is the
  `systemd1` `StartTransientUnit` route worth a second exception after all?
- Does the current metainfo `<name>` "Drime" pass Flathub's naming review for an
  unofficial client?

## Sources (verified in the research run)

- Flatpak sandbox model: github.com/flatpak/flatpak/wiki/Sandbox; bubblewrap `MS_SLAVE` root
- FUSE wrapper precedent: flathub/org.gnome.DejaDup manifest, flathub/org.gnome.Builder,
  flathub/org.gnome.World.PikaBackup `fusermount-wrapper.sh`; xdg-desktop-portal#695
- `flatpak-spawn --host` = sandbox escape: flatpak#5161, GHSA-4ppf-fxf6-vxg2
- Linter rules and exceptions: docs.flathub.org/docs/for-app-authors/linter,
  flathub-infra/flatpak-builder-lint `exceptions.json`
- systemd over D-Bus from a sandbox: flathub/io.github.martchus.syncthingtray, io.github.nikelaz.CTLDash;
  flatpak#2787 (no unit export)
- Background portal: flatpak.github.io/xdg-desktop-portal Background docs, xdg-desktop-portal#899
- rclone bundling: flathub/io.github.pieterdd.RcloneShuttle (`go-sources.yml`), flathub/org.gnome.DejaDup
  (`rclone.go.mod.yml`), flatpak-builder-tools/go-modules
