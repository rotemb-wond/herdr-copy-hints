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
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
