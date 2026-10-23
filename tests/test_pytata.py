import argparse
import os
import shlex
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

import pytata


class NotificationTests(unittest.TestCase):
    def test_enter_stops_active_player_and_remaining_beeps(self):
        read_fd, write_fd = os.pipe()
        with os.fdopen(write_fd, "w") as writer:
            writer.write("\n")
        player = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with os.fdopen(read_fd) as stdin, patch("pytata.sys.stdin", stdin), patch(
                "pytata.subprocess.Popen", return_value=player
            ) as launch:
                acknowledged = pytata.play_notification(5, pytata.Path("alert.wav"), True)

            self.assertTrue(acknowledged)
            launch.assert_called_once_with(
                ["aplay", "-q", "alert.wav"], stdin=subprocess.DEVNULL
            )
            self.assertIsNotNone(player.poll())
        finally:
            if player.poll() is None:
                player.kill()
            player.wait()

    @patch("pytata.select.select", return_value=([], [], []))
    @patch("pytata.subprocess.Popen")
    def test_completed_alert_still_needs_acknowledgment(self, launch, select):
        launch.return_value.poll.side_effect = [None, 0, 0] * 2

        self.assertFalse(pytata.play_notification(2, pytata.Path("alert.wav"), True))

        self.assertEqual(launch.call_count, 2)
        launch.return_value.terminate.assert_not_called()

    @patch("pytata.sys.stdin", new_callable=StringIO)
    @patch("pytata.select.select")
    @patch("pytata.subprocess.Popen")
    def test_eof_does_not_skip_remaining_beeps(self, launch, select, stdin):
        select.return_value = ([stdin], [], [])
        launch.return_value.poll.side_effect = [None, None, 0, None, 0]

        self.assertFalse(pytata.play_notification(2, pytata.Path("alert.wav"), True))

        self.assertEqual(launch.call_count, 2)
        select.assert_called_once()
        self.assertEqual(launch.return_value.wait.call_count, 2)

    @patch("pytata.subprocess.Popen", side_effect=FileNotFoundError)
    def test_missing_player_still_needs_acknowledgment(self, launch):
        self.assertFalse(pytata.play_notification(5, pytata.Path("alert.wav"), True))

    @patch("pytata.select.select", side_effect=KeyboardInterrupt)
    @patch("pytata.subprocess.Popen")
    def test_interrupt_cleans_up_player(self, launch, select):
        launch.return_value.poll.return_value = None

        with self.assertRaises(KeyboardInterrupt):
            pytata.play_notification(5, pytata.Path("alert.wav"), True)

        launch.return_value.terminate.assert_called_once()
        launch.return_value.wait.assert_called_once_with(timeout=1)

    @patch("pytata.run_command")
    @patch("pytata.select.select")
    def test_simple_notifications_play_in_full_without_reading_input(self, select, run):
        self.assertFalse(pytata.play_notification(3, pytata.Path("alert.wav")))

        self.assertEqual(run.call_count, 3)
        select.assert_not_called()


class PomodoroNotificationTests(unittest.TestCase):
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
