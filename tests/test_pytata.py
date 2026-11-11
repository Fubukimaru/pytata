import argparse
from array import array
import os
import pty
import select
import shlex
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import MagicMock, PropertyMock, patch

import pytata


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.audio = MagicMock()
        self.audio.MiniaudioError = type("AudioError", (Exception,), {})
        self.decoded = self.audio.decode_file.return_value
        self.decoded.samples = array("h", range(10))
        self.decoded.nchannels = 2
        self.decoded.sample_width = 2
        self.decoded.sample_rate = 44100
        self.decoded.num_frames = 5
        self.device = self.audio.PlaybackDevice.return_value.__enter__.return_value
        self.device.start.side_effect = self.start
        type(self.device).running = PropertyMock(side_effect=self.advance)
        self.audio.stream_raw_pcm_memory.side_effect = self.raw_stream
        self.played = bytearray()
        self.modules = patch.dict(sys.modules, {"miniaudio": self.audio})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        sleep = patch("pytata.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def start(self, stream):
        self.stream = stream

    def advance(self):
        try:
            chunk = self.stream.send(3)
        except StopIteration:
            return False
        self.assertLessEqual(len(chunk), 3 * 2 * 2)
        self.played.extend(chunk)
        return True

    @staticmethod
    def raw_stream(samples, channels, width):
        def generate():
            frames = yield b""
            data = samples.tobytes()
            offset = 0
            while offset < len(data):
                end = offset + frames * channels * width
                frames = yield data[offset:end]
                offset = end
        stream = generate()
        next(stream)
        return stream

    def test_configured_number_of_beeps(self):
        for count in (1, 2, 5, 10):
            with self.subTest(count=count):
                self.played.clear()
                self.assertFalse(pytata.play_notification(count, pytata.Path("alert.wav")))
                self.assertEqual(self.played, self.decoded.samples.tobytes() * count)
        self.assertEqual(self.audio.decode_file.call_count, 4)
        self.assertEqual(self.audio.PlaybackDevice.return_value.__exit__.call_count, 4)

    def test_zero_beeps_does_not_open_audio_device(self):
        self.assertFalse(pytata.play_notification(0, pytata.Path("alert.wav")))
        self.audio.decode_file.assert_not_called()
        self.audio.PlaybackDevice.assert_not_called()

    def test_enter_stops_current_sound_and_remaining_repeats(self):
        read_fd, write_fd = os.pipe()
        with os.fdopen(write_fd, "w") as writer:
            writer.write("\n")
        with os.fdopen(read_fd) as stdin, patch("pytata.sys.stdin", stdin):
            self.assertTrue(pytata.play_notification(10, pytata.Path("alert.wav"), True))
        self.assertLess(len(self.played), len(self.decoded.samples.tobytes()))
        self.audio.PlaybackDevice.return_value.__exit__.assert_called_once()

    @patch("pytata.select.select", return_value=([], [], []))
    def test_completed_alert_still_needs_acknowledgment(self, select):
        self.assertFalse(pytata.play_notification(2, pytata.Path("alert.wav"), True))
        self.assertEqual(self.played, self.decoded.samples.tobytes() * 2)
        self.audio.PlaybackDevice.return_value.__exit__.assert_called_once()

    @patch("pytata.sys.stdin", new_callable=StringIO)
    @patch("pytata.select.select")
    def test_eof_does_not_stop_playback(self, select, stdin):
        select.return_value = ([stdin], [], [])
        self.assertFalse(pytata.play_notification(5, pytata.Path("alert.wav"), True))
        self.assertEqual(self.played, self.decoded.samples.tobytes() * 5)
        select.assert_called_once()
        self.sleep.assert_called()
        self.audio.PlaybackDevice.return_value.__exit__.assert_called_once()

    @patch("pytata.select.select", side_effect=KeyboardInterrupt)
    def test_interrupt_releases_audio_device(self, select):
        with self.assertRaises(KeyboardInterrupt):
            pytata.play_notification(5, pytata.Path("alert.wav"), True)
        self.audio.PlaybackDevice.return_value.__exit__.assert_called_once()

    @patch("pytata.select.select")
    def test_simple_notifications_play_in_full_without_reading_input(self, select):
        self.assertFalse(pytata.play_notification(3, pytata.Path("alert.wav")))
        self.assertEqual(self.played, self.decoded.samples.tobytes() * 3)
        select.assert_not_called()
        self.audio.PlaybackDevice.return_value.__exit__.assert_called_once()

    def test_invalid_sound_reports_error(self):
        for failure in (FileNotFoundError("alert.wav"), self.audio.MiniaudioError("Invalid sound")):
            with self.subTest(failure=failure), redirect_stderr(StringIO()) as stderr:
                self.audio.decode_file.side_effect = failure
                self.assertFalse(pytata.play_notification(5, pytata.Path("alert.wav")))
                self.assertIn("Unable to play notification", stderr.getvalue())
        self.audio.PlaybackDevice.assert_not_called()

    def test_start_failure_releases_device(self):
        self.device.start.side_effect = self.audio.MiniaudioError("No audio device")
        with redirect_stderr(StringIO()) as stderr:
            self.assertFalse(pytata.play_notification(5, pytata.Path("alert.wav")))
        self.assertIn("No audio device", stderr.getvalue())
        self.audio.PlaybackDevice.return_value.__exit__.assert_called_once()

    def test_empty_sound_does_not_open_device(self):
        self.decoded.num_frames = 0
        self.assertFalse(pytata.play_notification(5, pytata.Path("alert.wav")))
        self.audio.PlaybackDevice.assert_not_called()

    def test_missing_dependency_still_allows_enter_prompt(self):
        with patch.dict(sys.modules, {"miniaudio": None}), redirect_stderr(StringIO()) as stderr:
            self.assertFalse(pytata.play_notification(5, pytata.Path("alert.wav")))
        self.assertIn("install miniaudio", stderr.getvalue())


class PomodoroNotificationTests(unittest.TestCase):
    def test_countdown_input_cannot_dismiss_work_or_pause_prompt(self):
        args = argparse.Namespace(
            task="task-id", output=None, pomodori=2, work=0, pause=0,
            simple=False, mute=True, num_beeps=5, sound="alert.wav",
        )
        master, slave = pty.openpty()
        try:
            with os.fdopen(slave) as stdin, patch("pytata.sys.stdin", stdin), patch(
                "pytata.signal.signal"
            ), patch("pytata.run_command"), redirect_stdout(StringIO()):
                def countdown(*_):
                    os.write(master, b"\n")
                    self.assertTrue(select.select([stdin], [], [], 1)[0])

                def acknowledge(_):
                    self.assertFalse(select.select([stdin], [], [], 0)[0])
                    os.write(master, b"\n")
                    self.assertTrue(select.select([stdin], [], [], 1)[0])
                    self.assertEqual(stdin.readline(), "\n")

                with patch("pytata.run_interval", side_effect=countdown), patch(
                    "pytata.wait_for_enter_if_interactive", side_effect=acknowledge
                ) as wait:
                    self.assertEqual(pytata.pomodoro(args), 0)
                    self.assertEqual(wait.call_count, 3)
        finally:
            os.close(master)

    def test_acknowledgment_during_work_and_pause_does_not_wait_twice(self):
        args = argparse.Namespace(
            task="task-id", output=None, pomodori=2, work=0, pause=0,
            simple=False, mute=False, num_beeps=5, sound="alert.wav",
        )
        for acknowledged in (True, False):
            with self.subTest(acknowledged=acknowledged), patch(
                "pytata.signal.signal"
            ), patch("pytata.run_command"), patch(
                "pytata.play_notification", return_value=acknowledged
            ) as play, patch("pytata.wait_for_enter_if_interactive") as wait, redirect_stdout(
                StringIO()
            ):
                self.assertEqual(pytata.pomodoro(args), 0)
                self.assertEqual(play.call_count, 3)
                self.assertEqual(wait.call_count, 0 if acknowledged else 3)
                for call in play.call_args_list:
                    self.assertTrue(call.kwargs["interactive"])


class StartActionTests(unittest.TestCase):
    @patch("pytata.run_command")
    def test_action_without_value_omits_empty_tag(self, run_command):
        run_command.return_value.returncode = 0

        result = pytata.start_action("mail", ("dmenu", "-p", "{prompt}"), "")

        self.assertEqual(result, 0)
        self.assertEqual(run_command.call_args_list[0].args, ("notify-send", "Patata: mail"))
        tmux_args = run_command.call_args_list[1].args
        self.assertEqual(shlex.split(tmux_args[-1])[-2:], ["chrono", "mail"])

    @patch("pytata.run_command")
    def test_explicit_value_is_included_as_second_tag(self, run_command):
        run_command.return_value.returncode = 0

        result = pytata.start_action("mail", ("dmenu", "-p", "{prompt}"), "inbox")

        self.assertEqual(result, 0)
        self.assertEqual(
            run_command.call_args_list[0].args,
            ("notify-send", "Patata: mail inbox"),
        )
        tmux_args = run_command.call_args_list[1].args
        self.assertEqual(shlex.split(tmux_args[-1])[-3:], ["chrono", "mail", "inbox"])

    @patch("pytata.run_command")
    @patch("pytata.choose_item", return_value=None)
    def test_cancelled_menu_does_not_start_action(self, choose_item, run_command):
        result = pytata.start_action("read", ("dmenu", "-p", "{prompt}"))

        self.assertEqual(result, 0)
        choose_item.assert_called_once()
        run_command.assert_not_called()


class ActionMenuTests(unittest.TestCase):
    def setUp(self):
        self.actions = (
            pytata.ActionConfig(
                name="custom",
                prompt=False,
                aliases=("c",),
                description="Track a custom activity.",
            ),
        )
        self.menu_command = ("dmenu", "-p", "{prompt}")

    @patch("pytata.select_from_menu")
    def test_menu_lists_builtins_and_custom_actions_with_descriptions(self, select):
        select.return_value = (
            "start - Start a Pomodoro for the most urgent pending task."
        )

        result = pytata.choose_action(self.actions, self.menu_command)

        self.assertEqual(result, "start")
        choices = list(select.call_args.args[2])
        self.assertEqual(
            choices,
            [
                "start - Start a Pomodoro for the most urgent pending task.",
                "end - Stop the active pytata session.",
                "custom (c) - Track a custom activity.",
            ],
        )

    @patch("pytata.select_from_menu")
    def test_typed_alias_remains_valid(self, select):
        select.return_value = "c"

        result = pytata.choose_action(self.actions, self.menu_command)

        self.assertEqual(result, "c")

    @patch("pytata.select_from_menu")
    def test_rofi_searches_commands_but_displays_descriptions(self, select):
        select.return_value = "end"

        result = pytata.choose_action(
            self.actions,
            ("rofi", "-dmenu", "-i", "-p", "{prompt}"),
        )

        self.assertEqual(result, "end")
        choices = list(select.call_args.args[2])
        self.assertEqual(
            choices,
            [
                "start\0display\x1f"
                "start - Start a Pomodoro for the most urgent pending task.",
                "end\0display\x1fend - Stop the active pytata session.",
                "custom\0display\x1fcustom (c) - Track a custom activity."
                "\0meta\x1fc",
            ],
        )
        self.assertEqual(
            [choice.split("\0", 1)[0] for choice in choices],
            ["start", "end", "custom"],
        )


class HelpTests(unittest.TestCase):
    def setUp(self):
        self.config = pytata.PytataConfig(
            work=25,
            pause=5,
            pomodori=4,
            num_beeps=5,
            notification=pytata.Path("notification.wav"),
            menu_command=("dmenu", "-p", "{prompt}"),
            actions=(
                pytata.ActionConfig(
                    name="custom",
                    prompt=False,
                    aliases=("c",),
                    description="Track a custom activity.",
                ),
            ),
        )

    def test_global_help_lists_builtin_and_configured_actions(self):
        help_text = pytata.build_parser(self.config).format_help()

        self.assertLess(
            help_text.index("default actions:"),
            help_text.index("custom actions:"),
        )
        self.assertIn("start", help_text)
        self.assertIn("Start a Pomodoro for the most urgent pending task.", help_text)
        self.assertIn("custom (c)", help_text)
        self.assertIn("Track a custom activity.", help_text)
        self.assertIn("end", help_text)
        self.assertIn("chrono", help_text)

    @patch("pytata.load_config")
    def test_bare_help_uses_global_parser(self, load_config):
        load_config.return_value = self.config
        stdout = StringIO()

        with self.assertRaises(SystemExit) as raised, redirect_stdout(stdout):
            pytata.main(["-h"])

        self.assertEqual(raised.exception.code, 0)
        self.assertIn("Track a custom activity.", stdout.getvalue())

    @patch("pytata.load_config")
    @patch("pytata.pomodoro", return_value=0)
    def test_timer_options_route_to_start(self, pomodoro, load_config):
        load_config.return_value = self.config

        result = pytata.main(["--task", "task-id", "--mute"])

        self.assertEqual(result, 0)
        args = pomodoro.call_args.args[0]
        self.assertEqual(args.command, "start")
        self.assertEqual(args.task, "task-id")
        self.assertTrue(args.mute)

    def test_patata_is_no_longer_a_command(self):
        parser = pytata.build_parser(self.config)

        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            parser.parse_args(["patata"])


if __name__ == "__main__":
    unittest.main()
