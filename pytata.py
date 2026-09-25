#!/usr/bin/env python3
"""Unified Python version of the patata helper shell scripts."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


__version__ = "0.1.0"


DEFAULT_CONFIG = """\
[pomodoro]
work = 25
pause = 5
pomodori = 4
num_beeps = 5
notification = ~/share/linux/patata/notification.wav

[menu]
command = dmenu -i -p {prompt}

[action:read]
prompt = true

[action:planning]
prompt = false
aliases = plan

[action:mail]
prompt = false

[action:meeting]
prompt = true
"""


class PatataError(RuntimeError):
    """Raised for expected command/runtime failures."""


@dataclass(frozen=True)
class ActionConfig:
    name: str
    prompt: bool
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class PytataConfig:
    work: int
    pause: int
    pomodori: int
    num_beeps: int
    notification: Path
    menu_command: tuple[str, ...]
    actions: tuple[ActionConfig, ...]


def validate_actions(actions: tuple[ActionConfig, ...]) -> None:
    reserved = {
        "patata",
        "end",
        "chrono",
    }
    seen = set(reserved)
    for action in actions:
        for command_name in (action.name, *action.aliases):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", command_name):
                raise ValueError(f"invalid action name or alias: {command_name!r}")
            if command_name in seen:
                raise ValueError(f"duplicate or reserved action name: {command_name}")
            seen.add(command_name)


def config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return config_home / "pytata" / "config.ini"


def load_config(path: Path | None = None) -> PytataConfig:
    path = path or config_path()
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_CONFIG, encoding="utf-8")
        except OSError as exc:
            raise PatataError(f"Unable to create configuration file {path}: {exc}") from exc

    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as config_file:
            parser.read_file(config_file)
        section = parser["pomodoro"]
        menu_command = tuple(shlex.split(parser["menu"]["command"]))
        if not menu_command:
            raise ValueError("menu command cannot be empty")
        if not any("{prompt}" in argument for argument in menu_command):
            raise ValueError("menu command must include {prompt}")
        actions = tuple(
            ActionConfig(
                name=section_name.removeprefix("action:").strip(),
                prompt=parser[section_name].getboolean("prompt"),
                aliases=tuple(
                    alias.strip()
                    for alias in parser[section_name].get("aliases", "").split(",")
                    if alias.strip()
                ),
            )
            for section_name in parser.sections()
            if section_name.startswith("action:")
        )
        if not actions or any(not action.name for action in actions):
            raise ValueError("at least one named [action:NAME] section is required")
        validate_actions(actions)
        return PytataConfig(
            work=int(section["work"]),
            pause=int(section["pause"]),
            pomodori=int(section["pomodori"]),
            num_beeps=int(section["num_beeps"]),
            notification=Path(section["notification"]).expanduser(),
            menu_command=menu_command,
            actions=actions,
        )
    except (OSError, KeyError, TypeError, ValueError, configparser.Error) as exc:
        raise PatataError(f"Invalid configuration file {path}: {exc}") from exc


def run_command(
    *args: str,
    check: bool = False,
    capture: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=check,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def task_id_from_next(task_filter: str | None = None) -> str:
    args = ["task", "rc.json.array=on", "+PENDING", "-WAITING"]
    try:
        if task_filter:
            args.extend(shlex.split(task_filter))
    except ValueError as exc:
        raise PatataError(f"Invalid Taskwarrior filter: {exc}") from exc
    args.append("export")

    result = run_command(*args, capture=True)
    if result.returncode != 0:
        raise PatataError(result.stderr.strip() or "Unable to read the next Taskwarrior task.")

    try:
        tasks = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PatataError(f"Invalid Taskwarrior JSON output: {exc}") from exc

    if not isinstance(tasks, list):
        raise PatataError("Invalid Taskwarrior JSON output: expected a list.")
    if not tasks:
        raise PatataError("Taskwarrior did not return a task.")

    try:
        next_task = max(tasks, key=lambda task: float(task.get("urgency", 0)))
        return str(next_task["uuid"])
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PatataError(f"Invalid Taskwarrior JSON output: {exc}") from exc


def task_command(task_id: str, command: str) -> None:
    run_command("task", task_id, command)


def play_notification(num_beeps: int, sound: Path) -> None:
    for _ in range(num_beeps):
        try:
            run_command("aplay", "-q", str(sound))
        except FileNotFoundError:
            return


def write_status(status_file: Path | None, text: str) -> None:
    if status_file:
        status_file.write_text(text, encoding="utf-8")


def remove_status(status_file: Path | None) -> None:
    if status_file and status_file.exists():
        status_file.unlink()


def wait_for_enter_if_interactive(interactive: bool) -> None:
    if interactive:
        try:
            input()
        except EOFError:
            pass


def sleep_minutes(minutes: int) -> None:
    time.sleep(minutes * 60)


def run_interval(minutes: int, label: str, status_file: Path | None, simple: bool) -> None:
    prefix = "" if simple else "\r"
    suffix = "\n" if simple else ""
    for minutes_left in range(minutes, 0, -1):
        message = f"{minutes_left}m left of {label}"
        print(f"{prefix}{message}{suffix}", end="", flush=True)
        write_status(status_file, f"{message}\n")
        sleep_minutes(1)


def pomodoro(args: argparse.Namespace) -> int:
    task_id = args.task or task_id_from_next(args.filter)
    status_file = Path(args.output) if args.output else None

    def shutdown(signum: int | None = None, frame: object | None = None) -> None:
        if signum is not None:
            print("\rSIGINT caught      ", end="", flush=True)
        remove_status(status_file)
        task_command(task_id, "stop")
        raise SystemExit(130 if signum is not None else 0)

    signal.signal(signal.SIGINT, shutdown)

    run_command("task", task_id, "list")
    try:
        for current in range(args.pomodori, 0, -1):
            print(current)
            task_command(task_id, "start")

            run_interval(args.work, "work", status_file, args.simple)

            if not args.mute:
                play_notification(args.num_beeps, Path(args.sound).expanduser())

            if not args.simple:
                print("\a")
                print("Work over")
                write_status(status_file, "Work over. \u00a1Estira!\n")
                wait_for_enter_if_interactive(True)

            if current == 1:
                continue

            run_interval(args.pause, "pause", status_file, args.simple)

            if not args.mute:
                play_notification(args.num_beeps, Path(args.sound).expanduser())

            if not args.simple:
                print("\a")
                print("Pause over")
                write_status(status_file, "Pause over\n")
                wait_for_enter_if_interactive(True)

        task_command(task_id, "stop")
        print("Take a coffee break! \u2615")
        remove_status(status_file)
        return 0
    except KeyboardInterrupt:
        shutdown(signal.SIGINT, None)
        return 130


def timewarrior_items(action: str) -> list[str]:
    result = run_command("timew", "tags", action, capture=True)
    return [re.sub(r"\s*-\s*$", "", line) for line in result.stdout.splitlines()[3:]]


def select_from_menu(
    menu_command: tuple[str, ...], prompt: str, choices: Iterable[str]
) -> str | None:
    command = tuple(argument.replace("{prompt}", prompt) for argument in menu_command)
    result = run_command(
        *command,
        capture=True,
        input_text="\n".join(choices),
    )
    selected = result.stdout.rstrip("\n")
    if result.returncode != 0 or not selected:
        return None
    return selected


def choose_item(action: str, menu_command: tuple[str, ...]) -> str | None:
    return select_from_menu(
        menu_command,
        f"What to {action}?",
        timewarrior_items(action),
    )


def choose_action(
    actions: tuple[ActionConfig, ...], menu_command: tuple[str, ...]
) -> str | None:
    selected = select_from_menu(
        menu_command,
        "What to do?",
        (action.name for action in actions),
    )
    if selected is None:
        return None

    valid_commands = {
        command_name
        for action in actions
        for command_name in (action.name, *action.aliases)
    }
    if selected not in valid_commands:
        raise PatataError(f"Unknown action selected: {selected}")
    return selected


def start_action(
    action: str, menu_command: tuple[str, ...], value: str | None = None
) -> int:
    item = choose_item(action, menu_command) if value is None else value
    if item is None:
        return 0
    run_command("notify-send", f"Patata: {action} {item}")

    chrono_command = shlex.join(
        [sys.executable, str(Path(__file__).resolve()), "chrono", action, item]
    )
    return run_command(
        "tmux",
        "new-session",
        "-d",
        "-s",
        "patata",
        "-n",
        "patata",
        "-c",
        str(Path.home()),
        chrono_command,
    ).returncode


def run_action(args: argparse.Namespace) -> int:
    value = args.value if args.value is not None or args.action_prompt else ""
    return start_action(args.action_name, args.menu_command, value)


def end(args: argparse.Namespace) -> int:
    run_command("notify-send", "Finishing task")
    return run_command("tmux", "send-keys", "-t", args.target, "C-c").returncode


def format_elapsed(start: float) -> str:
    elapsed = int(time.time() - start)
    days, remainder = divmod(elapsed, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days} day(s) and {hours:02}:{minutes:02}:{seconds:02}"


def chrono(args: argparse.Namespace) -> int:
    tags = args.tags
    if not tags:
        raise PatataError("chrono needs at least one Timewarrior tag.")

    def shutdown(signum: int | None = None, frame: object | None = None) -> None:
        run_command("timew", "stop", *tags)
        print("\nBye!")
        raise SystemExit(130 if signum is not None else 0)

    signal.signal(signal.SIGINT, shutdown)

    print(f"Tracking {' '.join(tags)}")
    run_command("timew", "track", *tags)
    start = time.time()

    while True:
        print(format_elapsed(start), end="\r", flush=True)
        time.sleep(args.interval)


def build_parser(config: PytataConfig) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pytata",
        description="Unified Python CLI for the original patata shell helpers.",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command")

    pomo = subparsers.add_parser("patata", help="Run the pomodoro timer.")
    pomo.add_argument("-s", "--simple", action="store_true", help="Use simple line-oriented output.")
    pomo.add_argument("-m", "--mute", action="store_true", help="Do not play notification sounds.")
    pomo.add_argument("-w", "--work", type=int, default=config.work, metavar="MINUTES")
    pomo.add_argument("-b", "--pause", type=int, default=config.pause, metavar="MINUTES")
    pomo.add_argument("-p", "--pomodori", type=int, default=config.pomodori, metavar="COUNT")
    pomo.add_argument("-t", "--task", metavar="TASK_ID")
    pomo.add_argument("-o", "--output", metavar="STATUS_FILE")
    pomo.add_argument("-f", "--filter", metavar="TASK_FILTER")
    pomo.add_argument("--num-beeps", type=int, default=config.num_beeps)
    pomo.add_argument("--sound", default=str(config.notification))
    pomo.set_defaults(func=pomodoro)

    for action in config.actions:
        action_parser = subparsers.add_parser(action.name, aliases=list(action.aliases))
        action_parser.add_argument("value", nargs="?")
        action_parser.set_defaults(
            func=run_action,
            action_name=action.name,
            action_prompt=action.prompt,
            menu_command=config.menu_command,
        )

    end_parser = subparsers.add_parser("end")
    end_parser.add_argument("--target", default="patata")
    end_parser.set_defaults(func=end)

    chrono_parser = subparsers.add_parser("chrono")
    chrono_parser.add_argument("tags", nargs="*")
    chrono_parser.add_argument("--interval", type=int, default=10)
    chrono_parser.set_defaults(func=chrono)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (["-V"], ["--version"]):
        print(f"pytata {__version__}")
        return 0

    try:
        config = load_config()
        parser = build_parser(config)
        if not argv:
            selected_action = choose_action(config.actions, config.menu_command)
            if selected_action is None:
                return 0
            argv.append(selected_action)
        elif argv[0].startswith("-") and argv[0] not in {"-V", "--version"}:
            argv.insert(0, "patata")

        args = parser.parse_args(argv)
        if not hasattr(args, "func"):
            parser.print_help()
            return 1
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"Missing external command: {exc.filename}", file=sys.stderr)
        return 127
    except PatataError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
