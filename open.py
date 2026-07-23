#!/usr/bin/env python3

import os
import subprocess
import sys


def main() -> int:
    pane_id = os.environ.get("HERDR_PANE_ID")
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")

    if not pane_id:
        print("copy-hints: no focused pane in the plugin context", file=sys.stderr)
        return 1

    result = subprocess.run(
        [
            herdr,
            "plugin",
            "pane",
            "open",
            "--plugin",
            "rotemb-wond.copy-hints",
            "--entrypoint",
            "hints",
            "--placement",
            "overlay",
            "--env",
            f"HERDR_HINT_TARGET_PANE_ID={pane_id}",
            "--focus",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())
        subprocess.run(
            [
                herdr,
                "notification",
                "show",
                "Copy Hints failed",
                "--body",
                (detail or "Herdr could not open the hint overlay")[:240],
                "--position",
                "bottom-center",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
