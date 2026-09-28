import shlex
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

import pytata


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
