# Chess Trainer operational notes

- The Chromium extension connects to the lightweight desktop bridge at `127.0.0.1:8765`; it cannot launch the desktop process itself.
- `scripts\install_shortcuts.ps1 -EnableAutoStart` installs the Start menu shortcut and the `ChessTrainerControlCentre` logon task. PowerShell script failures propagate as terminating errors, so do not infer success or failure from `$LASTEXITCODE` after invoking `install_autostart.ps1`.
- Keep LC0 request-started: the bridge may run from sign-in, but engine processes should exist only while a supported chess tab is actively using analysis and must stop on pause or loss of the final supported tab.
