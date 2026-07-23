# Herdr Copy Hints

[![test](https://github.com/rotemb-wond/herdr-copy-hints/actions/workflows/test.yml/badge.svg)](https://github.com/rotemb-wond/herdr-copy-hints/actions/workflows/test.yml)

Keyboard-driven copy hints for [Herdr](https://herdr.dev), inspired by
[tmux-fingers](https://github.com/Morantron/tmux-fingers).

Press one shortcut to place compact letter hints directly over paths, Git
commits, URLs, and other useful values in the active pane. Type a hint to copy
the full value immediately.

## Install

Install from GitHub:

```sh
herdr plugin install rotemb-wond/herdr-copy-hints
```

Add a keybinding to `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "rotemb-wond.copy-hints.open"
description = "show copy hints over the active pane"
```

Reload the configuration:

```sh
herdr server reload-config
```

Requires Herdr 0.7.0 or newer, Python 3.10 or newer, and macOS or Linux.

## Usage

1. Press the configured shortcut, such as `ctrl+b`, then `f`.
2. Type the yellow hint shown over the value you want.
3. The complete value is copied immediately.

When two-letter hints are needed, typing the first letter highlights matching
hints in green and dims the rest.

Press Escape or `ctrl+c` to cancel.

## What it recognizes

- File paths and `file:line:column` locations
- Git SHAs, remotes, branches, status paths, and diff paths
- HTTP, HTTPS, SSH, Git, and file URLs
- IPv4 addresses, UUIDs, hexadecimal values, and long numbers

Every visible occurrence receives a hint. The overlay preserves pane layout,
ANSI colors, and Unicode character alignment.

## Clipboard support

The plugin uses `pbcopy` on macOS. On Linux it detects `wl-copy`, `xclip`, or
`xsel`. Remote sessions and systems without a clipboard command fall back to
OSC 52, copying through the attached terminal.

## Development

Clone and link a development checkout:

```sh
git clone https://github.com/rotemb-wond/herdr-copy-hints.git
cd herdr-copy-hints
herdr plugin link "$PWD"
```

Run the dependency-free test suite:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_hints.py
```

## License

[MIT](LICENSE)
