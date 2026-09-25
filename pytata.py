#!/usr/bin/env python3
"""Unified Python version of the patata helper shell scripts."""

from __future__ import annotations

import argparse
import configparser
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG = """\
[pomodoro]
work = 25
pause = 5
pomodori = 4
num_beeps = 5
notification = ~/share/linux/patata/notification.wav
"""


class PatataError(RuntimeError):
    """Raised for expected command/runtime failures."""


@dataclass(frozen=True)
class PytataConfig:
    work: int
    pause: int
    pomodori: int
    num_beeps: int
    notification: Path


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
        return PytataConfig(
            work=int(section["work"]),
            pause=int(section["pause"]),
            pomodori=int(section["pomodori"]),
            num_beeps=int(section["num_beeps"]),
            notification=Path(section["notification"]).expanduser(),
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
    args = ["task"]
    if task_filter:
        args.extend(task_filter.split())
    args.extend(["next", "limit:1"])

    result = run_command(*args, capture=True)
    if result.returncode != 0:
        raise PatataError(result.stderr.strip() or "Unable to read the next Taskwarrior task.")

    lines = result.stdout.splitlines()
    if len(lines) < 4:
        raise PatataError("Taskwarrior did not return a task.")

    fields = lines[3].strip().split()
    if not fields:
        raise PatataError("Taskwarrior output did not contain a task id.")
    return fields[0]


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


def pomodoro(args: argparse.Namespace) -> int:
    task_id = args.task or task_id_from_next(args.filter)
    status_file = Path(args.output) if args.output else None
    time_left = "%im left of %s"
    time_left_file = "%im left of %s\n"

    def shutdown(signum: int | None = None, frame: object | None = None) -> None:
        if signum is not None:
            print("\rSIGINT caught      ", end="", flush=True)
        remove_status(status_file)
        task_command(task_id, "stop")
        raise SystemExit(130 if signum is not None else 0)

    signal.signal(signal.SIGINT, shutdown)

    run_command("task", task_id, "list")
    if args.simple:
        time_left = f"{time_left}\n"
    else:
        time_left = f"\r{time_left}"

    try:
        for current in range(args.pomodori, 0, -1):
            print(current)
            task_command(task_id, "start")

            for minutes_left in range(args.work, 0, -1):
                print(time_left % (minutes_left, "work"), end="", flush=True)
                write_status(status_file, time_left_file % (minutes_left, "work"))
                sleep_minutes(1)

            if not args.mute:
                play_notification(args.num_beeps, Path(args.sound).expanduser())

            if not args.simple:
                print("\a")
                print("Work over")
                write_status(status_file, "Work over. \u00a1Estira!\n")
                wait_for_enter_if_interactive(True)

            if current == 1:
                continue

            for minutes_left in range(args.pause, 0, -1):
                print(time_left % (minutes_left, "pause"), end="", flush=True)
                write_status(status_file, time_left_file % (minutes_left, "pause"))
                sleep_minutes(1)

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


def timewarrior_items(action: str) -> str:
    result = run_command("timew", "tags", action, capture=True)
    lines = [re.sub(r"\s*-\s*$", "", line) for line in result.stdout.splitlines()[3:]]
    return "\n".join(lines)


def choose_item(action: str) -> str:
    result = run_command(
        "dmenu",
        "-i",
        "-p",
        f"What to {action}?",
        capture=True,
        input_text=timewarrior_items(action),
    )
    return result.stdout.rstrip("\n")


def dmenu_template(action: str, value: str | None = None) -> int:
    item = choose_item(action) if value is None else value
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


def read_template(args: argparse.Namespace) -> int:
    return dmenu_template("read", args.value)


def planning_template(args: argparse.Namespace) -> int:
    return dmenu_template("planning", "")


def mail_template(args: argparse.Namespace) -> int:
    return dmenu_template("mail", "")


def meeting_template(args: argparse.Namespace) -> int:
    return dmenu_template("meeting", args.value)


def read_file_without_cr(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").replace("\r", "").strip()


def timewarrior_tracking_status() -> str:
    result = run_command("timew", capture=True)
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if "Tracking" in line:
            parts = line.split()
            return " ".join(parts[1:])
    return ""


def status(args: argparse.Namespace) -> int:
    current_status = read_file_without_cr(Path(args.status_file))
    if not current_status:
        current_status = timewarrior_tracking_status()

    woffu = read_file_without_cr(Path(args.woffu_file))
    if woffu:
        woffu = f"[{woffu}]"

    print(f"{woffu} {current_status}".strip())
    return 0


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
    subparsers = parser.add_subparsers(dest="command")

    pomo = subparsers.add_parser("pomodoro", aliases=["patata"], help="Run the pomodoro timer.")
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

    read_parser = subparsers.add_parser("read", aliases=["pataread"])
    read_parser.add_argument("value", nargs="?")
    read_parser.set_defaults(func=read_template)

    planning = subparsers.add_parser("planning", aliases=["plan", "pataplan"])
    planning.set_defaults(func=planning_template)

    status_parser = subparsers.add_parser("status", aliases=["patatastatus"])
    status_parser.add_argument("status_file")
    status_parser.add_argument("woffu_file")
    status_parser.set_defaults(func=status)

    mail = subparsers.add_parser("mail", aliases=["patamail"])
    mail.set_defaults(func=mail_template)

    meeting = subparsers.add_parser("meeting", aliases=["patameeting"])
    meeting.add_argument("value", nargs="?")
    meeting.set_defaults(func=meeting_template)

    end_parser = subparsers.add_parser("end", aliases=["pataend"])
    end_parser.add_argument("--target", default="patata")
    end_parser.set_defaults(func=end)

    chrono_parser = subparsers.add_parser("chrono", aliases=["patachrono"])
    chrono_parser.add_argument("tags", nargs="*")
    chrono_parser.add_argument("--interval", type=int, default=10)
    chrono_parser.set_defaults(func=chrono)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    try:
        parser = build_parser(load_config())
        if argv and argv[0].startswith("-"):
            argv.insert(0, "pomodoro")

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
