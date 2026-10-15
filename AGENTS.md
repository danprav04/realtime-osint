# Project Rules: Real-Time OSINT Gathering Service

## Public Repository Mindset
This repository is **public and open-source on GitHub** (`https://github.com/danprav04/realtime-osint.git`).
All agents, subagents, and contributors working on this codebase **MUST ALWAYS** act knowing that the repo is public.

### Non-Negotiable Safety Protocols
1. **Never Hardcode Secrets**: Never insert API keys, bot tokens, personal passwords, or private encryption keys into any tracked file. Use environment variables with `.env` (gitignored) and `.env.example` templates with empty/mock values.
2. **Never Commit Private Infrastructure**: Never expose raw VPS public IP addresses, Tailscale IPs, personal workstation file paths, or private host ports in committed files. Use environment variables (`DEPLOY_TAILSCALE_IP`, `DEPLOY_REMOTE_HOST`), `$PSScriptRoot`, or documentation placeholders.
3. **Verify Before Staging**: Always verify `git status` and `git diff` before committing. Ensure temporary deployment archives (`*.tar`, `*.tar.gz`), log files, and test caches are excluded by `.gitignore`.
4. **Preserve Test Integrity**: Ensure all 96 E2E tests (`python tests/run_e2e_tests.py`), backend unit/integration tests, and frontend tests (`npm test` in `frontend/`) continue to pass 100%.

For full guidelines and checklists, see the project skill:
[`.agents/skills/public-repo-safety/SKILL.md`](.agents/skills/public-repo-safety/SKILL.md)
