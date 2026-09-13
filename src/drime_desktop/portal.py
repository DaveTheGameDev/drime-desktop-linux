"""The Background portal: let the desktop start `drime-desktop --daemon` at login.

Used by the Flatpak in place of `systemctl --user enable`. Talks D-Bus directly
through Gio (the GNOME runtime ships no libportal). Never raises: a denied or
failed request returns False and the caller reports it.
"""
from __future__ import annotations

import secrets
import sys

COMMANDLINE = ["drime-desktop", "--daemon"]
REASON = "Keep the Drime drive mounted and the sync folder up to date"
PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"


def _request_path(unique_name: str, token: str) -> str:
    """Object path of the portal Request for our bus name and handle token
    (':1.23' -> /org/freedesktop/portal/desktop/request/1_23/<token>)."""
    sender = unique_name.lstrip(":").replace(".", "_")
    return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"


def request_background(autostart: bool, timeout: float = 60) -> bool:
    """RequestBackground(autostart, commandline=drime-desktop --daemon).

    autostart=True writes ~/.config/autostart/<app-id>.desktop on the host (the
    desktop may ask the user first); autostart=False removes it. Returns whether
    the autostart entry is now in the requested state."""
    try:
        from gi.repository import Gio, GLib
    except ImportError as e:
        print(f"portal: PyGObject unavailable ({e})", file=sys.stderr)
        return False

    ctx = GLib.MainContext.new()
    ctx.push_thread_default()   # works from worker threads: our own loop, not the GUI's
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        token = "drime" + secrets.token_hex(8)
        result: dict = {}

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            code, results = params.unpack()
            result["code"], result["results"] = code, results

        sub = bus.signal_subscribe(PORTAL_BUS, "org.freedesktop.portal.Request", "Response",
                                   _request_path(bus.get_unique_name(), token), None,
                                   Gio.DBusSignalFlags.NO_MATCH_RULE, on_response)
        options = {
            "handle_token": GLib.Variant("s", token),
            "reason": GLib.Variant("s", REASON),
            "autostart": GLib.Variant("b", autostart),
            "commandline": GLib.Variant("as", COMMANDLINE),
            "dbus-activatable": GLib.Variant("b", False),
        }
        bus.call_sync(PORTAL_BUS, PORTAL_PATH, "org.freedesktop.portal.Background", "RequestBackground",
                      GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType("(o)"),
                      Gio.DBusCallFlags.NONE, int(timeout * 1000), None)
        timer = GLib.timeout_source_new(int(timeout * 1000))
        timer.set_callback(lambda *_: result.setdefault("code", None) and False)
        timer.attach(ctx)   # wakes the loop below when the desktop never answers
        while "code" not in result:
            ctx.iteration(True)
        timer.destroy()
        bus.signal_unsubscribe(sub)
        if result.get("code") != 0:
            print(f"portal: RequestBackground not granted (response {result.get('code', 'timeout')})",
                  file=sys.stderr)
            return not autostart and result.get("code") is not None
        return bool(result["results"].get("autostart", False)) == autostart
    except GLib.Error as e:
        print(f"portal: {e.message}", file=sys.stderr)
        return False
    finally:
        ctx.pop_thread_default()
