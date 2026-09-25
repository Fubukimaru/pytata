# Pytata

Pytata is a Python command-line Pomodoro helper built around Taskwarrior,
Timewarrior, a configurable menu launcher, and tmux. It combines the scripts
in `base_src/` into one executable.

## Requirements

- Python 3.10 or newer
- Taskwarrior
- Timewarrior
- tmux
- dmenu, rofi, or another compatible menu launcher
- `notify-send`
- `aplay` for notification sounds

Pytata itself only uses the Python standard library.

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

## Configuration

On first use, Pytata creates `~/.config/pytata/config.ini`. It respects
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

The menu command receives choices on standard input and must print the selected
line on standard output. `{prompt}` is replaced without invoking a shell. To use
rofi instead, set `command = rofi -dmenu -i -p {prompt}`.

Each `[action:NAME]` section creates a subcommand. When `prompt` is true,
running the action without a value opens the menu using matching Timewarrior tags.
When it is false, the action starts immediately with an empty value. Explicit
values always bypass the menu, and `aliases` is an optional comma-separated list.
