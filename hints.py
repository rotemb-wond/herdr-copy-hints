#!/usr/bin/env python3

from __future__ import annotations

import base64
import itertools
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
import tty
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ESC = "\x1b"
DEFAULT_HINT_ALPHABET = "asdfghjklwertyuiopzxcvbnm"
DEFAULT_PATTERNS = frozenset(
    {"url", "git", "uuid", "ip", "hex", "sha", "path", "branch", "number"}
)
ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07]*(?:\x07|\x1b\\)"
    r")"
)
SGR_RE = re.compile(r"^[0-9]{1,3}(?:;[0-9]{1,3})*$")

URL_RE = re.compile(r"\b(?:https?|ssh|git|file)://[^\s<>\"']+")
GIT_REMOTE_RE = re.compile(r"\bgit@[A-Za-z0-9.-]+:[A-Za-z0-9._~/-]+")
UUID_RE = re.compile(
    r"(?<![\w])"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
    r"(?![\w])"
)
IP_RE = re.compile(r"(?<![\w])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w])")
SHA_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{7,40}(?![0-9a-fA-F])")
HEX_RE = re.compile(r"(?<![\w])0x[0-9a-fA-F]+(?![\w])")
PATH_RE = re.compile(
    r"(?<![\w.-])"
    r"(?:~|\.\.?|[\w.@+-]+)?(?:/[\w.@+-]+)+"
    r"(?::[0-9]+(?::[0-9]+)?)?"
    r"(?![\w])"
)
FILENAME_RE = re.compile(
    r"(?<![\w./-])[\w@+-][\w.@+-]*\.[A-Za-z][\w.-]*"
    r"(?::[0-9]+(?::[0-9]+)?)?"
    r"(?![\w])"
)
NUMBER_RE = re.compile(r"(?<![\w])[0-9]{4,}(?![\w])")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class CustomPattern:
    name: str
    regex: re.Pattern[str]


@dataclass(frozen=True)
class PluginConfig:
    enabled_patterns: frozenset[str] = DEFAULT_PATTERNS
    custom_patterns: tuple[CustomPattern, ...] = ()
    hint_alphabet: str = DEFAULT_HINT_ALPHABET
    hint_style: str = "30;103;1"
    matching_hint_style: str = "30;102;1"
    dimmed_hint_style: str = "90;100;2"
    clipboard_command: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Match:
    row: int
    start: int
    end: int
    value: str
    kind: str
    priority: int


@dataclass(frozen=True)
class Screen:
    snapshot: str
    lines: list[str]
    matches: list[Match]
    hints: list[str]


def _string_list(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        requirement = "an array" if allow_empty else "a non-empty array"
        raise ConfigError(f"{field} must be {requirement} of strings")
    if not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"{field} must contain only non-empty strings")
    return tuple(value)


def _style(value: object, field: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not SGR_RE.fullmatch(value):
        raise ConfigError(f"{field} must contain only ANSI SGR numbers and semicolons")
    return value


def parse_config(data: object) -> PluginConfig:
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")

    known = {
        "enabled_patterns",
        "custom_patterns",
        "hint_alphabet",
        "hint_style",
        "matching_hint_style",
        "dimmed_hint_style",
        "clipboard_command",
    }
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"unknown config field: {unknown[0]}")

    enabled = DEFAULT_PATTERNS
    if "enabled_patterns" in data:
        enabled_values = _string_list(
            data["enabled_patterns"],
            "enabled_patterns",
            allow_empty=True,
        )
        invalid = sorted(set(enabled_values) - DEFAULT_PATTERNS)
        if invalid:
            raise ConfigError(f"unknown enabled pattern: {invalid[0]}")
        enabled = frozenset(enabled_values)

    alphabet = data.get("hint_alphabet", DEFAULT_HINT_ALPHABET)
    if not isinstance(alphabet, str):
        raise ConfigError("hint_alphabet must be a string")
    alphabet = alphabet.lower()
    if len(alphabet) < 2:
        raise ConfigError("hint_alphabet must contain at least two characters")
    if len(set(alphabet)) != len(alphabet):
        raise ConfigError("hint_alphabet characters must be unique")
    if not alphabet.isascii() or not alphabet.isalpha():
        raise ConfigError("hint_alphabet must contain only ASCII letters")

    custom_patterns = []
    raw_custom = data.get("custom_patterns", {})
    if not isinstance(raw_custom, dict):
        raise ConfigError("custom_patterns must be an object of name-to-regex entries")
    if len(raw_custom) > 50:
        raise ConfigError("custom_patterns may contain at most 50 entries")
    for name, pattern in raw_custom.items():
        if not isinstance(name, str) or not name:
            raise ConfigError("custom pattern names must be non-empty strings")
        if not isinstance(pattern, str) or not pattern:
            raise ConfigError(f"custom pattern {name!r} must be a non-empty string")
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise ConfigError(f"invalid custom pattern {name!r}: {error}") from error
        custom_patterns.append(CustomPattern(name=name, regex=compiled))

    clipboard = None
    if "clipboard_command" in data and data["clipboard_command"] is not None:
        clipboard = _string_list(data["clipboard_command"], "clipboard_command")

    return PluginConfig(
        enabled_patterns=enabled,
        custom_patterns=tuple(custom_patterns),
        hint_alphabet=alphabet,
        hint_style=_style(data.get("hint_style"), "hint_style", "30;103;1"),
        matching_hint_style=_style(
            data.get("matching_hint_style"),
            "matching_hint_style",
            "30;102;1",
        ),
        dimmed_hint_style=_style(
            data.get("dimmed_hint_style"),
            "dimmed_hint_style",
            "90;100;2",
        ),
        clipboard_command=clipboard,
    )


def load_config(config_dir: str | None = None) -> PluginConfig:
    directory = config_dir or os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if not directory:
        return PluginConfig()

    path = Path(directory) / "config.json"
    try:
        if not path.exists():
            return PluginConfig()
        if path.stat().st_size > 1_000_000:
            raise ConfigError("config.json exceeds the 1 MB size limit")
        data = json.loads(path.read_text(encoding="utf-8"))
    except ConfigError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read config.json: {error}") from error
    return parse_config(data)


def strip_ansi(value: str) -> str:
    return ANSI_RE.sub("", value)


def display_width(value: str) -> int:
    width = 0
    for char in value:
        if unicodedata.combining(char) or char in {"\u200d", "\ufe0e", "\ufe0f"}:
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def clean_match(value: str, kind: str) -> tuple[str, int]:
    if kind in {"url", "git"}:
        cleaned = value.rstrip("),.;:")
        return cleaned, len(value) - len(cleaned)
    return value, 0


def regex_matches(
    row: int,
    line: str,
    pattern: re.Pattern[str],
    kind: str,
    priority: int,
) -> list[Match]:
    matches = []
    for found in pattern.finditer(line):
        value, removed = clean_match(found.group(0), kind)
        if value:
            matches.append(
                Match(
                    row=row,
                    start=found.start(),
                    end=found.end() - removed,
                    value=value,
                    kind=kind,
                    priority=priority,
                )
            )
    return matches


def custom_regex_matches(
    row: int,
    line: str,
    custom: CustomPattern,
) -> list[Match]:
    matches = []
    for found in custom.regex.finditer(line):
        group = "match" if "match" in custom.regex.groupindex else 0
        value = found.group(group)
        if value is None or value == "":
            continue
        matches.append(
            Match(
                row=row,
                start=found.start(group),
                end=found.end(group),
                value=value,
                kind=custom.name,
                priority=95,
            )
        )
    return matches


def contextual_matches(row: int, line: str, enabled: frozenset[str]) -> list[Match]:
    patterns = []
    if "path" in enabled:
        patterns.extend(
            [
                re.compile(
                    r"^\s*(?:modified:|new file:|deleted:|renamed:|copied:|"
                    r"typechange:|untracked:)\s+(.+?)\s*$"
                ),
                re.compile(
                    r"^(?:[MADRCU?!]{1,2}|[MADRCU?!] | [MADRCU?!])"
                    r"\s+(.+?)\s*$"
                ),
                re.compile(r"^(?:\+\+\+|---)\s+(?:[ab]/)?(.+?)\s*$"),
            ]
        )

    matches = []
    for pattern in patterns:
        found = pattern.match(line)
        if found and found.group(1) != "/dev/null":
            matches.append(
                Match(
                    row=row,
                    start=found.start(1),
                    end=found.end(1),
                    value=found.group(1),
                    kind="path",
                    priority=100,
                )
            )

    if "branch" in enabled:
        found = re.match(r"^\s*On branch\s+(\S+)\s*$", line)
        if found:
            matches.append(
                Match(
                    row=row,
                    start=found.start(1),
                    end=found.end(1),
                    value=found.group(1),
                    kind="branch",
                    priority=100,
                )
            )
    return matches


def find_matches(
    lines: list[str],
    config: PluginConfig | None = None,
) -> list[Match]:
    config = config or PluginConfig()
    regexes = [
        (URL_RE, "url", 90),
        (GIT_REMOTE_RE, "git", 90),
        (UUID_RE, "uuid", 80),
        (IP_RE, "ip", 80),
        (HEX_RE, "hex", 75),
        (SHA_RE, "sha", 70),
        (PATH_RE, "path", 60),
        (FILENAME_RE, "path", 55),
        (NUMBER_RE, "number", 40),
    ]

    candidates = []
    for row, line in enumerate(lines, start=1):
        candidates.extend(contextual_matches(row, line, config.enabled_patterns))
        for pattern, kind, priority in regexes:
            if kind in config.enabled_patterns:
                candidates.extend(regex_matches(row, line, pattern, kind, priority))
        for custom in config.custom_patterns:
            candidates.extend(custom_regex_matches(row, line, custom))

    candidates = [
        match
        for match in candidates
        if match.kind != "ip"
        or all(int(part) <= 255 for part in match.value.split("."))
    ]

    accepted: list[Match] = []
    for candidate in sorted(
        candidates,
        key=lambda match: (
            match.row,
            match.start,
            -match.priority,
            -(match.end - match.start),
        ),
    ):
        if any(
            existing.row == candidate.row
            and candidate.start < existing.end
            and existing.start < candidate.end
            for existing in accepted
        ):
            continue
        accepted.append(candidate)

    return sorted(accepted, key=lambda match: (match.row, match.start))


def make_hints(count: int, alphabet: str = DEFAULT_HINT_ALPHABET) -> list[str]:
    if count <= 0:
        return []

    width = 1
    capacity = len(alphabet)
    while capacity < count:
        width += 1
        capacity *= len(alphabet)

    return [
        "".join(chars)
        for chars in itertools.islice(
            itertools.product(alphabet, repeat=width),
            count,
        )
    ]


def cursor_position(match: Match, lines: list[str]) -> tuple[int, int]:
    return match.row, display_width(lines[match.row - 1][: match.start]) + 1


def fit_snapshot(snapshot: str, rows: int) -> tuple[str, list[str]]:
    ansi_lines = snapshot.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if ansi_lines and ansi_lines[-1] == "":
        ansi_lines.pop()
    if len(ansi_lines) > rows:
        ansi_lines = ansi_lines[-rows:]

    fitted = "\r\n".join(ansi_lines)
    lines = [strip_ansi(line) for line in ansi_lines]
    return fitted, lines


def read_pane(herdr: str, pane_id: str) -> str:
    result = subprocess.run(
        [
            herdr,
            "pane",
            "read",
            pane_id,
            "--source",
            "visible",
            "--format",
            "ansi",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(detail or "Herdr could not read the active pane")
    return result.stdout.decode("utf-8", errors="replace")


def build_screen(herdr: str, pane_id: str, config: PluginConfig) -> Screen:
    terminal_size = os.get_terminal_size(sys.stdout.fileno())
    snapshot, lines = fit_snapshot(
        read_pane(herdr, pane_id),
        terminal_size.lines,
    )
    matches = find_matches(lines, config)
    hints = make_hints(len(matches), config.hint_alphabet)
    visible = [
        (match, hint)
        for match, hint in zip(matches, hints)
        if cursor_position(match, lines)[1] + len(hint) - 1 <= terminal_size.columns
    ]
    return Screen(
        snapshot=snapshot,
        lines=lines,
        matches=[match for match, _ in visible],
        hints=[hint for _, hint in visible],
    )


def render_snapshot(snapshot: str) -> None:
    sys.stdout.write(f"{ESC}[?25l{ESC}[?7l{ESC}[2J{ESC}[H")
    sys.stdout.write(snapshot)
    sys.stdout.flush()


def draw_hints(screen: Screen, config: PluginConfig, prefix: str = "") -> None:
    output = []
    for match, hint in zip(screen.matches, screen.hints):
        row, column = cursor_position(match, screen.lines)
        if prefix and not hint.startswith(prefix):
            style = config.dimmed_hint_style
        elif prefix:
            style = config.matching_hint_style
        else:
            style = config.hint_style
        output.append(f"{ESC}[{row};{column}H{ESC}[{style}m{hint}{ESC}[0m")
    output.append(f"{ESC}[?25l")
    sys.stdout.write("".join(output))
    sys.stdout.flush()


def render_screen(screen: Screen, config: PluginConfig, prefix: str = "") -> None:
    render_snapshot(screen.snapshot)
    draw_hints(screen, config, prefix)


def show_message(message: str, *, error: bool = False, delay: float = 1.2) -> None:
    size = shutil.get_terminal_size((80, 24))
    clean = " ".join(message.split())
    clean = clean[: max(1, size.columns - 4)]
    row = max(1, size.lines)
    style = "97;41;1" if error else "30;103;1"
    padding = max(0, size.columns - display_width(clean) - 2)
    sys.stdout.write(
        f"{ESC}[{row};1H{ESC}[{style}m {clean}{' ' * padding} {ESC}[0m"
    )
    sys.stdout.flush()
    time.sleep(delay)


def read_selection(
    herdr: str,
    pane_id: str,
    config: PluginConfig,
    initial_screen: Screen,
) -> Match | None:
    input_fd = sys.stdin.fileno()
    previous = termios.tcgetattr(input_fd)
    screen = initial_screen
    prefix = ""
    resize_pending = False

    def on_resize(_signum, _frame) -> None:
        nonlocal resize_pending
        resize_pending = True

    previous_handler = signal.getsignal(signal.SIGWINCH)
    signal.signal(signal.SIGWINCH, on_resize)

    try:
        tty.setcbreak(input_fd)
        while True:
            if resize_pending:
                resize_pending = False
                screen = build_screen(herdr, pane_id, config)
                prefix = ""
                if not screen.matches:
                    render_screen(screen, config)
                    show_message("No copyable values found after resizing")
                    return None
                render_screen(screen, config)

            readable, _, _ = select.select([input_fd], [], [], 0.1)
            if not readable:
                continue

            raw = os.read(input_fd, 1)
            if not raw or raw in {b"\x03", b"\x1b"}:
                return None

            char = raw.decode("ascii", errors="ignore").lower()
            if char not in config.hint_alphabet:
                prefix = ""
                sys.stdout.write("\a")
                draw_hints(screen, config)
                continue

            prefix += char
            possible = [
                index
                for index, hint in enumerate(screen.hints)
                if hint.startswith(prefix)
            ]
            if not possible:
                prefix = ""
                sys.stdout.write("\a")
                draw_hints(screen, config)
                continue

            draw_hints(screen, config, prefix)
            exact = [index for index in possible if screen.hints[index] == prefix]
            if exact:
                return screen.matches[exact[0]]
    finally:
        signal.signal(signal.SIGWINCH, previous_handler)
        termios.tcsetattr(input_fd, termios.TCSADRAIN, previous)


def clipboard_command(
    config: PluginConfig | None = None,
    platform: str = sys.platform,
    environ: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str] | None:
    config = config or PluginConfig()
    if config.clipboard_command:
        return list(config.clipboard_command)

    environ = os.environ if environ is None else environ
    if environ.get("SSH_CONNECTION") or environ.get("SSH_TTY"):
        return None

    candidates = []
    if platform == "darwin":
        candidates = [(["pbcopy"], "pbcopy")]
    elif platform.startswith("linux"):
        candidates = [
            (["wl-copy"], "wl-copy"),
            (["xclip", "-selection", "clipboard"], "xclip"),
            (["xsel", "--clipboard", "--input"], "xsel"),
        ]

    for command, executable in candidates:
        if which(executable):
            return command
    return None


def copy_to_clipboard(value: str, config: PluginConfig) -> None:
    command = clipboard_command(config)
    if command:
        try:
            subprocess.run(
                command,
                input=value.encode(),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError(f"clipboard command failed: {error}") from error
        return

    encoded = base64.b64encode(value.encode()).decode()
    sys.stdout.write(f"{ESC}]52;c;{encoded}\a")
    sys.stdout.flush()


def main() -> int:
    pane_id = os.environ.get("HERDR_HINT_TARGET_PANE_ID")
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")

    try:
        config = load_config()
    except ConfigError as error:
        render_snapshot("")
        show_message(f"Configuration error: {error}", error=True, delay=2)
        return 1

    if not pane_id:
        render_snapshot("")
        show_message("Copy Hints could not identify the active pane", error=True)
        return 1

    try:
        screen = build_screen(herdr, pane_id, config)
    except (OSError, RuntimeError) as error:
        render_snapshot("")
        show_message(str(error), error=True, delay=2)
        return 1

    try:
        render_screen(screen, config)
        if not screen.matches:
            show_message("No copyable values found")
            return 0

        try:
            selected = read_selection(herdr, pane_id, config, screen)
        except (OSError, RuntimeError) as error:
            show_message(str(error), error=True, delay=2)
            return 1
        if selected is not None:
            try:
                copy_to_clipboard(selected.value, config)
            except RuntimeError as error:
                show_message(str(error), error=True, delay=2)
                return 1
    finally:
        sys.stdout.write(f"{ESC}[0m{ESC}[?7h{ESC}[?25h")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
