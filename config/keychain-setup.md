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
