#!/usr/bin/env python3

import base64
import contextlib
import fcntl
import io
import json
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path
from unittest import mock

import hints


ROOT = Path(__file__).resolve().parent


class CopyHintsTest(unittest.TestCase):
    def test_finds_supported_values_without_overlaps(self):
        lines = [
            "Visit https://herdr.dev/docs/configuration/ and c8b911e2c7e9a6cc",
            " M home/file with spaces.txt",
            "Connect to 192.168.0.1 but not 999.1.1.1",
            "Open home/.config/herdr/config.toml:42",
            "On branch feature/copy-hints",
        ]

        matches = hints.find_matches(lines)
        values = [match.value for match in matches]

        self.assertIn("https://herdr.dev/docs/configuration/", values)
        self.assertIn("c8b911e2c7e9a6cc", values)
        self.assertIn("home/file with spaces.txt", values)
        self.assertIn("192.168.0.1", values)
        self.assertNotIn("999.1.1.1", values)
        self.assertIn("home/.config/herdr/config.toml:42", values)
        self.assertIn("feature/copy-hints", values)

    def test_hints_have_fixed_width_and_are_unique(self):
        alphabet = hints.DEFAULT_HINT_ALPHABET
        short = hints.make_hints(len(alphabet))
        long = hints.make_hints(len(alphabet) + 1)

        self.assertEqual({len(hint) for hint in short}, {1})
        self.assertEqual({len(hint) for hint in long}, {2})
        self.assertEqual(len(long), len(set(long)))

    def test_repeated_values_each_receive_a_hint(self):
        matches = hints.find_matches(["open /tmp/file", "again /tmp/file"])

        self.assertEqual([match.value for match in matches], ["/tmp/file", "/tmp/file"])

    def test_strips_ansi_and_tracks_unicode_cell_width(self):
        line = "\x1b[1mhello\x1b[0m \x1b[38;5;2m世界\x1b[0m e\u0301"

        plain = hints.strip_ansi(line)

        self.assertEqual(plain, "hello 世界 e\u0301")
        self.assertEqual(hints.display_width(plain), 12)

    def test_snapshot_is_cropped_without_a_scrolling_newline(self):
        snapshot, lines = hints.fit_snapshot("one\r\ntwo\r\nthree\r\n", 2)

        self.assertEqual(snapshot, "two\r\nthree")
        self.assertEqual(lines, ["two", "three"])

    def test_disabled_patterns_are_not_matched(self):
        config = hints.parse_config({"enabled_patterns": ["url"]})

        values = [
            match.value
            for match in hints.find_matches(
                ["https://herdr.dev /tmp/file c8b911e2c7e9a6cc"],
                config,
            )
        ]

        self.assertEqual(values, ["https://herdr.dev"])

    def test_all_builtin_patterns_can_be_disabled(self):
        config = hints.parse_config(
            {
                "enabled_patterns": [],
                "custom_patterns": {"ticket": r"TICKET-[0-9]+"},
            }
        )

        matches = hints.find_matches(["TICKET-4821 /tmp/file"], config)

        self.assertEqual(
            [(match.kind, match.value) for match in matches],
            [("ticket", "TICKET-4821")],
        )

    def test_custom_pattern_can_copy_named_subgroup(self):
        config = hints.parse_config(
            {"custom_patterns": {"ticket": r"TICKET-(?P<match>[0-9]+)"}}
        )

        match = next(
            match
            for match in hints.find_matches(["Fix TICKET-4821"], config)
            if match.kind == "ticket"
        )

        self.assertEqual(match.value, "4821")

    def test_loads_config_from_plugin_config_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "config.json").write_text(
                json.dumps({"hint_alphabet": "hjkl"}),
                encoding="utf-8",
            )

            config = hints.load_config(directory)

        self.assertEqual(config.hint_alphabet, "hjkl")

    def test_rejects_invalid_configuration(self):
        invalid_configs = [
            {"unknown": True},
            {"hint_alphabet": "aa"},
            {"enabled_patterns": ["not-real"]},
            {"custom_patterns": {"ticket": "["}},
            {"hint_style": "\x1b[31m"},
            {"hint_style": "31;;1"},
            {"clipboard_command": "pbcopy"},
        ]

        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaises(hints.ConfigError):
                    hints.parse_config(config)

    def test_custom_clipboard_command_wins(self):
        config = hints.parse_config({"clipboard_command": ["custom-copy", "--stdin"]})

        command = hints.clipboard_command(
            config,
            platform="linux",
            environ={"SSH_CONNECTION": "host"},
            which=lambda _name: None,
        )

        self.assertEqual(command, ["custom-copy", "--stdin"])

    def test_remote_sessions_use_terminal_clipboard(self):
        command = hints.clipboard_command(
            platform="linux",
            environ={"SSH_CONNECTION": "host"},
            which=lambda _name: "/usr/bin/wl-copy",
        )

        self.assertIsNone(command)

    def test_terminal_clipboard_uses_osc52(self):
        config = hints.PluginConfig()
        output = io.StringIO()

        with (
            contextlib.redirect_stdout(output),
            mock.patch.object(hints, "clipboard_command", return_value=None),
        ):
            hints.copy_to_clipboard("hello 世界", config)

        encoded = base64.b64encode("hello 世界".encode()).decode()
        self.assertEqual(output.getvalue(), f"\x1b]52;c;{encoded}\a")


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary_directory.name)
        self.clipboard = self.temp / "clipboard"
        self.fake_herdr = self.temp / "herdr"
        self.fake_herdr.write_text(
            "#!/bin/sh\n"
            "if [ \"$1 $2\" = \"pane read\" ]; then\n"
            "  printf 'Commit c8b911e2c7e9a6cc\\nOpen /tmp/example.txt\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 2\n",
            encoding="utf-8",
        )
        self.fake_herdr.chmod(0o755)
        self.fake_copy = self.temp / "copy"
        self.fake_copy.write_text(
            "#!/bin/sh\ncat > \"$COPY_HINTS_TEST_CLIPBOARD\"\n",
            encoding="utf-8",
        )
        self.fake_copy.chmod(0o755)
        Path(self.temp, "config.json").write_text(
            json.dumps({"clipboard_command": [str(self.fake_copy)]}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def spawn_plugin(self):
        environment = os.environ.copy()
        environment.update(
            {
                "HERDR_BIN_PATH": str(self.fake_herdr),
                "HERDR_HINT_TARGET_PANE_ID": "pane-1",
                "HERDR_PLUGIN_CONFIG_DIR": str(self.temp),
                "COPY_HINTS_TEST_CLIPBOARD": str(self.clipboard),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 12, 80, 0, 0))
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "hints.py")],
            cwd=ROOT,
            env=environment,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
        )
        os.close(slave)
        return process, master

    def read_until(self, master, needle, timeout=3):
        output = bytearray()
        deadline = time.monotonic() + timeout
        while needle not in output and time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
        self.assertIn(needle, output)
        return bytes(output)

    def wait_for_cbreak(self, master, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not termios.tcgetattr(master)[3] & termios.ICANON:
                return
            time.sleep(0.01)
        self.fail("plugin did not enter cbreak input mode")

    def wait_for_exit(self, process, master, timeout=3):
        deadline = time.monotonic() + timeout
        while process.poll() is None and time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.05)
            if readable:
                try:
                    os.read(master, 65536)
                except OSError:
                    pass
        if process.poll() is None:
            self.fail("plugin did not exit")
        return process.returncode

    def test_selects_a_hint_and_copies_the_full_value(self):
        process, master = self.spawn_plugin()
        try:
            self.read_until(master, b"\x1b[30;103;1ma")
            self.wait_for_cbreak(master)
            os.write(master, b"a")
            self.assertEqual(self.wait_for_exit(process, master), 0)
            self.assertEqual(self.clipboard.read_text(), "c8b911e2c7e9a6cc")
        finally:
            os.close(master)
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_rebuilds_hints_after_terminal_resize(self):
        process, master = self.spawn_plugin()
        try:
            first = self.read_until(master, b"\x1b[30;103;1ma")
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 8, 50, 0, 0))
            os.kill(process.pid, signal.SIGWINCH)
            second = self.read_until(master, b"\x1b[30;103;1ma")
            self.assertIn(b"Commit c8b911e2c7e9a6cc", first)
            self.assertIn(b"Commit c8b911e2c7e9a6cc", second)
            os.write(master, b"\x1b")
            self.assertEqual(self.wait_for_exit(process, master), 0)
        finally:
            os.close(master)
            if process.poll() is None:
                process.kill()
                process.wait()


if __name__ == "__main__":
    unittest.main()
