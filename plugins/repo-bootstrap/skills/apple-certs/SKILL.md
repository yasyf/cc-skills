---
name: apple-certs
description: Mint and stash the Apple release credentials a signed-app repo needs — a Developer ID Application signing certificate (local CSR, portal upload via agent-browser-with-cookies, full-chain .p12) and an App Store Connect notary API key — verify the identity actually signs, then push both into 1Password and the repo's GitHub secrets. Use when a repo bootstrapped with a Go/Swift signed-app release (release-app.yml / wrap-daemon-bundle) needs MACOS_SIGN_P12 / MACOS_SIGN_PASSWORD / MACOS_NOTARY_* filled, when renewing an expiring Developer ID cert, or when setting up notarization for a new signed macOS app. macOS; you must be signed in to developer.apple.com as the Account Holder.
allowed-tools: Bash(bash:*, openssl:*, security:*, codesign:*, curl:*, gh:*, op:*, base64:*, cookiesync:*, open:*, pkill:*), Read
effort: medium
---

# apple-certs

This skill mints the two Apple release credentials a signed-app repo consumes — a Developer ID Application certificate and an App Store Connect notary API key — proves each actually works, and stashes both where CI can reach them. Portal navigation runs through the `agent-browser-with-cookies` skill on the user's existing desktop session; nothing here automates a login or guesses at the UI.

The moving parts:

- **Two independent flows**, run as needed: (A) the **Developer ID Application signing certificate** (§1–§5) and (B) the **App Store Connect notary API key** (§6). A repo needs both to ship a signed + notarized `.app`.
- **Scripts** (this skill, `${CLAUDE_PLUGIN_ROOT}/skills/apple-certs/scripts/`):
  - `gen-csr.sh` — RSA-2048 key + CSR; key never leaves the machine.
  - `assemble-p12.sh` — full-chain `.p12` (leaf + Developer ID intermediate + Apple Root CA), Apple certs fetched + SHA-256-pinned; `--self-test` proves the guard offline.
  - `check-p12-chain.sh` — the hard full-chain guard (also run standalone).
  - `verify-signing-identity.sh` — throwaway-keychain import + `find-identity` + scratch codesign + `codesign --verify`.
- **Sibling script** (repo-bootstrap skill): `${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/set-release-secrets.sh <owner>/<repo>` — pushes all six release secrets from 1Password to the repo.
- **Target secrets** (op layout `op://<VAULT>/<NAME>/credential`, default vault `OpenClaw`): `MACOS_SIGN_P12` (base64 full-chain p12), `MACOS_SIGN_PASSWORD`, `MACOS_NOTARY_ISSUER_ID`, `MACOS_NOTARY_KEY_ID`, `MACOS_NOTARY_KEY` (base64 `.p8`). (`HOMEBREW_TAP_TOKEN` is out of scope here.)

## 1. Generate the CSR (private key stays local)

The private key is generated locally and never travels — only the CSR goes to the portal, and Apple issues a certificate for the key you kept. For a renewal, pass the existing key with `--key` instead of minting a new one, so the fresh cert pairs with the key already stashed.

- `gen-csr.sh --out-dir ./devid --common-name "Developer ID Application" [--email you@example.com]`
- Outputs `developer-id.key` (mode 0600 — KEEP LOCAL, never upload) and `developer-id.csr` (upload to the portal).
- Apple sets the certificate's real subject from the account; the CSR subject is cosmetic. Key type is fixed at RSA 2048 (the Developer ID requirement).

## 2. Portal cert flow (via agent-browser-with-cookies)

Authenticated navigation is the `agent-browser-with-cookies` skill's job: authorize with a truthful `--reason` (e.g. "download a Developer ID certificate"), seed `developer.apple.com`, and drive the portal on the user's existing login. Never automate the sign-in itself — a dead session is a return-early condition (§7), not something to script around.

- Primary URL (`U1`): `https://developer.apple.com/account/resources/certificates/add`
- Navigation: Certificates → **+** → **Developer ID Application** (under "Software") → upload `developer-id.csr` → **Download** the issued `.cer`.
- Seed only `developer.apple.com` for this flow; verify you land authenticated (an account affordance), not the Apple ID sign-in page.
- Navigate by **visible text / ARIA role + a snapshot**, never by guessed coordinates (see §7 UI drift).

## 3. Assemble the full-chain p12

The full three-cert chain is load-bearing. A p12 missing the root or intermediate still signs cleanly and `codesign --verify` passes — the failure surfaces at exec, where the signed binary dies with SIGKILL (exit 137) because its designated requirement can't be satisfied without the anchor chain. That's the quill incident, and `check-p12-chain.sh` exists so it can't recur.

- `assemble-p12.sh --cert ./DeveloperID_Application.cer --key ./devid/developer-id.key --out ./developer-id.p12 [--password PW]`
- Fetches + SHA-256-pins Apple Root CA + Developer ID intermediate (G1 `https://www.apple.com/certificateauthority/DeveloperIDCA.cer`, G2 `https://www.apple.com/certificateauthority/DeveloperIDG2CA.cer`, root `https://www.apple.com/appleca/AppleIncRootCertificate.cer`), picks the intermediate the leaf actually chains through, and re-opens the p12 to assert exactly 3 certs before writing.
- If `--password` is omitted a strong one is generated and printed — store it as `MACOS_SIGN_PASSWORD`.
- Offline self-check: `assemble-p12.sh --self-test` (dummy PKI; accepts a 3-cert p12, rejects a leaf-only one).

## 4. Verify the signing identity

Prove the p12 signs before it ever reaches CI. The verifier imports it into a throwaway keychain — the login keychain is never touched — signs a scratch binary, and verifies strictly; a p12 that passes here works in the release workflow's import step.

- `verify-signing-identity.sh --p12 ./developer-id.p12 --password "$MACOS_SIGN_PASSWORD"`
- Runs `check-p12-chain.sh`, imports into a throwaway keychain, lists the identity (`security find-identity -v -p codesigning`), signs a scratch binary, and runs `codesign --verify --strict`. The keychain is deleted on exit.
- `-v` lists only trusted identities: a real Developer ID cert appears; an untrusted/self-signed one does not (`CSSMERR_TP_NOT_TRUSTED`).

## 5. Stash the signing secrets (op item create + set-release-secrets.sh)

The p12 and its password land in 1Password exactly once; every repo that needs them pulls from there via the sibling script. Rotating the cert means updating one item, not chasing N repos' secrets.

- Base64 the p12: `base64 -i ./developer-id.p12` produces the `MACOS_SIGN_P12` value.
- `op item create` (Password item) storing the base64 p12 as `op://<VAULT>/MACOS_SIGN_P12/credential`, and the export password as `op://<VAULT>/MACOS_SIGN_PASSWORD/credential` (default vault `OpenClaw`).
- Push to a repo: `${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/set-release-secrets.sh <owner>/<repo>` (idempotent; `--vault` to override; `-n` dry-run).

## 6. Notary API key flow (App Store Connect)

Notarization uses a credential separate from signing: an App Store Connect API key. The `.p8` private key is offered for download exactly once, at creation — miss it and the key must be recreated.

- URL: `https://appstoreconnect.apple.com/access/integrations/api` (Users and Access → Integrations → App Store Connect API). Seed `appstoreconnect.apple.com` via agent-browser-with-cookies.
- Generate a **Team key** (role: Developer is sufficient for notarization). Capture the **Issuer ID** (page header) and the per-key **Key ID**; **download the `AuthKey_<KEYID>.p8` immediately — it is offered only once.**
- Base64 the key: `base64 -i AuthKey_<KEYID>.p8` produces `MACOS_NOTARY_KEY`. Store `MACOS_NOTARY_ISSUER_ID`, `MACOS_NOTARY_KEY_ID`, `MACOS_NOTARY_KEY` via `op item create`, then `set-release-secrets.sh <owner>/<repo>`.

## 7. Failure modes (return early with findings)

Every branch below ends the same way: stop, report findings with 2-4 concrete options, and let the caller decide. The portal has account-wide blast radius — never improvise around it.

### 2FA / dead session
- When the desktop session is stale or a 2FA challenge blocks navigation, return early; ask the user to sign in to `developer.apple.com` in their desktop browser (agent-browser-with-cookies "Log in and retry"), then re-run.

### UI drift
- When the portal layout has moved, navigate by visible text / ARIA role plus a `snapshot`, capture a screenshot, and report; **never guess-click** coordinates.

### Account Holder requirement + Developer ID cert cap
- Creating a Developer ID Application certificate requires the **Account Holder** role — a Developer/Admin cannot; surface this and stop if the session lacks it.
- Developer ID Application certs are **capped per account** (currently 5). When the cap is hit, surface the existing certs and their expiry and ask how to proceed.

### Never auto-revoke
- **Never revoke an existing certificate to free a slot.** Revocation invalidates every signature already made with it (shipped, notarized apps break in the field). Revoking is always a human decision, surfaced with the consequence — never an automated step.
