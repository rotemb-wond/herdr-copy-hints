#!/usr/bin/env python3

import unittest

import hints


class CopyHintsTest(unittest.TestCase):
    def test_finds_supported_values_without_overlaps(self):
        lines = [
            "Visit https://herdr.dev/docs/configuration/ and c8b911e2c7e9a6cc",
            " M home/file with spaces.txt",
            "Connect to 192.168.0.1 but not 999.1.1.1",
            "Open home/.config/herdr/config.toml:42",
        ]

        matches = hints.find_matches(lines)
        values = [match.value for match in matches]

        self.assertIn("https://herdr.dev/docs/configuration/", values)
        self.assertIn("c8b911e2c7e9a6cc", values)
        self.assertIn("home/file with spaces.txt", values)
        self.assertIn("192.168.0.1", values)
        self.assertNotIn("999.1.1.1", values)
        self.assertIn("home/.config/herdr/config.toml:42", values)

    def test_hints_have_fixed_width_and_are_unique(self):
        short = hints.make_hints(len(hints.HINT_ALPHABET))
        long = hints.make_hints(len(hints.HINT_ALPHABET) + 1)

        self.assertEqual({len(hint) for hint in short}, {1})
        self.assertEqual({len(hint) for hint in long}, {2})
        self.assertEqual(len(long), len(set(long)))

    def test_repeated_values_each_receive_a_hint(self):
        matches = hints.find_matches(["open /tmp/file", "again /tmp/file"])

        self.assertEqual([match.value for match in matches], ["/tmp/file", "/tmp/file"])

    def test_strips_ansi_without_changing_cell_positions(self):
        line = "\x1b[1mhello\x1b[0m \x1b[38;5;2m世界\x1b[0m"

        plain = hints.strip_ansi(line)

        self.assertEqual(plain, "hello 世界")
        self.assertEqual(hints.display_width(plain), 10)

    def test_snapshot_is_cropped_without_a_scrolling_newline(self):
        snapshot, lines = hints.fit_snapshot("one\r\ntwo\r\nthree\r\n", 2)

        self.assertEqual(snapshot, "two\r\nthree")
        self.assertEqual(lines, ["two", "three"])

    def test_remote_sessions_use_terminal_clipboard(self):
        command = hints.clipboard_command(
            platform="linux",
            environ={"SSH_CONNECTION": "host"},
        )

        self.assertIsNone(command)


if __name__ == "__main__":
    unittest.main()
