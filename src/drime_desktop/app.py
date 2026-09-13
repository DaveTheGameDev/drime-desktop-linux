"""GTK4 / libadwaita application: the Drime web app window with a status menu
and a settings dialog (drive, sync, updates, removal)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__, backend, updates  # noqa: E402
from .web import DrimeWebView, open_externally  # noqa: E402

WINDOW_STATE = backend.CONFIG_DIR / "window.json"


def run_async(fn: Callable, on_done: Callable[[object, Exception | None], None]) -> None:
    """Run fn() in a thread; call on_done(result, error) on the main loop."""
    def worker():
        try:
            result, err = fn(), None
        except Exception as e:  # noqa: BLE001
            result, err = None, e
        GLib.idle_add(on_done, result, err)
    threading.Thread(target=worker, daemon=True).start()


def relative_time(ts: int | None) -> str:
    if not ts:
        return "never"
    d = int(ts - time.time())
    ago = d < 0
    d = abs(d)
    if d < 60:
        s = "less than a minute"
    elif d < 3600:
        s = f"{d // 60} min"
    elif d < 86400:
        s = f"{d // 3600} h {d % 3600 // 60} min"
    else:
        s = f"{d // 86400} days"
    return f"{s} ago" if ago else f"in {s}"


def button(label: str, cb: Callable, *css: str) -> Gtk.Button:
    b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    for c in css:
        b.add_css_class(c)
    b.connect("clicked", lambda _b: cb())
    return b


class LogView(Gtk.ScrolledWindow):
    """Monospace log box fed from worker threads via append()."""

    def __init__(self, height: int = 180):
        super().__init__(min_content_height=height, max_content_height=height,
                         propagate_natural_height=True)
        self.view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False,
                                 left_margin=8, right_margin=8, top_margin=6, bottom_margin=6,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.add_css_class("card")
        self.set_child(self.view)

    def append(self, line: str) -> None:
        def do():
            buf = self.view.get_buffer()
            buf.insert(buf.get_end_iter(), line + "\n")
            self.view.scroll_to_mark(buf.get_insert(), 0, False, 0, 0)
        GLib.idle_add(do)

    def set_text(self, text: str) -> None:
        self.view.get_buffer().set_text(text)


class SettingsDialog(Adw.PreferencesDialog):
    """Drive / sync / updates / remove. Owned by MainWindow, presented on demand."""

    def __init__(self, win: "MainWindow"):
        super().__init__(title="Drime settings", content_width=640, content_height=620)
        self.win = win
        self._refreshing = False
        self._known: backend.State | None = None   # last state read from the system
        self._release: updates.Release | None = None

        self.page = Adw.PreferencesPage()
        self.add(self.page)
        self._build_status()
        self._build_updates()
        self._build_remove()

    # --- building ----------------------------------------------------------

    def _build_status(self):
        g = Adw.PreferencesGroup(title="Status")
        self.page.add(g)

        self.drive_row = Adw.SwitchRow(title="Virtual drive", subtitle=str(backend.MOUNT))
        self.drive_row.add_prefix(Gtk.Image.new_from_icon_name("drive-harddisk-symbolic"))
        self.drive_row.add_suffix(button("Open folder", self.win.open_folder))
        self.drive_row.connect("notify::active", self._on_drive_switch)
        g.add(self.drive_row)

        self.sync_row = Adw.SwitchRow(title="Sync folder", subtitle=str(backend.SYNC_DIR))
        self.sync_row.add_prefix(Gtk.Image.new_from_icon_name("folder-remote-symbolic"))
        self.sync_row.add_suffix(button("Sync now", self.win.sync_now))
        self.sync_row.connect("notify::active", self._on_sync_switch)
        g.add(self.sync_row)

        self.log_row = Adw.ExpanderRow(title="Sync log")
        self.sync_log = LogView()
        self.log_row.add_row(self.sync_log)
        self.log_row.connect("notify::expanded", lambda *_: self._load_sync_log())
        g.add(self.log_row)

    def _build_updates(self):
        g = Adw.PreferencesGroup(title="Updates")
        self.page.add(g)

        self.update_row = Adw.ActionRow(title="Drime Desktop", subtitle=f"Version {__version__}")
        self.update_row.add_prefix(Gtk.Image.new_from_icon_name("view-refresh-symbolic"))
        self.update_check_btn = button("Check for updates", self.check_updates)
        self.update_install_btn = button("Download and install", self.download_update, "suggested-action")
        self.update_install_btn.set_visible(False)
        self.update_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self.update_row.add_suffix(self.update_spinner)
        self.update_row.add_suffix(self.update_install_btn)
        self.update_row.add_suffix(self.update_check_btn)
        g.add(self.update_row)

    def _build_remove(self):
        g = Adw.PreferencesGroup(title="Remove")
        self.page.add(g)
        row = Adw.ActionRow(title="Remove my setup",
                            subtitle="Unmounts the drive, stops "
                                     + ("the background service" if backend.is_flatpak() else "syncing")
                                     + " and signs out of the web app. "
                                     f"{backend.SYNC_DIR.name} and your cloud data are kept.")
        row.add_prefix(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
        row.add_suffix(button("Remove…", self.confirm_remove, "destructive-action"))
        g.add(row)

    # --- state -------------------------------------------------------------

    def toast(self, text: str) -> None:
        self.add_toast(Adw.Toast.new(text))

    def apply_state(self, st: backend.State, ss: backend.SyncStatus) -> None:
        self._refreshing = True
        try:
            self._known = st
            if self.drive_row.get_active() != st.mount_enabled:
                self.drive_row.set_active(st.mount_enabled)
            service_down = st.daemon_alive is False   # Flatpak: the background service is not running
            self.drive_row.set_sensitive(not st.drive_problem)
            self.drive_row.set_subtitle(
                f"{backend.MOUNT} — " + (f"not available: {st.drive_problem}" if st.drive_problem else
                                        "mounted" if st.mounted else
                                        "background service not running" if st.mount_enabled and service_down else
                                        "starting…" if st.mount_active else
                                        "enabled, not mounted" if st.mount_enabled else "off"))
            if self.sync_row.get_active() != st.sync_enabled:
                self.sync_row.set_active(st.sync_enabled)
            if ss.running:
                sub = "syncing now…"
            elif not st.sync_enabled:
                sub = "off"
            elif service_down:
                sub = "background service not running"
            else:
                last = "never synced" if not ss.last_end else \
                    f"last sync {relative_time(ss.last_end)} ({'ok' if ss.last_result == 'success' else 'failed'})"
                sub = last + (f", next {relative_time(ss.next_run)}" if ss.next_run else "")
            self.sync_row.set_subtitle(f"{backend.SYNC_DIR} ↔ {backend.SYNC_REMOTE_PATH} — {sub}")
        finally:
            self._refreshing = False

    # --- actions -----------------------------------------------------------

    def _user_toggled(self, row, known_value: bool) -> bool:
        """True only for a real user toggle: state is known, we are not refreshing,
        and the switch now differs from what the system reports."""
        return (not self._refreshing and self._known is not None
                and row.get_active() != known_value)

    def _on_drive_switch(self, row, _pspec):
        if not self._user_toggled(row, self._known.mount_enabled if self._known else False):
            return
        self._known.mount_enabled = row.get_active()
        fn = backend.mount_enable if row.get_active() else backend.mount_disable
        def done(_r, err):
            if err:
                self.toast(f"Error: {err}")
            self.win.refresh()
        def work():
            fn()
            if row.get_active():
                backend.bookmark_add()
                backend.folder_icon_set()
        run_async(work, done)

    def _on_sync_switch(self, row, _pspec):
        if not self._user_toggled(row, self._known.sync_enabled if self._known else False):
            return
        self._known.sync_enabled = row.get_active()
        fn = backend.sync_enable if row.get_active() else backend.sync_disable
        def done(_r, err):
            if err:
                self.toast(f"Error: {err}")
            self.win.refresh()
        run_async(fn, done)

    def _load_sync_log(self):
        if self.log_row.get_expanded():
            run_async(backend.sync_log_tail, lambda text, _e: self.sync_log.set_text(text or ""))

    def check_updates(self):
        self.update_check_btn.set_sensitive(False)
        self.update_spinner.start()
        def done(rel, err):
            self.update_spinner.stop()
            self.update_check_btn.set_sensitive(True)
            if err:
                self.update_row.set_subtitle(f"Version {__version__} — {err}")
                return
            if rel is None:
                self.update_row.set_subtitle(f"Version {__version__} — no releases published yet")
                return
            if updates.is_newer(rel.version, __version__):
                self.offer_release(rel)
                if rel.package_url is None:
                    updates.open_releases_page()
            else:
                self.update_row.set_subtitle(f"Version {__version__} — up to date")
        run_async(updates.fetch_latest, done)

    def offer_release(self, rel: updates.Release) -> None:
        """Show a newer release in the Updates row (from the manual check or the startup prompt)."""
        self._release = rel
        self.update_row.set_subtitle(f"Version {__version__} — version {rel.version} is available")
        self.update_install_btn.set_visible(rel.package_url is not None)

    def download_update(self):
        rel = self._release
        if not rel or not rel.package_url:
            return
        self.update_install_btn.set_sensitive(False)
        self.update_spinner.start()
        def progress(done_b, total):
            pct = f" {done_b * 100 // total}%" if total else ""
            GLib.idle_add(self.update_row.set_subtitle, f"Downloading {rel.version}…{pct}")
        def downloaded(path, err):
            if err:
                self.update_spinner.stop()
                self.update_install_btn.set_sensitive(True)
                self.toast(str(err))
                return
            if backend.is_flatpak():
                # No PackageKit in the sandbox: hand the bundle to the host's software center.
                self.update_spinner.stop()
                self.update_install_btn.set_visible(False)
                updates.open_for_install(path)
                self.update_row.set_subtitle(f"Downloaded {path.name} to {path.parent.name} — install it in the "
                                             "window that opened, then reopen Drime")
                return
            self.update_row.set_subtitle(f"Installing {rel.version}… (authorise when asked)")
            def pct(n):
                GLib.idle_add(self.update_row.set_subtitle, f"Installing {rel.version}… {n}%")
            def installed(_r, err):
                self.update_spinner.stop()
                self.update_install_btn.set_sensitive(True)
                if err:
                    if "PackageKit" in str(err):        # no PackageKit: hand the package to Software
                        updates.open_for_install(path)
                    self.toast(str(err))
                    self.update_row.set_subtitle(f"Downloaded to {path.parent} — {err}")
                    return
                self.update_install_btn.set_visible(False)
                self.update_row.set_subtitle(f"Version {rel.version} installed")
                self.win.check_upgraded_on_disk()
            run_async(lambda: updates.install_package(path, pct), installed)
        run_async(lambda: updates.download_package(rel.package_url, progress), downloaded)

    def confirm_remove(self):
        dlg = Adw.AlertDialog.new("Remove your Drime setup?",
                                  "The virtual drive will be unmounted, syncing stopped and the web app "
                                  f"signed out. {backend.SYNC_DIR} and everything in your cloud account are kept.")
        purge = Gtk.CheckButton(label="Also delete the API token (rclone remote) and uninstall pydrime")
        dlg.set_extra_child(purge)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Remove")
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response", lambda _d, r: r == "remove" and self._do_remove(purge.get_active()))
        dlg.present(self)

    def _do_remove(self, purge: bool):
        status = Adw.StatusPage(title="Removing…", icon_name="user-trash-symbolic")
        log = LogView(220)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=24, margin_end=24)
        box.append(log)
        status.set_child(box)
        page = Adw.NavigationPage.new(status, "Remove")
        page.set_can_pop(False)
        self.set_can_close(False)
        self.push_subpage(page)
        self.win.stop_refreshing()

        def done(_r, err):
            self.set_can_close(True)
            if err:
                status.set_title("Something went wrong")
                status.set_description(str(err))
                page.set_can_pop(True)
                return
            status.set_title("Drime setup removed")
            status.set_description(
                f"{backend.SYNC_DIR} and your cloud data were kept. "
                "To remove this app as well, uninstall “Drime” in your software center "
                f"or run: {backend.remove_hint()}")
            box.append(button("Quit", self.win.get_application().quit, "pill"))
        run_async(lambda: backend.uninstall_all(purge, log.append), done)


class MainWindow(Adw.ApplicationWindow):
    """The Drime window: web app + header-bar status + menu."""

    def __init__(self, app: Adw.Application, notice: str | None = None):
        super().__init__(application=app, title="Drime", default_width=1100, default_height=750)
        self._restore_size()
        self._tick_id: int | None = None
        self._boot_version = updates.installed_version()
        self._restart_offered = False
        self.settings = SettingsDialog(self)

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)
        self.web = DrimeWebView(on_download=self._downloaded, on_download_failed=self._download_failed,
                                on_notice=self.toast)
        view.set_content(self.web)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        # Header bar: reload · status pill · menu
        reload_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reload (Ctrl+R)")
        reload_btn.connect("clicked", lambda _b: self.web.reload())
        header.pack_start(reload_btn)

        self.status_icon = Gtk.Image.new_from_icon_name("drive-harddisk-symbolic")
        self.status_label = Gtk.Label(label="Checking…")
        pill = Gtk.Box(spacing=6)
        pill.append(self.status_icon)
        pill.append(self.status_label)
        self.status_btn = Gtk.Button(child=pill, tooltip_text="Drive and sync status — click for settings",
                                     css_classes=["flat"])
        self.status_btn.connect("clicked", lambda _b: self.open_settings())
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self._menu(),
                                       primary=True, tooltip_text="Menu"))
        header.pack_end(self.status_btn)

        self._add_actions()
        self.refresh()
        self._tick_id = GLib.timeout_add_seconds(10, self._tick)
        if notice:
            self.toast(notice)
        self._startup_housekeeping()
        GLib.timeout_add_seconds(5, lambda: (self._startup_update_check(), False)[1])
        self.connect("close-request", self._on_close)

    # --- menu / actions ------------------------------------------------------

    def _menu(self) -> Gio.Menu:
        m = Gio.Menu()
        s1 = Gio.Menu()
        s1.append("Open Drime folder", "win.open-drive")
        s1.append("Open Sync folder", "win.open-sync")
        s1.append("Sync now", "win.sync-now")
        m.append_section(None, s1)
        s2 = Gio.Menu()
        s2.append("Open in browser", "win.open-browser")
        s2.append("Settings", "win.settings")
        s2.append("About Drime Desktop", "win.about")
        m.append_section(None, s2)
        return m

    def _add_actions(self):
        acts = {
            "open-drive": (self.open_folder, None),
            "open-sync": (lambda: Gio.AppInfo.launch_default_for_uri(f"file://{backend.SYNC_DIR}", None), None),
            "sync-now": (self.sync_now, "<Control>s"),
            "open-browser": (lambda: open_externally(self.web.view.get_uri() or backend.WEB_URL, self), None),
            "settings": (self.open_settings, "<Control>comma"),
            "about": (self.about, None),
            "reload": (self.web.reload, "<Control>r"),
            "zoom-in": (lambda: self.web.zoom(0.1), "<Control>plus"),
            "zoom-out": (lambda: self.web.zoom(-0.1), "<Control>minus"),
            "zoom-reset": (lambda: self.web.zoom(None), "<Control>0"),
            "quit": (self.close, "<Control>q"),
        }
        app = self.get_application()
        for name, (cb, accel) in acts.items():
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
            if accel:
                app.set_accels_for_action(f"win.{name}", [accel] + (["<Control>equal"] if name == "zoom-in" else []))
        app.set_accels_for_action("win.reload", ["<Control>r", "F5"])

    def open_settings(self):
        self.settings.present(self)

    def about(self):
        dlg = Adw.AboutDialog(application_name="Drime Desktop", application_icon="drime-desktop",
                              version=__version__, developer_name="DaveTheGameDev",
                              website=f"https://github.com/{updates.GITHUB_REPO}",
                              issue_url=f"https://github.com/{updates.GITHUB_REPO}/issues",
                              license_type=Gtk.License.MIT_X11,
                              comments="Unofficial Drime cloud integration for Linux: virtual drive, "
                                       "sync folder and the Drime web app in one window.\n\n"
                                       "Not affiliated with Drime. The Drime logo belongs to Drime."
                                       + ("\n\nFlatpak build: the drive and the sync run in Drime's own "
                                          "background service." if backend.is_flatpak() else ""))
        dlg.present(self)

    # --- state -------------------------------------------------------------

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast.new(text))

    def _tick(self) -> bool:
        self.refresh()
        self.check_upgraded_on_disk()
        if backend.is_flatpak():
            run_async(backend.watchdog, lambda *_: None)   # respawn the background service if it died
        return True

    def check_upgraded_on_disk(self) -> None:
        """A package upgrade replaced our files while we run: offer a restart (once)."""
        if self._restart_offered or self._boot_version is None or backend.is_flatpak():
            return   # a Flatpak update deploys to a new directory; this instance keeps its files
        now = updates.installed_version()
        if now is None or now == self._boot_version:
            return
        self._restart_offered = True
        self.settings.update_row.set_subtitle(f"Version {now} installed — restart Drime to use it")
        self.settings.update_install_btn.set_visible(False)
        dlg = Adw.AlertDialog.new(f"Drime Desktop {now} is installed",
                                  f"This window is still running version {__version__}. "
                                  "Restart Drime to finish the update.")
        dlg.add_response("later", "Later")
        dlg.add_response("restart", "Restart")
        dlg.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("restart")
        dlg.set_close_response("later")
        dlg.connect("response", lambda _d, r: r == "restart" and self.restart())
        dlg.present(self)

    def restart(self) -> None:
        updates.restart_app()
        self.get_application().quit()

    def stop_refreshing(self) -> None:
        if self._tick_id is not None:
            GLib.source_remove(self._tick_id)
            self._tick_id = None

    def refresh(self) -> None:
        def done(result, err):
            if err or result is None:
                return
            st, ss = result
            self.settings.apply_state(st, ss)
            self._apply_indicator(st, ss)
        run_async(lambda: (backend.state(), backend.sync_status()), done)

    def _apply_indicator(self, st: backend.State, ss: backend.SyncStatus) -> None:
        warn = False
        if st.mounted:
            drive = "Drive mounted"
        elif st.mount_enabled:
            drive, warn = ("Drive starting…" if st.mount_active else "Drive not mounted"), not st.mount_active
        else:
            drive = "Drive off"
        if ss.running:
            sync = "syncing…"
        elif not st.sync_enabled:
            sync = "sync off"
        elif not ss.last_end:
            sync = "not synced yet"
        elif ss.last_result == "success":
            sync = f"synced {relative_time(ss.last_end)}"
        else:
            sync, warn = f"sync failed {relative_time(ss.last_end)}", True
        self.status_label.set_label(f"{drive} · {sync}")
        self.status_icon.set_from_icon_name("dialog-warning-symbolic" if warn else
                                            "emblem-synchronizing-symbolic" if ss.running else
                                            "drive-harddisk-symbolic")
        for cls in ("warning", "success"):
            self.status_btn.remove_css_class(cls)
        self.status_btn.add_css_class("warning" if warn else "success")

    def _startup_housekeeping(self):
        def done(result, _err):
            if result:
                self.toast("Switched to the packaged systemd units")
        def work():
            if backend.is_flatpak():
                backend.watchdog()
                return False
            migrated = backend.migrate_user_units()
            backend.cleanup_legacy()
            return migrated
        run_async(work, done)

    def _startup_update_check(self):
        """Ask once per launch when a newer release is on GitHub; silent otherwise."""
        def done(rel, err):
            if err or rel is None or not updates.is_newer(rel.version, __version__):
                return
            if rel.version == updates.skipped_version():
                return
            self.settings.offer_release(rel)
            dlg = Adw.AlertDialog.new(f"Drime Desktop {rel.version} is available",
                                      f"You have version {__version__}. Download and install it now? "
                                      + ("It will be handed to your software center to install."
                                         if backend.is_flatpak() else "You will be asked for your password."))
            dlg.add_response("later", "Later")
            dlg.add_response("skip", "Skip this version")
            if rel.package_url:
                dlg.add_response("install", "Download and install")
                dlg.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
                dlg.set_default_response("install")
            else:
                dlg.add_response("open", "View release")
                dlg.set_default_response("open")
            dlg.set_close_response("later")

            def on_response(_d, resp):
                if resp == "skip":
                    updates.skip_version(rel.version)
                elif resp == "install":
                    self.open_settings()
                    self.settings.download_update()
                elif resp == "open":
                    updates.open_releases_page()
            dlg.connect("response", on_response)
            dlg.present(self)
        run_async(updates.fetch_latest, done)

    # --- actions -----------------------------------------------------------

    def open_folder(self):
        Gio.AppInfo.launch_default_for_uri(f"file://{backend.MOUNT}", None)

    def sync_now(self):
        # Off the main thread: in the Flatpak this may have to (re)start the background service.
        run_async(backend.sync_now, lambda _r, err: self.toast(f"Error: {err}") if err else None)
        self.toast("Sync started")
        GLib.timeout_add_seconds(2, lambda: (self.refresh(), False)[1])

    def _downloaded(self, path: Path):
        t = Adw.Toast.new(f"Saved {path.name} to {path.parent.name}")
        t.set_button_label("Open")
        t.connect("button-clicked", lambda _t: Gtk.FileLauncher.new(Gio.File.new_for_path(str(path)))
                  .launch(self, None, None))
        self.toasts.add_toast(t)

    def _download_failed(self, msg: str):
        self.toast(f"Download failed: {msg}")

    # --- window size -------------------------------------------------------

    def _restore_size(self):
        try:
            d = json.loads(WINDOW_STATE.read_text())
            self.set_default_size(int(d["width"]), int(d["height"]))
            if d.get("maximized"):
                self.maximize()
        except (OSError, ValueError, KeyError):
            pass

    def _on_close(self, _win):
        try:
            WINDOW_STATE.parent.mkdir(parents=True, exist_ok=True)
            w, h = self.get_default_size()
            WINDOW_STATE.write_text(json.dumps({"width": w, "height": h, "maximized": self.is_maximized()}))
        except OSError:
            pass
        return False


class DrimeApp(Adw.Application):
    def __init__(self, notice: str | None = None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.notice = notice

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name("drime-desktop")

    def do_activate(self):
        win = self.get_active_window()
        if win is None:
            from .wizard import needs_wizard, WizardWindow
            if needs_wizard():
                win = WizardWindow(self, on_finished=self.show_main)
            else:
                win = MainWindow(self, self.notice)
        win.present()

    def show_main(self, wizard: Gtk.Window):
        wizard.close()
        MainWindow(self).present()


def run_gui(notice: str | None = None) -> int:
    return DrimeApp(notice).run(None)
