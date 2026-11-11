# Deployment troubleshooting

## Check before repairing

Run the read-only classifier on the Linux server:

```bash
python3 /path/to/skill/scripts/desktop_config.py check \
  --sesman-log /var/log/xrdp-sesman.log
```

It checks the configured Xorg executable/argument list and classifies the latest
login attempt without printing raw logs, clipboard contents or credentials.
It does not claim that a live desktop works. Also check `systemctl is-active xrdp
xrdp-sesman`, the VPN listener with `ss -lnt`, free memory and disk, and the
user's `.xorgxrdp.*.log` locally when needed. Preserve existing service users.

| Symptom | Meaning | Recovery |
| --- | --- | --- |
| `User does not exist, or could not be authenticated` | PAM/login credentials failed | Use the dedicated non-root desktop account, not the SSH root password. Check username, keyboard layout and PAM locally. Do not ask for passwords in chat. |
| Login succeeded, then `X server could not be started` | Authentication worked; graphical startup failed | Inspect the ordered `[Xorg]` `param=` lines and Xorg log. If the executable is a log filename, restore the full section from a known-good backup. |
| Claude cannot get its `userData` path | Wrong HOME/config ownership or session environment | Run as the desktop user with its real HOME/XDG runtime and owned `.config`, `.local`, `.local/share`, `.cache` parents. Do not launch as root or disable Electron's sandbox. |
| Unlock Login Keyring | Session keyring is locked or multiple daemons conflict | Use the dedicated desktop password locally. On CentOS install `gnome-keyring-pam`, keep auth as `substack password-auth` before optional keyring auth, and start XFCE through the prepared session. Do not make the keyring password empty or copy the password into scripts. |
| Cannot paste Windows text into Claude | RDP clipboard disabled at one or both ends, or old session retained | Use the default text mode below, generate a matching client file, save work and **Log Out** of XFCE before reconnecting. Closing the RDP window alone retains the old channel process. |
| System/browser timezone differs from selected datacenter | OS image default, clock/TZ override or cached Chromium zone | Follow [timezone.md](timezone.md), use the confirmed IANA region, read back timezone/NTP and refresh the test page. Reopen Claude after saving work if stale. Do not copy Windows time or infer location from the old OS setting. |
| MCP says `local session is paused, expired or revoked` | Local authorization is not active | Check the current local control page. Enable a new bounded session locally if intended. A remote request cannot enable itself; restart creates a new private control URL. |
| Claude cannot find `my-windows-pc` | Wrong config path, old GUI process, or adapter startup failure | Inspect actual client settings, preserve other MCP entries, restart Claude, then verify actual tools in Claude. A standalone `tools/list` is only an adapter check. |
| `Windows denied focus` | Windows foreground protection refused focus | Ask the user to click the approved test window locally. Reobserve and capture after focus; do not bypass lock/UAC protection. |
| `frame missing or expired` / window lost focus | The 30-second frame is no longer valid | Capture a new frame and consume it promptly. Every actual input consumes one frame. Reobserve unknown outcomes before deciding whether another input is appropriate. |

## Text-only clipboard (default)

Bootstrap and generated client files enable text copy/paste by default. Preview
the server configuration before applying:

```bash
python3 /path/to/skill/scripts/desktop_config.py configure --clipboard text
```

Then apply the reviewed changes with the same command plus `--apply`. It verifies
the installed xrdp version supports typed restrictions (conservative minimum
0.9.24), preserves all Xorg/Xvnc argument lines and unrelated settings, creates
backups and replaces changed files atomically. Ambiguous targeted keys/sections
or damaged Xorg parameters stop the write. It never restarts a service or logs
out a user. If applying the second file fails, it attempts to roll back only its
own first write; a conflicting external edit stops rollback for manual review.

Generate a **new** client file on Windows with actual addresses/user:

```powershell
python /path/to/skill/scripts/desktop_config.py rdp `
  --server-ip 100.80.0.2 --desktop-user worker --clipboard text `
  --out server-text.rdp --apply
```

The examples use placeholder VPN addresses. The server permits clipboard text
in both directions, with `RestrictInboundClipboard=file,image` and
`RestrictOutboundClipboard=file,image`. Drive/device/audio redirection stays
disabled in both configurations. Images/files require a separate user choice;
this helper does not provide an unrestricted mode. Text copied into the remote
desktop can be pasted/read there; this is an intentional change in data sharing.

Older stock xrdp packages (including some Ubuntu 22.04 installations) must be
upgraded through a trusted distribution source for text mode; unsupported versions
are rejected rather than silently allowing clipboard files/images. Choose
`--clipboard disabled` explicitly if retaining such a package.

Save work, **Log Out** from XFCE, then open the new `.rdp` file. Test a harmless
marker in both directions. Test file/image rejection separately. Never inspect
or log the user's existing clipboard as a health check. To turn it off, use the
disabled mode and a newly generated disabled client file, then log out/reconnect.

## Repeated INI arguments

`sesman.ini` is not safely serializable with a last-key-wins INI writer. These
eight lines must remain eight ordered arguments:

```ini
[Xorg]
param=/usr/libexec/Xorg
param=-config
param=xrdp/xorg.conf
param=-noreset
param=-nolisten
param=tcp
param=-logfile
param=.xorgxrdp.%s.log
```

Paths differ by distribution. Never replace a working path with this example
without checking the installed package. `desktop_config.py` edits only named
settings, preserves comments/line endings and does not guess missing arguments.

## Verify Claude can access Windows

In the **Linux Claude Desktop** chat, request actual MCP calls:

```text
Use my-windows-pc: call host_status, then create claude-test.txt in workspace
work with the text "Claude connection test". Read it back and report the real
tool result. If a call fails, show the error; do not simulate success.
```

Confirm the file in the configured Windows workspace. File API reads support
UTF-8 text up to 1 MiB; PDF/Word/image parsing or full binary download is a
different operation. Arbitrary execution/GUI still has current-user permissions,
so the workspace alias is not a sandbox. For desktop acceptance, use a disposable
window; confirm screenshot and focus before any click or text input. Record GUI
checks as incomplete if Windows protection prevents them.

## Visible Windows agent launcher

Run `scripts/start-windows-agent.ps1 -Config <private-agent.json>` as the paired
Windows user. It validates config, runs visibly in the foreground, starts paused
and lets Windows PowerShell UIA children resolve their own module paths even
when launched from PowerShell 7. It does not install persistence, record the
control URL or request elevation. Only an explicitly authorized local invocation
may use `-EnableForMinutes 30`, capped by the config's session limit. Ctrl+C stops
the agent. The RDP password, Tailscale account, Claude account and local-control
token are distinct credentials; never reuse the root password as the desktop
account's password or place credentials in a `.rdp` file.
