# Double-click start after configuration

After pairing and Claude login, generate a Windows launch kit once. On later
uses, double-click `Start-Claude.cmd`: it reuses a matching running agent or
opens a visible paused agent console, then opens the saved RDP connection.
Claude starts in the intended Linux user's XFCE session on login or reconnect.
Existing Claude processes are reused; no Windows startup task or service is
installed. An unavailable VPN, a changed listener or a different process at the
agent endpoint stops startup with a visible error.

## Prepare once

The desktop installers configure the Linux login/reconnect helper. For an
existing deployment, preview and apply the scoped configuration separately:

```text
python3 scripts/launcher.py linux --desktop-user DESKTOP_USER
python3 scripts/launcher.py linux --desktop-user DESKTOP_USER --apply
```

This writes two user-owned files:
`~/.local/bin/hostcontrol-start-claude.py` and
`~/.config/autostart/hostcontrol-claude.desktop`. It adds a marked block for only
that username immediately after the installed reconnect script's shebang,
preserving the upstream body and its existing exit/exec commands. The script is
located through `sesman.ini`'s `ReconnectScript` (default `reconnectwm.sh`). If the configured default `reconnectwm.sh` is missing, it creates only that
default file with the hook and an otherwise empty body. An
unknown shell script, wrong ownership, symlink or damaged marker is rejected.
Existing changed files are backed up. It neither restarts RDP nor logs anyone
out. The helper uses only the intended user's XFCE session environment, keeps
Electron's sandbox enabled and never launches Claude as root.

On Windows, use a new launcher directory under the existing per-user install:

```text
python scripts/launcher.py generate --agent-config PRIVATE_AGENT_JSON --agent-exe INSTALLED_AGENT_EXE --server-ip SERVER_VPN_IP --desktop-user DESKTOP_USER --out-dir NEW_LAUNCH_DIRECTORY
```

Default `--clipboard text` must match the server. Use `--clipboard disabled`
only for a deployment configured that way. The new directory contains the CMD
entry, PowerShell wrappers, `launch.json` and a credential-free `.rdp`. It refers
to the original agent config/executable by path; it does not copy credentials,
private keys, passwords or control URLs. Do not move/delete those originals.
Personalized launch kits contain device settings and must stay outside public
project/Skill archives. Windows PowerShell 5.1 is used by the CMD; inherited
PowerShell 7 module paths are cleared within that process only.

## Each later start

1. Double-click `Start-Claude.cmd`; Tailscale must be signed in and reachable.
2. Complete normal RDP login if prompted. Passwords are not embedded. Existing
   Windows Credential Manager choices remain under the user's control.
3. XFCE login or xrdp reconnect starts Claude, unless it is already running.
   Claude may still require account login after its own credentials expire.
4. Enable a bounded control session on the agent's private local page when
   computer control is wanted. Startup does not silently re-enable access.

Closing RDP disconnects the view and may leave the Linux session running.
Pause/close the visible Windows agent console to stop computer control. Closing
the CMD launcher alone does not terminate the separate agent console.

## Verify and undo

Check new login and reconnect separately with Claude first closed after saving
work. Confirm the right user's Claude window opens, a second launch reuses the
agent, text paste works and local session state is still paused/unchanged.
Offline tests mock Windows processes and Linux `/proc`; they do not establish
live RDP reconnection or successful GUI/account login.

To undo only this startup feature, remove the two named user files and that
user's `BEGIN/END HOSTCONTROL CLAUDE` block from the reconnect script after
reviewing the diff. Keep upstream content and other users' blocks. Restore a
backup only if no intervening edits would be lost. Remove the personalized
Windows launch directory/CMD entry; do not remove pairing credentials or
firewall rules just to remove a launcher.
