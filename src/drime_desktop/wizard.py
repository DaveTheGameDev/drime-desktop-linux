"""First-run setup wizard (token → drive → sync)."""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import backend  # noqa: E402
from .app import LogView, button, run_async  # noqa: E402

TOKEN_HELP = ("Create a token at app.drime.cloud → Settings → Developer, then paste it here. "
              "It is stored only in your rclone configuration.")
SERVICE_NOTE = ("\n\nIn this Flatpak it is run by Drime's own background service, which starts at login "
                "— your desktop may ask whether Drime is allowed to run in the background.")


def needs_wizard() -> bool:
    st = backend.state()
    return not st.configured


class Step(Adw.NavigationPage):
    """A wizard page: status page with an optional log and a Next/Skip row."""

    def __init__(self, wizard: "WizardWindow", title: str, description: str, icon: str):
        self.wizard = wizard
        self.status = Adw.StatusPage(title=title, description=description, icon_name=icon)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                            halign=Gtk.Align.FILL, margin_start=24, margin_end=24)
        self.status.set_child(self.body)
        scrolled = Gtk.ScrolledWindow(child=self.status, vexpand=True)
        super().__init__(child=scrolled, title=title)
        self.log = LogView(160)
        self.log.set_visible(False)
        self.spinner = Gtk.Spinner(halign=Gtk.Align.CENTER)
        self.error = Gtk.Label(wrap=True, css_classes=["error"], visible=False)
        self.buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                               halign=Gtk.Align.CENTER, margin_top=12)

    def finish_layout(self):
        self.body.append(self.error)
        self.body.append(self.spinner)
        self.body.append(self.log)
        self.body.append(self.buttons)

    def busy(self, on: bool):
        self.spinner.set_spinning(on)
        self.buttons.set_sensitive(not on)
        if on:
            self.error.set_visible(False)

    def fail(self, msg: str):
        self.busy(False)
        self.error.set_text(str(msg))
        self.error.set_visible(True)

    def run_step(self, fn: Callable, on_ok: Callable):
        self.busy(True)
        def done(result, err):
            if err:
                self.fail(str(err))
            else:
                self.busy(False)
                on_ok(result)
        run_async(fn, done)


class WizardWindow(Adw.ApplicationWindow):
    def __init__(self, app, on_finished: Callable[[Gtk.Window], None]):
        super().__init__(application=app, title="Set up Drime", default_width=600, default_height=640)
        self.on_finished = on_finished
        self.state = backend.state()
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.nav = Adw.NavigationView()
        view.set_content(self.nav)
        self.set_content(view)
        self.nav.add(self.welcome_page())

    # Page order; each `next_*` skips steps that are already done.
    def go_token(self):
        if self.state.remote:
            return self.go_drive()
        self.nav.push(self.token_page())

    def go_drive(self):
        if self.state.mount_enabled:
            return self.go_sync()
        self.nav.push(self.drive_page())

    def go_sync(self):
        if self.state.sync_enabled:
            return self.go_done()
        self.nav.push(self.sync_page())

    def go_done(self):
        self.nav.push(self.done_page())

    # --- pages ---------------------------------------------------------------

    def welcome_page(self):
        p = Step(self, "Welcome to Drime",
                 "This sets up your Drime cloud on this computer: a virtual drive in your home folder"
                 + (" (needs fuse3 on your system)" if backend.is_flatpak() else "")
                 + ", a two-way synced folder, and the Drime web app right here in this window.",
                 "drime-desktop")
        icon = backend.icon_path()
        if icon is not None:  # works before the packaged icon is in the theme cache / from a git checkout
            p.status.set_paintable(Gdk.Texture.new_from_filename(str(icon)))
        if self.state.problems:
            p.status.set_description("Please fix the following before continuing:")
            for prob in self.state.problems:
                p.body.append(Gtk.Label(label="• " + prob, wrap=True, xalign=0))
            p.buttons.append(button("Quit", self.close))
        else:
            p.buttons.append(button("Get started", self.go_token, "suggested-action", "pill"))
        p.finish_layout()
        return p

    def token_page(self):
        p = Step(self, "Connect your account", TOKEN_HELP, "channel-secure-symbolic")
        group = Adw.PreferencesGroup()
        entry = Adw.PasswordEntryRow(title="API token")
        group.add(entry)
        p.body.append(group)
        p.body.append(Gtk.LinkButton(label="Open app.drime.cloud", uri=backend.WEB_URL))

        def connect():
            token = entry.get_text().strip()
            if not token:
                return p.fail("Please paste your API token.")
            def work():
                backend.create_remote(token)
                if not backend.check_remote():
                    backend.delete_remote()
                    raise RuntimeError("Drime rejected this token. Check it and try again.")
            def ok(_r):
                self.state.remote = True
                self.go_drive()
            p.run_step(work, ok)
        entry.connect("entry-activated", lambda *_: connect())
        p.buttons.append(button("Connect", connect, "suggested-action", "pill"))
        p.finish_layout()
        return p

    def drive_page(self):
        problem = self.state.drive_problem
        if problem:
            p = Step(self, "Virtual drive",
                     f"The virtual drive is not available in this installation: {problem} "
                     "You can turn it on later in Settings once fuse3 is installed.",
                     "drive-harddisk-symbolic")
            p.buttons.append(button("Continue", self.go_sync, "suggested-action", "pill"))
            p.finish_layout()
            return p
        p = Step(self, "Virtual drive",
                 f"Your whole Drime account will appear at {backend.MOUNT}, mounted automatically "
                 "at login. Files download on demand and are cached locally (up to 10 GB); "
                 "anything you save there uploads automatically."
                 + (SERVICE_NOTE if backend.is_flatpak() else ""),
                 "drive-harddisk-symbolic")
        def enable():
            def work():
                backend.mount_enable(p.log.append)
                backend.bookmark_add()
                backend.folder_icon_set()
            def ok(_r):
                self.state.mount_enabled = True
                self.go_sync()
            p.run_step(work, ok)
        p.buttons.append(button("Skip", self.go_sync, "pill"))
        p.buttons.append(button("Enable drive", enable, "suggested-action", "pill"))
        p.finish_layout()
        return p

    def sync_page(self):
        p = Step(self, "Sync folder",
                 f"{backend.SYNC_DIR} will mirror the “Sync” folder of your cloud in both directions "
                 "every 15 minutes, so you always have an offline copy. A small RCLONE_TEST marker file "
                 "is placed on both sides as a safety check — please don't delete it.\n\n"
                 "The first run compares both sides and can take a while if the folder is large."
                 + (SERVICE_NOTE if backend.is_flatpak() else ""),
                 "folder-remote-symbolic")
        def enable():
            p.log.set_visible(True)
            def ok(_r):
                self.state.sync_enabled = True
                self.go_done()
            p.run_step(lambda: backend.sync_enable(p.log.append), ok)
        p.buttons.append(button("Skip", self.go_done, "pill"))
        p.buttons.append(button("Enable sync", enable, "suggested-action", "pill"))
        p.finish_layout()
        return p

    def done_page(self):
        p = Step(self, "All set",
                 "The Drime web app opens next. The status pill in its title bar shows the drive and "
                 "sync state; the \u2630 menu has Sync now, and Settings (Ctrl+,) lets you toggle the "
                 "drive and the sync folder, check for updates or remove the setup.",
                 "emblem-ok-symbolic")
        p.set_can_pop(False)
        p.buttons.append(button("Open Drime", lambda: self.on_finished(self), "suggested-action", "pill"))
        p.finish_layout()
        return p
