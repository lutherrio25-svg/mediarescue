# Signing the Windows build

Why this file exists: without a signature, Windows shows **"Unknown publisher"** every time someone
downloads `mediarescue-<version>-setup.exe` — not once, but for every download until enough strangers
have run it. A signature is the only thing that changes that, and a signature Windows *trusts* has to
come from a certificate authority. That part is not free.

What is already free, and already built:

- `packaging/sign.ps1` — signs every `.exe`/`.dll` in a folder, timestamps it (RFC 3161, so the
  signature outlives the certificate), then proves each file carries one. It takes either a `.pfx`
  file (CI) or a thumbprint of a certificate in the store (a hardware token, or a cloud signer).
- `.github/workflows/build-exe.yml` — calls it for both exes and for the installer. The steps are
  inert until a certificate exists; the log then prints a warning instead of failing the release.
- A local rehearsal against a throwaway self-signed certificate, so the mechanics are never
  discovered for the first time on release day:

```powershell
$c = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=Star Corps bks' `
     -KeyUsage DigitalSignature -CertStoreLocation Cert:\CurrentUser\My
.\packaging\sign.ps1 -Path dist-exe\mediarescue -Thumbprint $c.Thumbprint -Verify -AllowUntrusted
Remove-Item "Cert:\CurrentUser\My\$($c.Thumbprint)"
```

Verified on this machine on 2026-11-22: both exes took an Authenticode signature, DigiCert's
timestamp authority stamped them, and `-Verify` reported the chain as `UntrustedRoot` only — exactly
what a self-signed certificate is supposed to look like. A file signed that way still warns on
someone else's computer, so it is a rehearsal, never a release.

## The routes, with real numbers

| Route | Cost | Publisher name on the UAC/SmartScreen dialog | Notes |
|---|---|---|---|
| **SignPath Foundation** (open source) | **free** | `SignPath Foundation` | Free OV-level signing for OSI-licensed public projects. The binary is built in *their* managed pipeline from your repo, so their certificate signs it. Application + review, days to weeks. |
| Certum Open Source | ~€49/yr (cloud) — was free, now billed | `Open Source Developer, <name>` | Cert in your own name. Requires SimplySign desktop + their activation process. |
| Azure Artifact Signing (formerly Trusted Signing) | $9.99/mo ($119.88/yr), 5,000 signatures | your verified identity | Individual accounts accepted; needs an Azure subscription and identity validation. Cheapest own-name route with a usable API/CI integration. |
| OV certificate (Sectigo, resellers) | from ~$211–$219/yr | your company | Hardware token or cloud HSM (keys can no longer be exported since 2023). SmartScreen reputation still has to accumulate. |
| EV certificate | from ~$287/yr | your company | Immediate SmartScreen reputation. Hardware token only. |

`mediarescue` qualifies for the first row: MIT (OSI-approved), public repo, released, actively
maintained, and the binaries are built from the repo in CI — which is exactly the condition SignPath
verifies. The cost is that the publisher shown is the foundation, not `Star Corps bks`.

## Applying for the free route (the only work that needs a human)

1. <https://signpath.org/> → **Apply for Free Code Signing**. Give the repository URL
   (`https://github.com/lutherrio25-svg/mediarescue`), the download page (`/releases/latest`), and one
   line on what the program does.
2. They check the licence file, that the project builds from source, and that it is maintained. No
   payment, no personal ID.
3. When approved, install the **SignPath GitHub App** on the repository and create, in their web UI:
   - an *artifact configuration* pointing at `dist-setup/*.exe` and `dist-exe/mediarescue/*.exe`,
   - a *signing policy* (slug, e.g. `release-signing`),
   - note the **organization id**, **project slug** and **policy slug**.
4. Add to the repository: secret `SIGNPATH_API_TOKEN`, and variables `SIGNPATH_ORGANIZATION_ID`,
   `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_POLICY_SLUG`.
5. Enable the job in `docs/signpath-job.yml` (copy it into `.github/workflows/`), then move the release
   upload to use the *signed* artifacts that job returns.

Until step 5, releases keep going out unsigned and the workflow says so in the log — a signature is a
trust improvement, not a prerequisite for a working installer.

## If a certificate is ever bought instead

Either a `.pfx` + password, or a thumbprint of a certificate in the runner's store:

```powershell
# .pfx route: base64 the file into a secret, never commit it
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("codesign.pfx"))
# repository secrets: SIGNING_CERT_PFX_B64, SIGNING_CERT_PASSWORD
```

`SIGNING_CERT_THUMBPRINT` covers a cloud/HSM signer (Certum SimplySign, Azure), where the key never
leaves the service. Both are already read by the workflow; no YAML edit is needed.

## What a signature does not do

- It does not stop SmartScreen downloads that have no reputation yet: an OV-signed binary from a new
  publisher still warns until it accumulates. EV and Azure start closer to zero warnings.
- It does not replace the checksum. Keep publishing `sha256` next to the asset so anyone can compare
  what they downloaded (see the release notes).
- It does not touch the repair logic. Signing is packaging only: the same bytes, plus a signature
  blob appended to the PE.
