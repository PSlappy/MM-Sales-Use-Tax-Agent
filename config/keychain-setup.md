# Credentials

This tool reuses the **same MicroMart Keychain entries** as the sibling
`micromart-vendsoft-sync` tool (same `automations@accessamenities.com` account) — nothing new
to set up if that tool is already configured on this Mac:

- `micromart-platform` — password
- `micromart-platform-username` — email
- `micromart-platform-totp-secret` — TOTP secret for MFA

If setting up fresh (e.g. on a different machine), see the sibling tool's
[`config/keychain-setup.md`](../../micromart-vendsoft-sync/config/keychain-setup.md) for the
exact `read`-based commands that avoid ever typing a password into a chat or shell history.

**Note**: because this tool has its own separate browser profile (`browser-state/`, not shared
with the sibling tool), it will need its own first-time login the first time it runs — expect
one TOTP consumption for that, and possibly the same headless-login block the sibling tool hit
in production (see CLAUDE.md) if it ever runs headless before a session is established.

## Google Drive upload

Unlike MicroMart, this is **not** shared with the sibling tool — see
[`docs/google-oauth-setup.md`](../docs/google-oauth-setup.md) for why (a separate
OAuth Client keeps the two tools independently revocable). Its refresh token lives under its own
Keychain entry, `tax-agent-drive-oauth-refresh-token`, written automatically by
`src/authorize_drive.py` during the one-time consent flow — not something you type in by hand.

## Georgia Tax Center (gtc.dor.ga.gov)

Username + password only day to day — no MFA on a browser GTC already recognizes. Run these
yourself in Terminal (same `read`-based pattern as MicroMart's setup above — nothing ever
passes through chat or shell history):

```bash
printf "GTC username: " && read -rs PW && echo && security add-generic-password -a "$USER" -s "gtc-dor-ga-username" -w "$PW" -U && unset PW
```

```bash
printf "GTC password: " && read -rs PW && echo && security add-generic-password -a "$USER" -s "gtc-dor-ga" -w "$PW" -U && unset PW
```

`step_gtc_login` in `src/sync.py` reads these two entries. A brand-new browser profile (like
this tool's dedicated one, on its first-ever login) does get an emailed device-verification
code, confirmed live — handled automatically by reading that email via the Gmail access set up
in the Google OAuth step above, no separate credential needed for it. See the "GTC surprises"
note in `CLAUDE.md` for the full story.
