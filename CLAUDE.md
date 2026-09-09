# Monthly Sales & Use Tax Agent

## What this is

An automation for [Access Amenities](https://www.accessamenities.com) (a smart-vending
operator) that will pull data out of MicroMart (the point-of-sale platform for the vending
units) to support monthly sales & use tax work. **Only the MicroMart login step is built so
far** — everything after a successful login is intentionally undefined and waiting on the
user to specify it. Do not guess at or invent post-login functionality; ask.

## Why it shares code with a sibling tool

This repo's login logic (`src/sync.py`, everything through `step_micromart_login`) was
extracted, close to verbatim, from a sibling project at
`../micromart-vendsoft-sync/` — the **MicroMart → VendSoft Sales Reconciliation Agent**, a
daily automation that logs into the same MicroMart account and has been running in production
since early September 2026. That tool's own `CLAUDE.md`-equivalent is its
[README](../micromart-vendsoft-sync/README.md) and
[repo](https://github.com/PSlappy/MM-VS-Sales-Reconciliation-Agent) — worth reading if you need
more context on *why* the login logic is shaped the way it is, since several of its details
came from real production incidents, not upfront design:

- **MicroMart blocks headless Chromium logins specifically.** Confirmed live: five consecutive
  headless login attempts hit an account-level "contact an admin" block with valid credentials
  and a valid TOTP code; two consecutive headed (visible-browser) attempts with the identical
  credentials succeeded immediately. The login logic here already handles this: it stays
  headless by default and automatically escalates to a headed browser only if a login attempt
  actually fails (see `_login_if_needed`'s `forced_headed` logic) — don't remove this thinking
  it's unnecessary complexity.
- **MFA is TOTP-based and fully automated.** The account requires MFA; rather than a human
  typing a code, the script computes valid codes itself from a Keychain-stored secret
  (`pyotp`). `src/totp_code.py` prints a fresh code on demand for manual use if you ever need
  one by hand (e.g. finishing an MFA reset).
- **Repeated automated login attempts risk a real account lockout.** This already happened once
  during the sibling tool's development, from testing too aggressively against the live
  account. Be conservative about how often you re-run login flows against the real site while
  developing — prefer `--resume-from micromart_login` with the existing persisted session
  (`browser-state/micromart-profile/`) over forcing fresh logins, and don't loop retries beyond
  what's already built in (capped at `MAX_LOGIN_ATTEMPTS = 2`).
- **Deliberately not built, and should stay that way until genuinely needed**: an MFA
  self-healing recovery-code flow (detect a forced recovery prompt → use the stored recovery
  code → complete the resulting forced MFA reset → rotate secrets in Keychain → always alert
  the user). The design for this is approved (see the sibling repo's README "Deferred" section)
  but it must only ever be built and exercised against a genuine live occurrence, never tested
  speculatively — the same applies here, since this tool hits the same MicroMart account.

## Architecture

- `src/sync.py` — a deterministic Playwright pipeline, not an AI agent driving the browser
  live. A `Context` class manages one persistent Chromium profile (`browser-state/`, gitignored)
  so a login session survives across runs. `STEPS` is an ordered list of `(name, function)`
  pairs; `run()` executes them in order, with `--resume-from <step name>` to restart partway
  through instead of redoing everything. Right now `STEPS` contains exactly one step,
  `micromart_login`.
- On any failure, the run halts, screenshots whichever browser page was open
  (`debug-screenshots/`, gitignored), logs a resume command, and returns a nonzero exit code —
  it does not retry aggressively or guess at recovery.
- Credentials live only in macOS Keychain — see `config/keychain-setup.md`. Never in code, chat,
  or shell history.

## Adding the next step

Add a new function below `step_micromart_login` in `src/sync.py` (it receives the same `ctx:
Context` and can call `ctx.micromart_page()` to get the authenticated page), then append it to
`STEPS`. Ask the user what that step should actually do before writing it — this is explicitly
where the two tools diverge, and no assumptions have been made about it yet.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/playwright install chromium
.venv/bin/python src/sync.py
```

No scheduling is set up yet (no `launchd` LaunchAgent) — that's premature until there's a real
pipeline to schedule. When there is, the sibling tool's LaunchAgent
(`~/Library/LaunchAgents/com.accessamenities.micromart-vendsoft-sync.plist`) is a working
reference for the pattern (headless by default, daily/monthly `StartCalendarInterval`, catches
up automatically if the Mac was asleep at trigger time).

## Not included here (by design, ask before adding)

The sibling tool has patterns for these that can be reused if/when this tool needs them, but
none of it has been copied over speculatively:

- Google Drive upload / Gmail HTML email reporting (OAuth setup, `src/email_report.py`)
- The rolling-30-day CSV export logic, HubSpot popup dismissal, and other MicroMart
  *transactions-page* specifics — those are page-specific, not login-specific, and weren't part
  of what was asked to be shared here
- VendSoft integration entirely — not relevant to this tool
