# pytata

pytata is a Python command-line Pomodoro helper built around Taskwarrior,
Timewarrior, a configurable menu launcher, and tmux. It combines the scripts
in `base_src/` into one executable.

This project was inspired by [KubikPixel/patata](https://github.com/KubikPixel/patata),
a shell Pomodoro timer with Taskwarrior integration.

## Requirements

- Python 3.10 or newer
- Taskwarrior
- Timewarrior
- tmux
- dmenu, rofi, or another compatible menu launcher
- `notify-send`
- `aplay` for notification sounds

pytata itself only uses the Python standard library.

## Installation

The recommended installation method is [pipx](https://pipx.pypa.io/), which
keeps command-line applications in isolated environments:

```console
pipx install git+https://github.com/Fubukimaru/pytata.git
```

To install from a local checkout with pip:

```console
python -m pip install .
```

For development, use an editable installation:

```console
python -m pip install --editable .
```

All methods create a `pytata` command. Confirm the installed version with:

```console
pytata --version
```

## Usage

Run the Pomodoro timer directly:

```console
./pytata.py patata
./pytata.py --work 25 --pause 5 --pomodori 4
```

Running `./pytata.py` without arguments opens the configured menu with the
available actions.

The other original helpers are available as subcommands:

```console
./pytata.py read
./pytata.py planning
./pytata.py mail
./pytata.py meeting
./pytata.py end
./pytata.py chrono TAG [TAG ...]
```

Use `./pytata.py COMMAND --help` for command-specific options.

When installed as a package, replace `./pytata.py` in these examples with
`pytata`.

## Versioning

pytata follows semantic versioning. The current version is defined once as
`__version__` in `pytata.py`; package metadata and `pytata --version` both read
that value.

## Configuration

On first use, pytata creates `~/.config/pytata/config.ini`. It respects
`XDG_CONFIG_HOME` when that environment variable is set.

```ini
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
```

Command-line options override values from the configuration file.

### Menu launchers

The menu command receives choices on standard input and must print the selected
line on standard output. It must include `{prompt}`, which pytata replaces with
the current prompt before starting the process.

Only one `command` entry should be active. Other launchers can be retained as
comments for quick switching:

```ini
[menu]
# command = dmenu -i -p {prompt}
command = rofi -dmenu -i -p {prompt}
```

Common configurations follow.

#### dmenu (X11)

```ini
[menu]
command = dmenu -i -p {prompt}
```

#### rofi (X11 or Wayland)

```ini
[menu]
command = rofi -dmenu -i -p {prompt}
```

#### wofi (Wayland)

```ini
[menu]
command = wofi --dmenu --prompt {prompt}
```

#### fuzzel (Wayland)

```ini
[menu]
command = fuzzel --dmenu --prompt {prompt}
```

The command is parsed as arguments and executed directly, without a shell.
Shell syntax such as pipes, redirects, aliases, and environment-variable
expansion is therefore unavailable. The launcher executable must be in `PATH`;
quoted arguments and absolute executable paths are supported.

Each `[action:NAME]` section creates a subcommand. When `prompt` is true,
running the action without a value opens the menu using matching Timewarrior tags.
When it is false, the action starts immediately with an empty value. Explicit
values always bypass the menu, and `aliases` is an optional comma-separated list.
