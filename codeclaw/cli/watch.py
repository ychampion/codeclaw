"""Watch daemon subcommand handlers."""

import json


def _handle_watch(args) -> None:
    from ..daemon import daemon_status, start_daemon, stop_daemon, trigger_sync_now

    if args.start:
        print(json.dumps(start_daemon(), indent=2))
        return
    if args.stop:
        print(json.dumps(stop_daemon(), indent=2))
        return
    if args.status:
        print(json.dumps(daemon_status(), indent=2))
        return
    if args.now:
        print(json.dumps(trigger_sync_now(), indent=2))
        return


def _run_setup_wizard(args) -> None:
    """Backward-compatible wrapper to the guided setup handler."""
    from .setup import handle_setup

    handle_setup(args)
