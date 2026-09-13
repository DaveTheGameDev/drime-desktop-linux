"""Command-line entry point. Without arguments the GUI starts."""
from __future__ import annotations

import argparse
import getpass
import sys

from . import __version__, backend


def _print(line: str) -> None:
    print(line, flush=True)


def cmd_install(args) -> int:
    token = None
    if not backend.remote_exists():
        if args.token_from_stdin:
            token = sys.stdin.readline().strip()
        else:
            print("A Drime API token is required.")
            print("Get one at: app.drime.cloud -> Settings -> Developer -> create token")
            token = getpass.getpass("Paste your Drime API token: ")
    try:
        backend.install_all(token, _print, with_pydrime=args.with_pydrime)
    except Exception as e:  # noqa: BLE001 - surface any failure to the user
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print()
    print("Done. Summary:")
    problem = backend.drive_problem()
    if problem:
        print(f"  - Virtual drive:  skipped - {problem}")
    else:
        print(f"  - Virtual drive:  {backend.MOUNT} "
              + ("(Drime background service)" if backend.is_flatpak() else f"({backend.MOUNT_UNIT})"))
    print(f"  - Sync folder:    {backend.SYNC_DIR} <-> {backend.SYNC_REMOTE_PATH} every 15 min")
    print("  - App:            'Drime' in your application grid (web app, status, updates)")
    print(f"  - Keep the RCLONE_TEST file in {backend.SYNC_DIR} - it is a safety marker.")
    return 0


def cmd_uninstall(args) -> int:
    backend.uninstall_all(purge_config=args.purge_config, log=_print)
    if backend.is_flatpak() or backend.ICON_SYSTEM.is_file():
        print(f"To remove the application itself: {backend.remove_hint()}")
    return 0


def cmd_status(_args) -> int:
    st = backend.state()
    print(f"drime-desktop {__version__}")
    for p in st.problems:
        print(f"PROBLEM: {p}")
    print(f"API token configured: {'yes' if st.remote else 'no'}")
    print(f"Virtual drive:        enabled={st.mount_enabled} active={st.mount_active} mounted={st.mounted}"
          + (f" ({st.drive_problem})" if st.drive_problem else ""))
    print(f"Sync folder:          enabled={st.sync_enabled} initialized={st.sync_initialized}")
    s = backend.sync_status()
    print(f"Last sync:            {s.last_result or 'never'} (ended {s.last_end or '-'}), next {s.next_run or '-'}")
    if backend.is_flatpak():
        print(f"Background service:   {'running' if st.daemon_alive else 'not running'} (Flatpak)")
    else:
        print(f"Units:                {'packaged' if st.packaged_units else 'user'}"
              + (f", user copies present: {', '.join(st.user_unit_copies)}" if st.user_unit_copies else ""))
    return 0


def cmd_check_update(_args) -> int:
    from . import updates
    try:
        rel = updates.fetch_latest()
    except updates.UpdateError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if rel is None:
        print(f"Installed {__version__}; no releases published yet.")
        return 0
    if updates.is_newer(rel.version, __version__):
        print(f"Update available: {rel.version} (installed {__version__})")
        print(rel.package_url or rel.html_url)
        return 10
    print(f"Up to date ({__version__}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="drime-desktop",
                                description="Unofficial Drime cloud desktop integration. "
                                            "Without options, opens the graphical app.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="set up everything from the terminal")
    g.add_argument("--uninstall", action="store_true", help="remove the setup (keeps ~/DrimeSync)")
    g.add_argument("--status", action="store_true", help="print the current state")
    g.add_argument("--check-update", action="store_true", help="check GitHub for a newer release")
    g.add_argument("--daemon", action="store_true",
                   help="run the background service (drive holder and sync scheduler; the Flatpak uses it "
                        "instead of the systemd units)")
    p.add_argument("--token-from-stdin", action="store_true", help="(--install) read the API token from stdin")
    p.add_argument("--with-pydrime", action="store_true", help="(--install) also install the pydrime CLI")
    p.add_argument("--purge-config", action="store_true",
                   help="(--uninstall) also delete the API token and pydrime config")
    args = p.parse_args(argv)

    if args.install:
        return cmd_install(args)
    if args.uninstall:
        return cmd_uninstall(args)
    if args.status:
        return cmd_status(args)
    if args.check_update:
        return cmd_check_update(args)
    if args.daemon:
        from .daemon import main as daemon_main
        return daemon_main()
    from .app import run_gui
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
