---
name: public-repo-safety
description: >-
  Operational guidelines and safety protocols for working in this public open-source repository.
  Use whenever modifying configuration, adding environment variables, committing files, writing tests,
  refactoring code, updating documentation, managing deployments, or handling credentials to ensure
  no secrets, private infrastructure details, or sensitive user data are ever leaked to GitHub.
---

# Public Repository Safety & Open-Source Hygiene Guide

This repository is **public and open-source on GitHub** (`https://github.com/danprav04/realtime-osint.git`).
Every commit, branch, pull request, issue, and file added to this repository is immediately visible to the public.

All agents, pair programmers, and contributors working on this codebase **MUST ALWAYS** act knowing that the repo is public.

---

## 1. Zero-Leak Credential & Secret Management

### Strict Rules
1. **Never hardcode real credentials in tracked files**:
   - No API keys (Google Gemini, Groq Cloud, AIHubMix, OpenAI, Hugging Face, NewsAPI, Reddit).
   - No bot tokens (Telegram Bot API, Discord, Slack).
   - No private keys, certificates, or SSH keys (`*.pem`, `*.key`, `id_rsa*`, `id_ed25519*`).
   - No production database passwords or live secret signing tokens.

2. **Use `.env` for secrets, `.env.example` for documentation**:
   - Active secrets reside **only** in local `.env` files (which are strictly excluded by `.gitignore`).
   - Whenever introducing a new setting or provider credential, add only the variable name and an empty or sanitized mock value (e.g. `GEMINI_API_KEY=`) to `.env.example`.
   - Never commit `.env`, `.env.local`, `.env.production`, or any variant containing real values.

3. **Testing with Mocks**:
   - Automated tests must use mock tokens (e.g. `test-secret-key-12345`, `mock-gemini-key`, `mock-token:12345`).
   - For tests executing against live free-tier endpoints, ensure API keys are retrieved from environment variables and that missing keys or quota limits (HTTP 429) trigger `pytest.skip()` rather than exposing raw tokens in stack traces.

---

## 2. Infrastructure & Network Privacy

1. **No Private Server IP Addresses in Tracked Code or Docs**:
   - Never commit private host IPs, Tailscale VPN IPs (`100.x.y.z`), or cloud VPS public IPs (`129.x.y.z`).
   - For local connections, use `localhost` or `127.0.0.1`.
   - For documentation examples, use standard RFC 5737 reserved documentation ranges (`192.0.2.1`, `198.51.100.1`, `203.0.113.1`) or hostname placeholders (`production`, `osint.example.com`).
   - In deployment scripts (e.g. `deploy.ps1`), allow host targets and Tailscale IPs to be read from `.env` overrides (`DEPLOY_TAILSCALE_IP`, `DEPLOY_REMOTE_HOST`) or CLI parameters.

2. **No Hardcoded Local Absolute Paths**:
   - Never hardcode personal workstation directories (e.g. `C:\Users\<username>\...`).
   - In PowerShell scripts, use `$PSScriptRoot`.
   - In Python, use `pathlib.Path(__file__).resolve().parent`.
   - In Node.js, use `process.cwd()` or `import.meta.url`.

---

## 3. Pre-Commit Verification Runbook

Before staging or committing changes to git, execute the following checklist:

### Step 1: Inspect Git Status
```powershell
git status
```
- Ensure no untracked `.tar`, `.tar.gz`, `.tmp*`, `deploy_status.txt`, `deploy_*.log`, or `.env` files appear in untracked files.
- Verify only intended code and configuration files are modified.

### Step 2: Audit Diff for Accidental Secrets
```powershell
git diff
```
- Scan diffs for any accidental API key pastes, tokens, personal IP addresses, or personal usernames.

### Step 3: Run Fast Automated Quality Gates
```powershell
# Run backend test suite
python tests/run_e2e_tests.py

# Run frontend test suite
npm --prefix frontend test
```
All tests must pass cleanly before publishing.

---

## 4. Remediation Protocol for Accidental Exposure

If a secret or private credential is ever mistakenly committed:
1. **Immediately revoke and rotate the secret** at the provider console (Google AI Studio, Groq Cloud, Telegram BotFather, etc.). Consider the token compromised the instant it touches a public or staged git ref.
2. **Remove the secret from git history**: Use `git rm --cached` if unpushed, or interactive rebase / `git-filter-repo` to purge it from git history before pushing to `origin/main`.
3. **Verify `.gitignore` rules** to prevent recurrence.
