# Contributing

Thanks for helping make Copy Hints better.

## Report a bug or suggest a pattern

Open an issue and include representative terminal text. Replace secrets,
private URLs, and personal paths before posting. For matching bugs, explain
exactly which substring should be copied.

Questions and early ideas belong in
[GitHub Discussions](https://github.com/rotemb-wond/herdr-copy-hints/discussions).

## Develop locally

Requirements:

- Herdr 0.7.0 or newer
- Python 3.10 or newer
- macOS or Linux

Clone and link a development checkout:

```sh
git clone https://github.com/rotemb-wond/herdr-copy-hints.git
cd herdr-copy-hints
herdr plugin link "$PWD"
```

Run all tests:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_hints.py
```

Changes to matching, keyboard input, resizing, or clipboard behavior should
include an end-to-end pseudo-terminal test where practical. Test visual
changes interactively in Herdr at narrow and wide terminal sizes.

Keep the plugin dependency-free unless a dependency provides a clear
reliability or maintainability benefit. Do not include pane contents,
clipboard data, or user configuration in logs or error reports.
