# Deployment and pairing

For automatic installation or resume, start with [automation.md](automation.md).
Codex executes ready stages using existing authenticated access, asks only for
missing inputs or interactive authorization, and verifies actual results.
`configure.py` accepts `--server-ip` as an alias for `--ubuntu-ip`;
`windows-firewall.ps1` accepts `-ServerIP` as an alias for `-UbuntuIP`.

Use only when deployment, configuration, or repair is requested. Creating or packaging this skill does not authorize installation, network changes, startup registration, or control of a live desktop. Once the user authorizes a deployment scope, execute its routine reversible steps without repeatedly asking for permission. Ask for missing addresses at deployment time, not during source/skill development. Never request private keys or passwords in chat.

## Prerequisites and network

Reuse the current session's server, desktop user, workspace and OS choice. Inspect
the installed state before installing again. An existing deployment normally
needs scoped configuration/repair, not another bootstrap or new certificates.
Troubleshooting and text clipboard steps are in [troubleshooting.md](troubleshooting.md).

Ubuntu desktop is the controller; Windows is the actual controlled desktop. Ubuntu is not a file server mounted into Windows. This release provides Windows amd64 and Ubuntu/Linux amd64/arm64 binaries. The automatic Ubuntu installer targets 22.04/24.04; inspect other versions manually.

Discover OS/architecture, exact Tailscale IPs and existing services through authenticated tools. Reuse known workspace choices and use the 30-minute default unless the user specifies otherwise. Ask only for necessary values that cannot be discovered. Tailscale/Claude login and system authorization may require interactive action.

Install Tailscale from its official distribution. For Ubuntu, resolve `/etc/os-release` and use the documented repository for that codename. Do not invent package URLs for unsupported releases. Official reference: https://tailscale.com/kb/1031/install-linux . On Windows use the official installer: https://tailscale.com/download/windows . Joining a tailnet alone does not restrict access.

Use `tailscale_policy.py` as described in [automation.md](automation.md) to read the full JSON/HuJSON policy, plan exact grants, preserve unrelated metadata and provably disjoint business grants, conditionally save once and verify readback. Both installers use its read-only `audit` command on the reviewed full policy. Unsupported or overlapping rules stop deployment for review; no unrelated rule is deleted automatically. The historical `configure.py policy` command only generates a fragment, and its `check-policy` command deliberately accepts only two grants; these are not substitutes for the full transaction or live readback.

At the Ubuntu host, stage the skill scripts and reviewed full policy. Run the installer without `--apply` to preview. Once authorized, run with `--apply --windows-ip <actual> --ubuntu-ip <actual> --desktop-user <existing-non-root-user> --policy-file <reviewed-json>`. It requires authenticated Tailscale with the matching address, refuses an already active xrdp, masks RDP during installation, installs XFCE/xrdp and official Claude Desktop, binds RDP to VPN IPv4, enables text-only clipboard while disabling drive/device redirection, and adds a dedicated nftables source guard before the usual filter priority. It never closes existing SSH access. Existing xrdp needs a reviewed maintenance procedure rather than this bootstrap path.

Log in over RDP using the existing Ubuntu desktop account. Complete Claude login inside Ubuntu. Verify text copy/paste works in both directions and clipboard file/image, drive and device sharing are unavailable. Linux Claude Desktop feature availability and local-MCP settings must be checked in the installed client; this project does not rely on Linux built-in Computer Use.

For an explicitly selected CentOS Stream 9 x86_64 host, use
`scripts/centos-desktop.sh` with `--windows-ip`, `--server-ip`, `--desktop-user`
and `--policy-file`. It is a compatibility adaptation using the official Debian
package, verified with Anthropic's signing-key fingerprint, signed index and
package SHA256. Run preview first; `--apply` refuses an already active xrdp.
Install/login Tailscale using its official CentOS repository before this step.
The installer includes `gnome-keyring-pam`, `xdg-utils` and the EL9 CJK font
package, prepares user-owned config parents, and configures PAM/XFCE keyring
startup. Never substitute the SSH root password for dedicated desktop credentials.

Both desktop installers accept `--clipboard disabled|text`, defaulting to
`text`. Text mode requires verified xrdp >=0.9.24. Both installers
keep clipboard file/image transfer blocked, and generate a client file with the
same choice via `desktop_config.py rdp`. During acceptance, verify redirection
against the chosen mode rather than always expecting clipboard denial.

## Pairing: only public materials cross devices

1. Copy `assets/runtime/windows-amd64/hostagent.exe` and `hostctl.exe` to a chosen Windows install directory. Use a **new, empty dedicated** credential directory outside workspaces and outside the distribution/skill folder.
2. On Windows: `hostctl.exe init --dir <private-dir> --ip <Windows-VPN-IP>`. This generates `ca.pem`, `ca-key.pem`, `agent.pem`, `agent-key.pem`. Windows-generated private keys are DPAPI-protected for the current user; the directory is ACL-restricted. Run the agent under that same Windows account. Do not transfer the Windows key files to Ubuntu.
3. On Ubuntu: install matching `hostmcp` and `hostctl` binaries with mode 0755. Run as the non-root desktop user: `hostctl request --dir <new-empty-private-dir>`. `client-key.pem` stays on Ubuntu with directory 0700/file 0600; send only `client.csr` to Windows using the authorized transfer path.
4. Windows: `hostctl.exe approve --dir <windows-private-dir> --csr <received-client.csr> --out <new-client.pem>`. Return `client.pem` and public `ca.pem` to Ubuntu. It is unnecessary to transfer `agent-key.pem` or `ca-key.pem`.
5. Read the approved client fingerprint on Windows with `hostctl.exe fingerprint --cert <client.pem>`. Read the server fingerprint with `hostctl.exe fingerprint --cert <agent.pem>`. Verify public fingerprint exchange via the existing authenticated channel. Certificate fingerprints are identity checksums, distinct from browser/device fingerprints.
6. Run `configure.py agent` on Windows with `--credential-dir`, approved **client** `--fingerprint`, existing `--workspace`, actual IPs and `--out <new-agent.json>`. The generated workspace alias is `work`. Agent capabilities default to `files,exec,desktop`; reduce via `--capabilities` if requested. Validate with `hostagent.exe --config <agent.json> --check`.
7. Run `configure.py client` **on Ubuntu** with its local `--credential-dir`, **server** `--fingerprint`, actual IPs and `--out <new-client.json>`. Paths are resolved on the machine running the generator; do not generate Ubuntu credential paths using a Windows process. Keep configuration files within restricted directories.
8. Preview `windows-firewall.ps1` with actual IPs and the installed agent path. Applying requires local elevation. By default broad potential TCP allow rules stop application. In an authorized exact-source deployment, `-GuardBroadRules` adds program-scoped block rules for other IPv4 sources and IPv6 without deleting unrelated allows. Default inbound actions remain unchanged. Disabled profiles stop application unless the user has accepted the concrete impact and `-EnableDisabledProfiles` is explicitly supplied; save a before-state backup first. Verify effective firewall profiles and rules after applying.

CA validity is one year; leaf certificate validity is three months. Plan explicit renewal/re-pairing before expiration. There is no unattended renewal service. Revocation on the local control page writes `.revoked-<fingerprint>` beside the Windows key and remains effective across restarts; for re-pairing generate/approve a new client key/certificate and update the approved fingerprint. Do not delete a revocation marker just to restore an old compromised key.

## Claude MCP configuration and local authorization

Use `configure.py claude --windows-ip <actual> --ubuntu-ip <actual> --client-config <absolute-Ubuntu-client.json> --mcp-binary <absolute-hostmcp> --out <new-fragment.json>`. Merge `mcpServers.my-windows-pc` into the existing Claude Desktop local MCP configuration; preserve other entries and keep a backup. Obtain its actual location from the installed Linux client's settings. This is a stdio server running on Ubuntu, not a cloud connector URL.

Launch `hostagent.exe --config <agent.json>` in the actual logged-in Windows user session, without elevation unless separately needed and authorized. It prints a **private localhost control URL** containing a fragment token. Do not paste this URL into chat, logs or remote configuration. Open it locally and enable a limited session. Default startup is paused; `--enable-for <minutes>` is an explicit alternative local startup authorization, capped by configuration. No startup task or hidden service is installed by the skill.

Prefer `scripts/start-windows-agent.ps1 -Config <agent.json>` for a visible
foreground launch from PowerShell 7 or Windows PowerShell. It avoids passing
PowerShell 7's module paths to Windows PowerShell UIA children and restores the
environment on exit. It remains paused unless a locally authorized invocation
explicitly supplies `-EnableForMinutes`. Use current status to confirm enable;
a successful listener or user reply alone does not prove a live session.

Restart Claude Desktop if required by its MCP configuration mechanism. Verify tool discovery, then read/write a disposable file in `work`. The adapter writes only MCP JSON to stdout; errors go to stderr. It ignores system HTTP proxy variables for the paired private endpoint and never follows HTTP redirects.

## Double-click startup

After configuration, follow [launcher.md](launcher.md) to generate the Windows
entry that launches/reuses the visible agent, opens RDP and starts Claude on
Linux login/reconnect. Both installers prepare the Linux hook; existing
installations can apply it without restarting RDP. Keep credentials outside the
kit and leave local control paused until explicitly enabled.

## Test and stop

Follow `verification.md`. Do not assume successful compilation equals successful Claude/Ubuntu/Windows integration. Use test files, a user-approved test window and test pages; do not probe unrelated third-party networks. Native input is only supported on Windows amd64. Each input consumes a fresh frame; focus/move/expiry failures require re-observation rather than blind retry.

Pause cancels outstanding tasks. End the agent with Ctrl+C when done. Revocation is persistent. Closing or pausing cannot undo a click, file upload, external command side effect or already transmitted content.

## Rollback

- Stop agent and adapter, revoke pairing, remove only the three owned `HostControl-Agent-*` Windows firewall rules using `windows-firewall.ps1 -Apply -Remove` after authorization. If profiles were explicitly enabled, use the reviewed before-state backup to restore only that deployment's changes; do not disable protection blindly.
- Remove only `my-windows-pc` from Claude configuration, preserving other MCP servers.
- For Ubuntu, first close RDP and restore the backed-up xrdp configuration; disable/remove the owned `xrdp.service.d/hostcontrol.conf` and `hostcontrol-rdp-firewall.service` as appropriate. Delete only nft table `inet hostcontrol` after closing the protected listener. Never flush the system ruleset or disable unrelated firewall protection.
- Remove only this deployment's install/configuration directories after checking resolved paths. Keep or securely discard private credentials according to the user's request. Leaving the original DevSpace repository intact is fine.
