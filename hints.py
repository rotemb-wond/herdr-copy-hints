#!/usr/bin/env python3

from __future__ import annotations

import base64
import itertools
import os
import re
import shutil
import subprocess
import sys
import termios
import tty
import unicodedata
from dataclasses import dataclass


ESC = "\x1b"
HINT_ALPHABET = "asdfghjklwertyuiopzxcvbnm"
ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07]*(?:\x07|\x1b\\)"
    r")"
)

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


@dataclass(frozen=True)
class Match:
    row: int
    start: int
    end: int
    value: str
    kind: str
    priority: int


def strip_ansi(value: str) -> str:
    return ANSI_RE.sub("", value)


def display_width(value: str) -> int:
    width = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def clean_match(value: str, kind: str) -> tuple[str, int]:
    if kind == "url":
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


def contextual_matches(row: int, line: str) -> list[Match]:
    patterns = [
        (
            re.compile(
                r"^\s*(?:modified:|new file:|deleted:|renamed:|copied:|"
                r"typechange:|untracked:)\s+(.+?)\s*$"
            ),
            "path",
        ),
        (
            re.compile(
                r"^(?:[MADRCU?!]{1,2}|[MADRCU?!] | [MADRCU?!])"
                r"\s+(.+?)\s*$"
            ),
            "path",
        ),
        (re.compile(r"^(?:\+\+\+|---)\s+(?:[ab]/)?(.+?)\s*$"), "path"),
        (re.compile(r"^\s*On branch\s+(\S+)\s*$"), "branch"),
    ]

    matches = []
    for pattern, kind in patterns:
        found = pattern.match(line)
        if not found:
            continue
        value = found.group(1)
        if value == "/dev/null":
            continue
        matches.append(
            Match(
                row=row,
                start=found.start(1),
                end=found.end(1),
                value=value,
                kind=kind,
                priority=100,
            )
        )
    return matches


def find_matches(lines: list[str]) -> list[Match]:
    candidates = []
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

    for row, line in enumerate(lines, start=1):
        candidates.extend(contextual_matches(row, line))
        for pattern, kind, priority in regexes:
            candidates.extend(regex_matches(row, line, pattern, kind, priority))

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


def make_hints(count: int) -> list[str]:
    if count <= 0:
        return []

    width = 1
    capacity = len(HINT_ALPHABET)
    while capacity < count:
        width += 1
        capacity *= len(HINT_ALPHABET)

    return [
        "".join(chars)
        for chars in itertools.islice(
            itertools.product(HINT_ALPHABET, repeat=width),
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


def render_snapshot(snapshot: str) -> None:
    sys.stdout.write(f"{ESC}[?25l{ESC}[?7l{ESC}[2J{ESC}[H")
    sys.stdout.write(snapshot)
    sys.stdout.flush()


def draw_hints(
    matches: list[Match],
    hints: list[str],
    lines: list[str],
    prefix: str = "",
) -> None:
    output = []
    for match, hint in zip(matches, hints):
        row, column = cursor_position(match, lines)
        if prefix and not hint.startswith(prefix):
            style = f"{ESC}[90;100;2m"
        elif prefix:
            style = f"{ESC}[30;102;1m"
        else:
            style = f"{ESC}[30;103;1m"
        output.append(f"{ESC}[{row};{column}H{style}{hint}{ESC}[0m")
    output.append(f"{ESC}[?25l")
    sys.stdout.write("".join(output))
    sys.stdout.flush()


def read_selection(hints: list[str], redraw) -> int | None:
    input_fd = sys.stdin.fileno()
    previous = termios.tcgetattr(input_fd)
    prefix = ""

    try:
        tty.setcbreak(input_fd)
        while True:
            raw = os.read(input_fd, 1)
            if not raw:
                return None
            if raw in {b"\x03", b"\x1b"}:
                return None

            char = raw.decode("ascii", errors="ignore").lower()
            if char not in HINT_ALPHABET:
                prefix = ""
                sys.stdout.write("\a")
                redraw(prefix)
                continue

            prefix += char
            possible = [index for index, hint in enumerate(hints) if hint.startswith(prefix)]
            if not possible:
                prefix = ""
                sys.stdout.write("\a")
                redraw(prefix)
                continue

            redraw(prefix)
            exact = [index for index in possible if hints[index] == prefix]
            if exact:
                return exact[0]
    finally:
        termios.tcsetattr(input_fd, termios.TCSADRAIN, previous)


def clipboard_command(
    platform: str = sys.platform,
    environ: dict[str, str] | None = None,
) -> list[str] | None:
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
        if shutil.which(executable):
            return command
    return None


def copy_to_clipboard(value: str) -> None:
    command = clipboard_command()
    if command:
        subprocess.run(command, input=value.encode(), check=True)
        return

    # OSC 52 copies through the attached terminal, which also keeps remote
    # Herdr sessions copying to the local machine instead of the server.
    encoded = base64.b64encode(value.encode()).decode()
    sys.stdout.write(f"{ESC}]52;c;{encoded}\a")
    sys.stdout.flush()


def main() -> int:
    pane_id = os.environ.get("HERDR_HINT_TARGET_PANE_ID")
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
    if not pane_id:
        print("copy-hints: target pane was not provided", file=sys.stderr)
        return 1

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
        sys.stderr.buffer.write(result.stderr)
        return result.returncode

    terminal_size = os.get_terminal_size(sys.stdout.fileno())
    snapshot, lines = fit_snapshot(
        result.stdout.decode("utf-8", errors="replace"),
        terminal_size.lines,
    )
    matches = find_matches(lines)
    hints = make_hints(len(matches))

    if not matches:
        return 0

    visible = [
        (match, hint)
        for match, hint in zip(matches, hints)
        if cursor_position(match, lines)[1] + len(hint) - 1
        <= terminal_size.columns
    ]
    matches = [match for match, _ in visible]
    hints = [hint for _, hint in visible]
    if not matches:
        return 0

    try:
        render_snapshot(snapshot)
        draw_hints(matches, hints, lines)
        selected = read_selection(
            hints,
            lambda prefix: draw_hints(matches, hints, lines, prefix),
        )
        if selected is not None:
            copy_to_clipboard(matches[selected].value)
    finally:
        sys.stdout.write(f"{ESC}[0m{ESC}[?7h{ESC}[?25h")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
