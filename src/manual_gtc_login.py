#!/usr/bin/env python3
"""
One-time manual completion of GTC's device-trust challenge.

Run this yourself in your own Terminal (not through the automation) so a real,
visible browser window opens on your screen -- Playwright's headed browser
launched from some sandboxed shells doesn't actually display anything to you
(same issue hit with authorize_drive.py's browser-launch step).

    .venv/bin/python src/manual_gtc_login.py

Log in, complete the emailed security code, and check "Trust this device". Once
done, this profile (browser-state/gtc-profile/) is trusted and future automated
runs of sync.py's gtc_login step should skip this challenge entirely.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync import Context, GTC_BASE, _goto  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ctx = Context(log=logging.getLogger("manual_gtc_login"))
    page = ctx.gtc_page(force_headed=True)
    _goto(page, GTC_BASE)
    input(
        "Complete login, the emailed security code, and check 'Trust this device' "
        "in the browser window, then press Enter here to close it: "
    )
    ctx.close()


if __name__ == "__main__":
    main()
