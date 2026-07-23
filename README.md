# Herdr Copy Hints

Keyboard-driven copy hints for [Herdr](https://herdr.dev), inspired by
[tmux-fingers](https://github.com/Morantron/tmux-fingers).

Press one shortcut to place compact letter hints directly over paths, Git
commits, URLs, and other useful values in the active pane. Type a hint to copy
the full value immediately.

## Features

- Native Herdr overlay that preserves the pane layout and ANSI colors
- One- or two-letter keyboard hints with no Enter required
- Every visible occurrence is selectable
- Recognizes:
  - file paths and `file:line:column` locations
  - Git SHAs, remotes, branches, status paths, and diff paths
  - HTTP, HTTPS, SSH, Git, and file URLs
  - IPv4 addresses, UUIDs, hexadecimal values, and long numbers
- Unicode-aware hint placement
- Local clipboard support on macOS, Wayland, and X11
- OSC 52 fallback for remote sessions and minimal Linux environments
- No Python dependencies outside the standard library

## Requirements

- Herdr 0.7.0 or newer
- Python 3.10 or newer
- macOS or Linux

On Linux, local clipboard commands are detected in this order: `wl-copy`,
`xclip`, then `xsel`. When none is installed, or when running over SSH, the
plugin uses OSC 52 to copy through the attached terminal.

## Install

After publishing this directory as a GitHub repository:

```sh
herdr plugin install rotemb-wond/herdr-copy-hints
```

For local development, clone the repository and link it:

```sh
git clone https://github.com/rotemb-wond/herdr-copy-hints.git
cd herdr-copy-hints
herdr plugin link "$PWD"
```

Add a shortcut to `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "rotemb-wond.copy-hints.open"
description = "show copy hints over the active pane"
```

Apply the binding to a running server:

```sh
herdr server reload-config
```

## Use

1. Press the configured shortcut, such as `ctrl+b`, then `f`.
2. Type the yellow hint shown over the value you want.
3. The complete value is copied immediately.

When two-letter hints are needed, typing the first letter highlights matching
hints in green and dims the rest.

Press Escape or `ctrl+c` to cancel.

## Test

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_hints.py
```

## Publish to the Herdr marketplace

1. Create a public GitHub repository named `herdr-copy-hints`.
2. Push the contents of this directory to the repository root.
3. Add the GitHub topic `herdr-plugin`.

Herdr discovers marketplace entries automatically from that topic. See the
[Herdr marketplace documentation](https://herdr.dev/docs/marketplace/).

## License

[MIT](LICENSE)
