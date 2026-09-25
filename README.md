# Pytata

Pytata is a Python command-line Pomodoro helper built around Taskwarrior,
Timewarrior, dmenu, and tmux. It combines the scripts in `base_src/` into one
executable.

## Requirements

- Python 3.10 or newer
- Taskwarrior
- Timewarrior
- tmux
- dmenu
- `notify-send`
- `aplay` for notification sounds

Pytata itself only uses the Python standard library.

## Usage

Run the Pomodoro timer directly:

```console
./pytata.py pomodoro
./pytata.py --work 25 --pause 5 --pomodori 4
```

The other original helpers are available as subcommands:

```console
./pytata.py read
./pytata.py planning
./pytata.py mail
./pytata.py meeting
./pytata.py status STATUS_FILE WOFFU_FILE
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
```

Command-line options override values from the configuration file.
