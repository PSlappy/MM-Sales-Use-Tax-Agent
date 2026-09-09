#!/usr/bin/env python3
"""
Monthly Sales & Use Tax Agent.

Shares its MicroMart login logic (including TOTP-based MFA and headless-to-headed
escalation) with the sibling MicroMart -> VendSoft Sales Reconciliation Agent tool,
extracted verbatim since both automate logging into the same MicroMart account. See
CLAUDE.md for the full story. After login, downloads the previous month's Tax
Report ("Tax Breakdown" pivot export) from MicroMart Analytics, saves it under
~/Desktop/Access-Amenities/Financials/Monthly-Sales-and-Use-Tax, sums Sales
(pre-tax)/Tax Collected/Total (tax included) per Tax Region into a separate
totals file (the figures that get filed and paid to each state monthly --
filing/payment itself is a future step, not built here), and uploads the
downloaded report to the same folder in Google Drive.

Manual run:       .venv/bin/python src/sync.py
Resume a step:    .venv/bin/python src/sync.py --resume-from download_tax_report
"""
import argparse
import csv
import logging
import os
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
BROWSER_STATE_DIR = ROOT / "browser-state"
DEBUG_DIR = ROOT / "debug-screenshots"

MICROMART_BASE = "https://platform.micromart.com"
MICROMART_DASHBOARD = f"{MICROMART_BASE}/dashboard"
MICROMART_TAX_REPORT = f"{MICROMART_BASE}/dashboard/analytics/tax-report"
MICROMART_LOGIN_FRAGMENT = "auth.micromart.com"

TAX_REPORT_TARGET_DIR = Path.home() / "Desktop" / "Access-Amenities" / "Financials" / "Monthly-Sales-and-Use-Tax"
DRIVE_FOLDER_ID_FILE = ROOT / "config" / "drive-folder-id.txt"
DRIVE_OAUTH_CLIENT_FILE = ROOT / "config" / "oauth-client.json"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
KEYCHAIN_DRIVE_REFRESH = "tax-agent-drive-oauth-refresh-token"

MAX_LOGIN_ATTEMPTS = 2  # capped low deliberately -- avoid tripping account lockouts

# Best-effort guesses -- kept in case MicroMart ever shows a non-TOTP one-time-code
# challenge. Update if/when a real one is seen (see CLAUDE.md's "Deferred" section).
OTP_PROBE_TEXTS = [
    "verification code",
    "one-time code",
    "enter the code",
    "two-factor",
    "2FA",
]

HEADLESS = os.environ.get("SYNC_HEADLESS") == "1"


class StepFailed(RuntimeError):
    """Raised by a step to halt the run. The step name is used for --resume-from."""


def _goto(page, url: str, retries: int = 3, delay_seconds: float = 5) -> None:
    """page.goto with a few retries for transient network blips (e.g. right after the Mac
    wakes from sleep and Wi-Fi is still reconnecting)."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            page.goto(url)
            return
        except PlaywrightError as e:
            last_error = e
            if attempt < retries:
                time.sleep(delay_seconds)
    raise last_error


def _wait_settled(page, timeout: int = 10000) -> None:
    """Best-effort settle wait. MicroMart's dashboard has a live-updating chart and chat
    widget that keep some network activity going indefinitely, so it never truly reaches
    networkidle -- don't treat that as fatal."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeoutError:
        pass


def get_keychain_secret(service: str, setup_hint: str = "config/keychain-setup.md") -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-a", os.environ["USER"], "-s", service, "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise StepFailed(f"Could not read '{service}' from Keychain. See {setup_hint}.")
    return result.stdout.strip()


def _drive_service():
    import json
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not DRIVE_OAUTH_CLIENT_FILE.exists():
        raise StepFailed(
            f"Missing {DRIVE_OAUTH_CLIENT_FILE}. See docs/google-drive-oauth-setup.md."
        )
    client_config = json.loads(DRIVE_OAUTH_CLIENT_FILE.read_text())["installed"]
    refresh_token = get_keychain_secret(
        KEYCHAIN_DRIVE_REFRESH, setup_hint="src/authorize_drive.py (run it once)"
    )
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=client_config["token_uri"],
        client_id=client_config["client_id"],
        client_secret=client_config["client_secret"],
        scopes=DRIVE_SCOPES,
    )
    return build("drive", "v3", credentials=creds)


class Context:
    def __init__(self, log: logging.Logger):
        self.log = log
        self._playwright = None
        self._micromart_ctx = None

    def _pw(self):
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        return self._playwright

    def _persistent_page(self, attr: str, profile_name: str, force_headed: bool = False):
        existing = getattr(self, attr)
        if force_headed and existing is not None:
            try:
                existing.close()
            except Exception:
                pass
            existing = None
            setattr(self, attr, None)
        if existing is None:
            profile_dir = BROWSER_STATE_DIR / profile_name
            profile_dir.mkdir(parents=True, exist_ok=True)
            headless = False if force_headed else HEADLESS
            existing = self._pw().chromium.launch_persistent_context(
                str(profile_dir), headless=headless
            )
            setattr(self, attr, existing)
        return existing.pages[0] if existing.pages else existing.new_page()

    def micromart_page(self, force_headed: bool = False):
        return self._persistent_page("_micromart_ctx", "micromart-profile", force_headed)

    def screenshot_all(self, tag: str) -> list:
        """Screenshot whichever browser page(s) are currently open, for failure diagnostics."""
        paths = []
        DEBUG_DIR.mkdir(exist_ok=True)
        for label, browser_ctx in (("micromart", self._micromart_ctx),):
            if browser_ctx is None:
                continue
            try:
                page = browser_ctx.pages[0] if browser_ctx.pages else None
                if page is None:
                    continue
                path = DEBUG_DIR / f"failure-{tag}-{label}.png"
                page.screenshot(path=str(path))
                paths.append(path)
            except Exception:
                pass
        return paths

    def close(self) -> None:
        if self._micromart_ctx is not None:
            try:
                self._micromart_ctx.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass


def _submit_totp_code(page, secret: str, log: logging.Logger, site_label: str) -> bool:
    """Best-effort: find a 6-digit code input and submit a freshly computed TOTP code.
    Returns False (without error) if no such field is found or pyotp isn't installed,
    so the caller can fall back to the manual-intervention path."""
    try:
        import pyotp
    except ImportError:
        log.warning("pyotp not installed -- can't auto-submit TOTP. `pip install pyotp`.")
        return False

    selector = "input[autocomplete='one-time-code'], input[maxlength='6'], input[inputmode='numeric']"
    try:
        page.wait_for_selector(selector, timeout=8000, state="visible")
    except PlaywrightTimeoutError:
        log.info("%s: no TOTP field appeared within 8s", site_label)
        return False

    candidates = page.locator(selector)
    log.info("%s: TOTP field candidates found: %d", site_label, candidates.count())
    code_input = candidates.first

    DEBUG_DIR.mkdir(exist_ok=True)
    page.screenshot(path=str(DEBUG_DIR / f"{site_label.lower()}-totp-01-field-found.png"))

    # Avoid submitting a code that's about to roll over -- network latency between filling
    # and the server validating it could otherwise turn a valid code into a rejected one,
    # and repeated rejections are exactly the kind of thing that gets an account locked.
    totp = pyotp.TOTP(secret)
    remaining = totp.interval - (time.time() % totp.interval)
    if remaining < 5:
        time.sleep(remaining + 0.5)
    code = totp.now()
    code_input.fill(code)
    page.screenshot(path=str(DEBUG_DIR / f"{site_label.lower()}-totp-02-filled.png"))

    submit = page.get_by_role("button", name=re.compile("verify|continue|submit|confirm", re.I))
    submit_count = submit.count()
    submit_labels = submit.all_inner_texts() if submit_count else []
    log.info("%s: TOTP submit button candidates: %s", site_label, submit_labels)
    if submit_count > 0:
        submit.first.click()
    else:
        page.keyboard.press("Enter")

    page.wait_for_timeout(800)
    page.screenshot(path=str(DEBUG_DIR / f"{site_label.lower()}-totp-03-after-click.png"))
    log.info("%s: submitted TOTP code automatically", site_label)
    return True


def _login_if_needed(
    log: logging.Logger,
    *,
    page_getter: Callable[[bool], object],
    dashboard_url: str,
    login_url_fragment: str,
    site_label: str,
    fill_fn: Callable[[object], None],
    submit_name: str,
    totp_keychain_service: Optional[str] = None,
) -> None:
    """page_getter(force_headed) returns the page to use -- called fresh each attempt so
    a headless failure can escalate to a headed browser for the retry (confirmed live in
    the sibling tool: MicroMart blocks headless Chromium logins specifically; headed
    succeeds with the same credentials/TOTP). fill_fn takes the current page, since it may
    change across attempts."""
    forced_headed = False
    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        page = page_getter(forced_headed)
        _goto(page, dashboard_url)
        _wait_settled(page)
        if login_url_fragment not in page.url:
            log.info("%s: already logged in (session reused)", site_label)
            return

        mode_note = " [headed escalation]" if forced_headed else ""
        log.info("%s: login required (attempt %d/%d)%s", site_label, attempt, MAX_LOGIN_ATTEMPTS,
                  mode_note)
        DEBUG_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(DEBUG_DIR / f"{site_label.lower()}-before-fill-attempt{attempt}.png"))
        try:
            fill_fn(page)
        except PlaywrightTimeoutError:
            page.screenshot(path=str(DEBUG_DIR / f"{site_label.lower()}-fill-timeout-attempt{attempt}.png"))
            raise
        page.get_by_role("button", name=re.compile(submit_name, re.I)).click()
        _wait_settled(page)

        if login_url_fragment not in page.url:
            log.info("%s: login succeeded", site_label)
            return

        DEBUG_DIR.mkdir(exist_ok=True)
        debug_shot = DEBUG_DIR / f"{site_label.lower()}-after-submit-attempt{attempt}.png"
        page.screenshot(path=str(debug_shot))
        log.info("%s: not yet past login after submit -- saved %s", site_label, debug_shot)

        if totp_keychain_service:
            secret = None
            secret_result = subprocess.run(
                ["security", "find-generic-password", "-a", os.environ["USER"],
                 "-s", totp_keychain_service, "-w"],
                capture_output=True, text=True,
            )
            if secret_result.returncode == 0:
                secret = secret_result.stdout.strip()
            if secret and _submit_totp_code(page, secret, log, site_label):
                _wait_settled(page)
                if login_url_fragment not in page.url:
                    log.info("%s: login succeeded (TOTP auto-submitted)", site_label)
                    return

        for probe in OTP_PROBE_TEXTS:
            if page.get_by_text(re.compile(probe, re.I)).count() > 0:
                raise StepFailed(
                    f"{site_label} is asking for a one-time code and it couldn't be "
                    f"auto-submitted. Open the browser window this script controls "
                    f"(profile under browser-state/), complete it manually, then re-run "
                    f"with the same step -- the session will be saved for next time."
                )

        if login_url_fragment not in page.url:
            log.info("%s: login succeeded", site_label)
            return

        if HEADLESS and not forced_headed:
            log.warning("%s: headless login attempt failed -- escalating retry to headed mode",
                        site_label)
            forced_headed = True

    raise StepFailed(f"{site_label} login failed after {MAX_LOGIN_ATTEMPTS} attempts.")


# --- steps -------------------------------------------------------------

def step_micromart_login(ctx: Context) -> None:
    email = get_keychain_secret("micromart-platform-username")
    password = get_keychain_secret("micromart-platform")

    def fill(page):
        page.get_by_placeholder("name@example.com").fill(email)
        page.get_by_placeholder("Enter your password").fill(password)

    _login_if_needed(
        ctx.log,
        page_getter=ctx.micromart_page,
        dashboard_url=MICROMART_DASHBOARD,
        login_url_fragment=MICROMART_LOGIN_FRAGMENT,
        site_label="MicroMart",
        fill_fn=fill,
        submit_name="sign in",
        totp_keychain_service="micromart-platform-totp-secret",
    )


def _dismiss_hubspot_popup(page, log: logging.Logger) -> None:
    """MicroMart embeds HubSpot 'Web Interactives' marketing popups that can overlay
    the page and intercept clicks -- confirmed live on the Tax Report page, same as
    the sibling tool hit on its transactions page. Not part of the app itself, just
    clear it out of the way. Don't call this once a MicroMart menu/panel of our own
    is open -- the Escape key press closes those too (confirmed live: it silently
    dismissed the Download-data panel before it could be interacted with)."""
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        removed = page.evaluate(
            """() => {
                const selectors = [
                    '#hs-interactives-modal-overlay',
                    '#hs-web-interactives-top-anchor',
                    'iframe[title="Popup CTA"]',
                ];
                let count = 0;
                for (const sel of selectors) {
                    document.querySelectorAll(sel).forEach((el) => { el.remove(); count++; });
                }
                return count;
            }"""
        )
        if removed:
            log.info("Removed %d HubSpot popup element(s)", removed)
    except Exception:
        pass


def _previous_month_period(today: date) -> date:
    """The tax period a report run on `today` should cover -- the prior calendar
    month, not the month the script happens to run in (this tool is scheduled for
    the 1st of the month, pulling the just-completed month's data)."""
    first_of_this_month = today.replace(day=1)
    return first_of_this_month - timedelta(days=1)


def _tax_report_filename(period: date) -> str:
    return f"tax-report-summary-{period.strftime('%m-%Y')}.csv"


def _tax_region_totals_filename(period: date) -> str:
    return f"tax-region-totals-{period.strftime('%m-%Y')}.csv"


def step_download_tax_report(ctx: Context) -> None:
    page = ctx.micromart_page()
    log = ctx.log

    CHART_ERROR_TEXT = "There was a problem displaying this chart"
    MAX_ATTEMPTS = 3
    pivot = None
    dashcard = None
    success = False

    for attempt in range(1, MAX_ATTEMPTS + 1):
        # A fresh navigation each attempt doubles as the "reload and retry" that
        # a stuck or errored chart needs -- confirmed live: the analytics query
        # can either hang indefinitely or fail outright with "There was a
        # problem displaying this chart," and a plain reload clears both.
        _goto(page, MICROMART_TAX_REPORT)
        page.wait_for_selector("button[aria-label='Date']", timeout=90000, state="visible")
        _dismiss_hubspot_popup(page, log)

        # Confirmed live: a stale filter (e.g. "Product Tax Group") can persist
        # on this dashboard across runs on the same profile and silently scope
        # the export wrong -- clear every active filter chip, not just Date.
        filters = page.get_by_test_id("fixed-width-filters")
        for _ in range(6):
            clear_btn = filters.get_by_role("button", name="Clear")
            if clear_btn.count() == 0:
                break
            clear_btn.first.click()
            page.wait_for_timeout(400)

        # With every filter cleared, the Date button reopens the full preset
        # menu (Today/.../Previous month/...) rather than a relative-range
        # editor for whatever filter type was previously active.
        date_btn = filters.get_by_role("button", name="Date")
        date_btn.click()
        page.wait_for_timeout(500)
        controls_id = date_btn.get_attribute("aria-controls")
        if not controls_id:
            raise StepFailed("Tax Report: date filter dropdown did not open as expected.")
        page.locator(f"#{controls_id}").get_by_text("Previous month", exact=True).first.click()
        log.info("Tax Report: date filter set to Previous month (attempt %d/%d)", attempt, MAX_ATTEMPTS)

        pivot = page.locator("[data-testid='visualization-root'][data-viz-ui-name='Pivot Table']")
        try:
            pivot.wait_for(state="visible", timeout=90000)
        except PlaywrightTimeoutError:
            raise StepFailed("Tax Report: the Tax Breakdown pivot table card never appeared.")
        dashcard = page.locator("[data-testid='dashcard']").filter(has=pivot)
        dashcard.scroll_into_view_if_needed()

        # The underlying analytics query is slow and varies a lot -- confirmed
        # live anywhere from ~15s to several minutes for the same report, and
        # it can also fail outright rather than just being slow. Poll for
        # either real content or the error state rather than a fixed sleep.
        log.info("Tax Report: waiting for Tax Breakdown data to finish loading (up to 5 min)...")
        errored = False
        for _ in range(150):
            text = pivot.inner_text()
            if CHART_ERROR_TEXT in text:
                errored = True
                break
            if "%" in text and len(text) > 50:
                success = True
                break
            page.wait_for_timeout(2000)

        if success:
            break
        reason = "the chart failed to display" if errored else "it never finished loading within 5 minutes"
        log.warning("Tax Report: Tax Breakdown data failed (%s) -- attempt %d/%d", reason, attempt, MAX_ATTEMPTS)

    if not success:
        raise StepFailed("Tax Report: Tax Breakdown data failed to load after repeated attempts.")

    DEBUG_DIR.mkdir(exist_ok=True)
    page.screenshot(path=str(DEBUG_DIR / "tax-report-breakdown-loaded.png"))

    # The card's "..." action menu only renders once the card is hovered.
    dashcard.hover()
    page.wait_for_timeout(300)
    ellipsis = dashcard.locator("[data-testid='public-or-embedded-dashcard-menu']")
    ellipsis.click()
    page.wait_for_timeout(400)

    menu = page.locator("[role='menu']")
    if menu.count() == 0:
        raise StepFailed("Tax Report: the Tax Breakdown '...' menu did not open.")
    menu.first.locator("[role='menuitem']").filter(has_text="Download results").first.click()

    try:
        page.wait_for_selector("text=Download data", timeout=10000)
    except PlaywrightTimeoutError:
        raise StepFailed("Tax Report: 'Download data' panel did not open after clicking Download results.")

    # .csv is the default-selected format, but select it explicitly for robustness
    # against the default ever changing.
    page.get_by_text(".csv", exact=True).click()

    # "Keep the data formatted" and "Keep the data pivoted" are both checked by
    # default -- confirmed live -- but check explicitly rather than trust that.
    checkboxes = page.locator("input[type='checkbox']")
    for i in range(checkboxes.count()):
        cb = checkboxes.nth(i)
        if not cb.is_checked():
            cb.check()

    period = _previous_month_period(date.today())
    TAX_REPORT_TARGET_DIR.mkdir(parents=True, exist_ok=True)
    dest = TAX_REPORT_TARGET_DIR / _tax_report_filename(period)

    # The export can take a while to generate server-side -- Playwright's default
    # download-wait timeout is only 30s, give it much longer.
    with page.expect_download(timeout=300000) as download_info:
        page.get_by_role("button", name="Download", exact=True).click()
    download = download_info.value
    download.save_as(str(dest))
    log.info("Tax Report: saved %s", dest)


def _parse_amount(value: str) -> float:
    return float(value.replace(",", "").replace("$", ""))


def step_summarize_tax_by_region(ctx: Context) -> None:
    """Sums Sales (pre-tax), Tax Collected, and Total (tax included) per Tax
    Region -- currently a single Georgia region, but the export can carry
    multiple Tax Regions (and multiple Store/Product Tax Group rows per
    region) once more locations are added, and each region's totals are
    what eventually gets filed and paid separately. Filing/payment itself
    is a future step -- this just produces the per-region figures."""
    log = ctx.log
    period = _previous_month_period(date.today())
    source_path = TAX_REPORT_TARGET_DIR / _tax_report_filename(period)
    if not source_path.exists():
        raise StepFailed(
            f"Expected file not found: {source_path}. Resume from download_tax_report first."
        )

    totals = {}
    region_order = []
    with open(source_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            region = row["Tax Region"]
            if region not in totals:
                totals[region] = {"sales": 0.0, "tax_collected": 0.0, "total": 0.0}
                region_order.append(region)
            totals[region]["sales"] += _parse_amount(row["Sales (pre-tax)"])
            totals[region]["tax_collected"] += _parse_amount(row["Tax Collected"])
            totals[region]["total"] += _parse_amount(row["Total (tax included)"])

    dest = TAX_REPORT_TARGET_DIR / _tax_region_totals_filename(period)
    with open(dest, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Tax Region", "Sales (pre-tax)", "Tax Collected", "Total (tax included)"])
        for region in region_order:
            t = totals[region]
            writer.writerow([region, f"{t['sales']:.2f}", f"{t['tax_collected']:.2f}", f"{t['total']:.2f}"])
            log.info(
                "Tax Region totals -- %s: sales=%.2f tax_collected=%.2f total=%.2f",
                region, t["sales"], t["tax_collected"], t["total"],
            )
    log.info("Saved tax region totals to %s", dest)


def step_upload_tax_report_to_drive(ctx: Context) -> None:
    from googleapiclient.http import MediaFileUpload

    log = ctx.log
    period = _previous_month_period(date.today())
    file_path = TAX_REPORT_TARGET_DIR / _tax_report_filename(period)
    if not file_path.exists():
        raise StepFailed(
            f"Expected file not found: {file_path}. Resume from download_tax_report first."
        )

    if not DRIVE_FOLDER_ID_FILE.exists():
        raise StepFailed(
            f"No Drive folder ID configured. Put the target folder's ID (one line) in "
            f"{DRIVE_FOLDER_ID_FILE}. See docs/google-drive-oauth-setup.md."
        )
    folder_id = DRIVE_FOLDER_ID_FILE.read_text().strip()

    service = _drive_service()
    file_metadata = {"name": file_path.name, "parents": [folder_id]}
    media = MediaFileUpload(str(file_path), mimetype="text/csv")
    service.files().create(
        body=file_metadata, media_body=media, fields="id", supportsAllDrives=True,
    ).execute()
    log.info("Uploaded %s to Drive folder %s", file_path.name, folder_id)


STEPS = [
    ("micromart_login", step_micromart_login),
    ("download_tax_report", step_download_tax_report),
    ("summarize_tax_by_region", step_summarize_tax_by_region),
    ("upload_tax_report_to_drive", step_upload_tax_report_to_drive),
]


def run(resume_from: Optional[str]) -> int:
    log_file = LOG_DIR / f"{date.today().strftime('%Y-%m-%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("sync")
    ctx = Context(log=log)

    names = [name for name, _ in STEPS]
    if resume_from and resume_from not in names:
        log.error("Unknown --resume-from step %r. Valid steps: %s", resume_from, names)
        return 2
    start_index = names.index(resume_from) if resume_from else 0

    try:
        for name, fn in STEPS[start_index:]:
            log.info("=== step: %s ===", name)
            try:
                fn(ctx)
            except StepFailed as e:
                log.error("Step '%s' failed: %s", name, e)
                for path in ctx.screenshot_all(name):
                    log.error("FAILURE_SCREENSHOT: %s", path)
                log.error("Fix the issue, then resume with: --resume-from %s", name)
                return 1
            except Exception:
                log.exception("Step '%s' crashed unexpectedly", name)
                for path in ctx.screenshot_all(f"{name}-crash"):
                    log.error("FAILURE_SCREENSHOT: %s", path)
                log.error("Fix the issue, then resume with: --resume-from %s", name)
                return 1
        log.info("All steps completed")
        return 0
    finally:
        ctx.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-from", help="step name to resume from", default=None)
    args = parser.parse_args()
    sys.exit(run(args.resume_from))


if __name__ == "__main__":
    main()
