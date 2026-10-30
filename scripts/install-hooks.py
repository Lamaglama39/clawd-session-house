#!/usr/bin/env python3
"""Register or remove the status dashboard hooks in a Claude Code settings file.

Only hook handlers whose command carries this installer's tag comment
(``# clawd-session-house``) are added or removed; every other setting and
hook is preserved. A timestamped backup is written before the file is changed.

Usage:
    python3 scripts/install-hooks.py              # ~/.claude/settings.json (all projects)
    python3 scripts/install-hooks.py --dry-run    # show the result without writing
    python3 scripts/install-hooks.py --uninstall
    python3 scripts/install-hooks.py --settings PROJECT/.claude/settings.local.json
    python3 scripts/install-hooks.py --port 9000
    python3 scripts/install-hooks.py --url http://192.168.1.10:8765/hook
"""

import argparse
import json
import os
import shlex
import shutil
import sys
import tempfile
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SEND_EVENT = os.path.join(os.path.dirname(SCRIPT_DIR), "hooks", "send-event.sh")
DEFAULT_SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
DEFAULT_PORT = 8765

# Marker for entries added by this installer. It is appended to the command as a shell comment,
# which has no effect on execution, so entries are recognized even after the clone moves
HOOK_TAG = "# clawd-session-house"

NOTIFICATION_MATCHER = "permission_prompt|idle_prompt|elicitation_dialog|elicitation_url_dialog"

# (event name, matcher). A matcher of None matches everything
HOOK_EVENTS = (
    ("SessionStart", None),
    ("SessionEnd", None),
    ("UserPromptSubmit", None),
    ("PreToolUse", None),
    ("PermissionRequest", None),
    ("PermissionDenied", None),
    ("PostToolUse", None),
    ("PostToolUseFailure", None),
    ("Elicitation", None),
    ("ElicitationResult", None),
    ("Notification", NOTIFICATION_MATCHER),
    ("Stop", None),
    ("StopFailure", None),
    # Used for the subagent display and task progress
    ("SubagentStart", None),
    ("SubagentStop", None),
    ("TaskCreated", None),
    ("TaskCompleted", None),
)

# SessionStart gets a longer timeout to cover waiting for the autostarted server
TIMEOUTS = {"SessionStart": 10}
DEFAULT_TIMEOUT = 5


def is_ours(handler):
    """Return True if a hook handler was added by this installer."""
    if not isinstance(handler, dict):
        return False
    return str(handler.get("command", "")).endswith(HOOK_TAG)


def build_command(event, port, url, autostart):
    """Build the shell command for one hook event."""
    env = []
    if url:
        env.append(f"CLAUDE_STATUS_URL={shlex.quote(url)}")
    elif port != DEFAULT_PORT:
        env.append(f"CLAUDE_STATUS_PORT={port}")
    cmd = shlex.quote(SEND_EVENT)
    # A server on another machine cannot be started locally, so skip the autostart
    if event == "SessionStart" and autostart and not url:
        cmd += " --autostart"
    return " ".join(env + [cmd, HOOK_TAG])


def remove_ours(hooks):
    """Remove this installer's handlers from a hooks mapping (in place).

    Returns:
        Number of handlers removed.
    """
    removed = 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            handlers = [h for h in group["hooks"] if not is_ours(h)]
            removed += len(group["hooks"]) - len(handlers)
            if handlers:
                kept_groups.append({**group, "hooks": handlers})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    return removed


def install(settings, port=DEFAULT_PORT, url=None, autostart=True):
    """Return a copy of settings with this dashboard's hooks registered (idempotent)."""
    result = json.loads(json.dumps(settings))
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    remove_ours(hooks)
    for event, matcher in HOOK_EVENTS:
        group = {}
        if matcher:
            group["matcher"] = matcher
        group["hooks"] = [
            {
                "type": "command",
                "command": build_command(event, port, url, autostart),
                "timeout": TIMEOUTS.get(event, DEFAULT_TIMEOUT),
            }
        ]
        hooks.setdefault(event, []).append(group)
    result["hooks"] = hooks
    return result


def uninstall(settings):
    """Return a copy of settings without this dashboard's hooks, and the removed count."""
    result = json.loads(json.dumps(settings))
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result, 0
    removed = remove_ours(hooks)
    if not hooks:
        del result["hooks"]
    return result, removed


def load_settings(path):
    """Load a settings file; a missing file is treated as empty settings."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("settings file must contain a JSON object")
    return data


def write_settings(path, data):
    """Back up the current file (if any) and atomically write data.

    Returns:
        The backup path, or None if there was nothing to back up.
    """
    backup = None
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if os.path.exists(path):
        backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if backup:
            shutil.copymode(backup, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return backup


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Install Claude Code status dashboard hooks")
    parser.add_argument(
        "--settings",
        default=DEFAULT_SETTINGS,
        help="settings file to edit (default: %(default)s)",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="local server port")
    parser.add_argument("--url", help="send events to this URL instead of the local server")
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        help="do not start the server from the SessionStart hook",
    )
    parser.add_argument("--uninstall", action="store_true", help="remove the hooks")
    parser.add_argument("--dry-run", action="store_true", help="print the result only")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    path = os.path.abspath(os.path.expanduser(args.settings))
    try:
        current = load_settings(path)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot read {path}: {e}")

    if args.uninstall:
        updated, removed = uninstall(current)
        summary = f"removed {removed} hook handler(s)"
    else:
        updated = install(current, port=args.port, url=args.url, autostart=not args.no_autostart)
        summary = f"registered {len(HOOK_EVENTS)} hook event(s)"

    if args.dry_run:
        print(json.dumps(updated.get("hooks", {}), ensure_ascii=False, indent=2))
        print(f"(dry run) {summary} in {path}", file=sys.stderr)
        return

    if updated == current:
        print(f"no changes: {path}")
        return
    backup = write_settings(path, updated)
    print(f"{summary} in {path}")
    if backup:
        print(f"backup: {backup}")


if __name__ == "__main__":
    main()
