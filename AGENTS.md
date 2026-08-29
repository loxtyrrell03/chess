# Chess Trainer operational notes

## Milestone documentation

- Agents must update this `AGENTS.md` after every meaningful, verified milestone and include that update in the same milestone commit.
- Record concise, durable context: important behavior or architecture changes, decisions and their rationale, relevant tests or verification, deployment or runtime state, and material limitations or follow-up work.
- Update or replace stale guidance instead of accumulating contradictory history; keep notes factual and useful to future agents.
- Do not record secrets, credentials, personal data, raw transcripts, routine command logs, or transient debugging noise.


- The Chromium extension connects to the lightweight desktop bridge at `127.0.0.1:8765`; it cannot launch the desktop process itself.
- `scripts\install_shortcuts.ps1 -EnableAutoStart` installs the Start menu shortcut and the `ChessTrainerControlCentre` logon task. PowerShell script failures propagate as terminating errors, so do not infer success or failure from `$LASTEXITCODE` after invoking `install_autostart.ps1`.
- Keep LC0 request-started: the bridge may run from sign-in, but engine processes should exist only while a supported chess tab is actively using analysis and must stop on pause or loss of the final supported tab.
