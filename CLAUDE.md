# Monthly Sales & Use Tax Agent

## What this is

An automation for [Access Amenities](https://www.accessamenities.com) (a smart-vending
operator) that pulls the monthly Tax Report out of MicroMart (the point-of-sale platform for
the vending units), sums it by Tax Region, uploads it to Google Drive as a live spreadsheet,
and logs into the Georgia Tax Center so the totals can eventually be filed and paid there.
Filing/payment itself is the next thing to build — ask before inventing what that should look
like, same as every other step so far.

## This repo is public — a portfolio piece, not just internal tooling

The user is putting this repo on GitHub under their own name where recruiters and hiring
managers may read it. That changes a few defaults from a typical private project:

- **Commit often, in small increments**, not one big commit at the end of a work session.
- **Commit messages are natural language**, written like a person describing what they did and
  why — not technical/structured/AI-sounding. Mention real issues hit and how they were
  resolved (e.g. the GTC device-trust email-code surprise, the HubSpot popup blocking clicks,
  the false-positive login-success bug) — that history is part of what makes this worth reading
  as a portfolio piece, not something to sanitize away.
- **Keep `README.md` current for an external reader** — what this is, why it exists, how it
  works, how to run it — updated alongside the code, not as an afterthought. `CLAUDE.md` (this
  file) stays the technical/internal instructions for whoever (human or agent) is developing
  the tool next; `README.md` is the front door for someone who's never seen the project before.

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
  live. A `Context` class manages one persistent Chromium profile per site (`browser-state/`,
  gitignored) so a login session survives across runs. `STEPS` is an ordered list of `(name,
  function)` pairs; `run()` executes them in order, with `--resume-from <step name>` to restart
  partway through instead of redoing everything. Current steps, in order:
  1. `micromart_login` — shared with the sibling tool, see below.
  2. `download_tax_report` — MicroMart Analytics → Tax Report, filtered to the previous month,
     downloaded as CSV. Retries with a fresh page reload (up to 3 attempts) if the chart errors
     out or the underlying analytics query stalls — both confirmed live, not hypothetical.
  3. `summarize_tax_by_region` — sums Sales (pre-tax) / Tax Collected / Total (tax included) per
     Tax Region from that CSV into a second local file. Only one region exists today, but the
     logic is written to handle however many Access Amenities operates in later.
  4. `upload_tax_report_to_drive` — uploads the CSV as a Google Sheet (not a flat file): one tab
     with the raw data, one tab ("Tax-Region-Totals") holding a single QUERY formula that
     computes the same per-region totals live from the first tab, so they can be checked by eye
     in Drive without opening this repo.
  5. `gtc_login` — logs into the Georgia Tax Center (gtc.dor.ga.gov). See its docstring and the
     "GTC surprises" note below before touching it.
- On any failure, the run halts, screenshots whichever browser page was open
  (`debug-screenshots/`, gitignored), logs a resume command, and returns a nonzero exit code —
  it does not retry aggressively or guess at recovery.
- Credentials live only in macOS Keychain — see `config/keychain-setup.md`. Never in code, chat,
  or shell history. Same for Google OAuth: `config/oauth-client.json` identifies the app,
  `authorize_drive.py` is the one-time consent flow, the resulting refresh token goes to
  Keychain — see `docs/google-oauth-setup.md`.

## GTC surprises (confirmed live, not guessed)

- **A brand-new browser profile gets an email security-code challenge on first login**, even
  though the user's own regular browser sees no MFA at all on this account. GTC apparently
  trusts *devices*, not just credentials, and this tool's dedicated Playwright profile
  (`browser-state/gtc-profile/`) started out untrusted. `step_gtc_login`'s first version
  false-positived on this screen (it checked for the Username field disappearing, which is also
  true on the security-code screen) — fixed to detect the screen by name and fail with clear
  instructions instead of silently reporting success.
- **Now handled fully automatically** via `_fetch_gtc_security_code`: it reads the code straight
  out of the `info@accessamenities.com` Gmail inbox (read-only `gmail.readonly` scope, added to
  the same OAuth client as Drive/Sheets — only ever used to search for one specific email,
  `from:NoReply@dor.ga.gov subject:"Georgia Tax Center Security Code"`, filtered to after the
  login attempt started so a stale code from an older session is never picked up) and checks
  "Trust this device" automatically. Verified live against a genuinely fresh, never-logged-in
  browser profile — no human involved, and a second run against that same now-trusted profile
  skipped the challenge entirely, confirming the trust persists.
- **The user explicitly considered and declined reusing their real Chrome profile** for this
  instead (which would make GTC see an already-trusted device from the start) — rejected because
  it would need their everyday Chrome fully closed every time the automation runs, and it
  couples this tool's automation to their personal daily-use browser in a way the rest of this
  project deliberately avoids (see the per-site isolated `browser-state/` profiles above). If
  ever revisited, treat it as a real decision to re-raise with the user, not something to build
  speculatively.
- `src/manual_gtc_login.py` still exists as the fallback if the automated email-code fetch ever
  times out (e.g. Gmail access itself is broken, or the email is delayed) — see its
  `StepFailed` message.

## Adding the next step

Add a new function in `src/sync.py` (it receives the same `ctx: Context` — call
`ctx.micromart_page()` or `ctx.gtc_page()` for the relevant authenticated page), then append it
to `STEPS`. Ask the user what that step should actually do before writing it, and don't assume
success/failure detection logic works until it's been run live at least once — this project's
history so far (the HubSpot popup, the chart-error retries, the GTC device-trust screen) is
entirely things discovered by running the real thing, never guessed correctly upfront.

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
they haven't been copied over speculatively:

- Gmail HTML email reporting (`src/email_report.py`) — this tool's Drive OAuth client only has
  `drive.file` and `spreadsheets` scopes, deliberately not `gmail.send`/`gmail.readonly`, until
  there's an actual reason to add one.
- The rolling-30-day CSV export logic and other MicroMart *transactions-page* specifics — those
  are page-specific, not relevant to the Tax Report page this tool actually uses. (The HubSpot
  popup dismissal *was* reused — the Tax Report page has the same marketing popup.)
- VendSoft integration entirely — not relevant to this tool.
- Georgia filing/payment itself — `gtc_login` only gets as far as an authenticated GTC session.
  What happens after that (which page, which numbers go where, whether payment is ever
  automated or stays a human-in-the-loop step) hasn't been discussed yet.
- Any `launchd` scheduling — the sibling tool's LaunchAgent is a working reference for the
  pattern, but this tool isn't a settled-enough pipeline to schedule unattended yet (GTC filing
  isn't built, and the device-trust step needs a human at least once per machine).
