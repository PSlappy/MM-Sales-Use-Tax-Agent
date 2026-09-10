# Google OAuth setup — Drive, Sheets, Gmail (for info@accessamenities.com)

Same Google Cloud project as the sibling `micromart-vendsoft-sync` tool
(`vendsoft-sales-import-tool` — Drive and Gmail APIs already enabled from that tool's own setup,
"Internal" consent screen already configured for the Workspace org), but a **separate OAuth
Client** for this tool rather than reusing the sibling's. Reasoning: Google's connected-apps
revocation (`myaccount.google.com/permissions`) works per OAuth Client, not per refresh token —
sharing a Client would mean revoking one tool's access silently kills the other's too. A second
Client keeps them independently revocable and shows up as its own distinct app in Google's UI,
matching the "separate Keychain entries per tool" approach already used for the MicroMart
credentials.

## 1. Enable the Sheets API (same project; Drive and Gmail APIs already enabled)

**APIs & Services → Library → Google Sheets API → Enable.** Needed because the report is
uploaded as a Google Sheet (two tabs: the raw report, and a formula-driven "Tax-Region-Totals"
tab), not a flat CSV file — see `src/sync.py`'s `step_upload_tax_report_to_drive`.

## 2. Create the new OAuth Client (same project, skip consent-screen setup)

1. https://console.cloud.google.com/ → select the existing `vendsoft-sales-import-tool` project
   (signed in as whichever Google account manages it — the sibling's setup doc has this history).
2. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
3. Application type: **Desktop app**.
4. Name it `monthly-sales-and-use-tax-agent`.
5. Download the resulting JSON and save it to:
   ```
   monthly-sales-and-use-tax-agent/config/oauth-client.json
   ```
   Already covered by `.gitignore` (`*oauth-client*.json`). This file identifies the app, not a
   credential for your Drive data by itself — lower-risk than a service account key, but keep it
   out of git regardless.

## 3. One-time consent

Once `config/oauth-client.json` is in place:

```bash
.venv/bin/python src/authorize_drive.py
```

Opens a browser window. Sign in as `info@accessamenities.com` and approve access — scoped to:

- `drive.file` — create/place the file in the target folder; it can only see/write files it
  creates or that are explicitly shared with it, not your whole Drive.
- `spreadsheets` — write the "Tax-Region-Totals" formula tab via the Sheets API (`drive.file`
  alone doesn't cover Sheets API calls even on files this app created itself).
- `gmail.readonly` — read the Georgia Tax Center's emailed device-trust security code so a
  new/untrusted browser profile's first login doesn't need a human. Read-only, and the code only
  ever searches for one specific email (`from:NoReply@dor.ga.gov subject:"Georgia Tax Center
  Security Code"`) — see `_fetch_gtc_security_code` in `src/sync.py`.

The refresh token is stored in Keychain as `tax-agent-drive-oauth-refresh-token` — a new,
separate entry from the sibling tool's `vendsoft-drive-oauth-refresh-token` (its own separate
Gmail scope, `gmail.send`, is for sending its daily report email, unrelated to this tool's
`gmail.readonly` use). Re-run this any time scopes change (it overwrites the Keychain entry in
place).

## 4. Folder ID

Target: `Shared drives → Access Amenities → Financials → Monthly-Sales-and-Use-Tax`
Folder ID: `19h3wCaM0yD3s8f5qJWN1iYMFYTTjNspw` (already saved to `config/drive-folder-id.txt`,
gitignored).

After steps 1–3 are done, the upload step in `src/sync.py` reads the refresh token from Keychain
and the folder ID from `config/drive-folder-id.txt` — no further setup needed, and no browser
prompts on subsequent runs.
