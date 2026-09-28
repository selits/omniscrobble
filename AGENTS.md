# Agent Guidelines & Instructions

All autonomous and pair-programming AI agents (Gemini, Claude, Copilot, Cursor, Codex, etc.) working on this repository must strictly adhere to the project rules and security boundaries documented in:

- [**`GEMINI.md`**](./GEMINI.md)

### Key Rules Summary (CRITICAL)

1. **Git Author Identity:**
   - Every commit must use the GitHub privacy email:
     `selits <selits@users.noreply.github.com>`
   - Never commit using a personal email address.
2. **Explicit Confirmation Required for Commits & Pushes:**
   - NEVER execute `git commit` or `git push` autonomously without explicit user confirmation.
   - Always present the proposed commit message, diff summary, and changed files to the user and wait for approval.
3. **No Direct Commits to Main:**
   - Always develop in dedicated feature or fix branches (`feature/...`, `fix/...`, `docs/...`).
4. **Scrub Personal Infrastructure:**
   - Never commit real seedbox hostnames (`*.usbx.me`), high-range ports, passwords, or personal credentials.
   - Always use generic placeholders: `<servername>.usbx.me`, `<PORT>`, `your_trakt_client_id_here`.
5. **Quality Gates:**
   - Always run unit tests with `.venv/bin/pytest`. All tests must pass with 0 failures before proposing any commit.
