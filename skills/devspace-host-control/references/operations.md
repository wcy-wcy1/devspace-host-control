# Deployment and pairing

Use only when deployment, configuration, or repair is requested. Creating or packaging this skill does not authorize installation, network changes, startup registration, or control of a live desktop. Once the user authorizes a deployment scope, execute its routine reversible steps without repeatedly asking for permission. Ask for missing addresses at deployment time, not during source/skill development. Never request private keys or passwords in chat.

## Prerequisites and network

Ubuntu desktop is the controller; Windows is the actual controlled desktop. Ubuntu is not a file server mounted into Windows. This release provides Windows amd64 and Ubuntu/Linux amd64/arm64 binaries. The automatic Ubuntu installer targets 22.04/24.04; inspect other versions manually.

Ask for OS/architecture, the two exact Tailscale IPs, allowed workspace directory, session duration, and existing desktop/network setup. Prefer existing SSH access to Ubuntu; use the user's established authentication path. Installation/login of Tailscale and Claude may require the user to authenticate interactively.

Install Tailscale from its official distribution. For Ubuntu, resolve `/etc/os-release` and use the documented repository for that codename. Do not invent package URLs for unsupported releases. Official reference: https://tailscale.com/kb/1031/install-linux . On Windows use the official installer: https://tailscale.com/download/windows . Joining a tailnet alone does not restrict access.

Generate `policy.json` with `scripts/configure.py policy --windows-ip <actual> --ubuntu-ip <actual> --out <new-file>`. It contains exactly Windows→Ubuntu TCP 3389 and Ubuntu→Windows TCP 7443 grants. The file is a policy fragment until reviewed against the complete tailnet policy. Never replace unrelated policy blindly. Remove/restrict conflicting allow-all grants; grants/legacy ACLs are additive. The checker is deliberately conservative and rejects legacy ACLs or additional grants; a rejection means manual review is required, not that unrelated rules may be deleted automatically. It accepts standard JSON, not commented HuJSON, and does not query or deploy live Tailscale policy.

At the Ubuntu host, stage the skill scripts and reviewed full policy. Run the installer without `--apply` to preview. Once authorized, run with `--apply --windows-ip <actual> --ubuntu-ip <actual> --desktop-user <existing-non-root-user> --policy-file <reviewed-json>`. It requires authenticated Tailscale with the matching address, refuses an already active xrdp, masks RDP during installation, installs XFCE/xrdp and official Claude Desktop, binds RDP to VPN IPv4, disables redirection, and adds a dedicated nftables source guard before the usual filter priority. It never closes existing SSH access. Existing xrdp needs a reviewed maintenance procedure rather than this bootstrap path.

Log in over RDP using the existing Ubuntu desktop account. Complete Claude login inside Ubuntu. Verify clipboard, drive and device sharing are unavailable. Linux Claude Desktop feature availability and local-MCP settings must be checked in the installed client; this project does not rely on Linux built-in Computer Use.

## Pairing: only public materials cross devices

1. Copy `assets/runtime/windows-amd64/hostagent.exe` and `hostctl.exe` to a chosen Windows install directory. Use a **new, empty dedicated** credential directory outside workspaces and outside the distribution/skill folder.
2. On Windows: `hostctl.exe init --dir <private-dir> --ip <Windows-VPN-IP>`. This generates `ca.pem`, `ca-key.pem`, `agent.pem`, `agent-key.pem`. Windows-generated private keys are DPAPI-protected for the current user; the directory is ACL-restricted. Run the agent under that same Windows account. Do not transfer the Windows key files to Ubuntu.
3. On Ubuntu: install matching `hostmcp` and `hostctl` binaries with mode 0755. Run as the non-root desktop user: `hostctl request --dir <new-empty-private-dir>`. `client-key.pem` stays on Ubuntu with directory 0700/file 0600; send only `client.csr` to Windows using the authorized transfer path.
4. Windows: `hostctl.exe approve --dir <windows-private-dir> --csr <received-client.csr> --out <new-client.pem>`. Return `client.pem` and public `ca.pem` to Ubuntu. It is unnecessary to transfer `agent-key.pem` or `ca-key.pem`.
5. Read the approved client fingerprint on Windows with `hostctl.exe fingerprint --cert <client.pem>`. Read the server fingerprint with `hostctl.exe fingerprint --cert <agent.pem>`. Verify public fingerprint exchange via the existing authenticated channel. Certificate fingerprints are identity checksums, distinct from browser/device fingerprints.
6. Run `configure.py agent` on Windows with `--credential-dir`, approved **client** `--fingerprint`, existing `--workspace`, actual IPs and `--out <new-agent.json>`. The generated workspace alias is `work`. Agent capabilities default to `files,exec,desktop`; reduce via `--capabilities` if requested. Validate with `hostagent.exe --config <agent.json> --check`.
7. Run `configure.py client` **on Ubuntu** with its local `--credential-dir`, **server** `--fingerprint`, actual IPs and `--out <new-client.json>`. Paths are resolved on the machine running the generator; do not generate Ubuntu credential paths using a Windows process. Keep configuration files within restricted directories.
8. Preview `windows-firewall.ps1` with actual IPs and the installed agent path. Applying requires local elevation. It refuses broad potential TCP allow rules rather than silently editing unrelated settings. It does not enable/change global profile defaults. Verify firewall profiles are enabled with default inbound deny and review any detected conflicts.

CA validity is one year; leaf certificate validity is three months. Plan explicit renewal/re-pairing before expiration. There is no unattended renewal service. Revocation on the local control page writes `.revoked-<fingerprint>` beside the Windows key and remains effective across restarts; for re-pairing generate/approve a new client key/certificate and update the approved fingerprint. Do not delete a revocation marker just to restore an old compromised key.

## Claude MCP configuration and local authorization

Use `configure.py claude --windows-ip <actual> --ubuntu-ip <actual> --client-config <absolute-Ubuntu-client.json> --mcp-binary <absolute-hostmcp> --out <new-fragment.json>`. Merge `mcpServers.my-windows-pc` into the existing Claude Desktop local MCP configuration; preserve other entries and keep a backup. Obtain its actual location from the installed Linux client's settings. This is a stdio server running on Ubuntu, not a cloud connector URL.

Launch `hostagent.exe --config <agent.json>` in the actual logged-in Windows user session, without elevation unless separately needed and authorized. It prints a **private localhost control URL** containing a fragment token. Do not paste this URL into chat, logs or remote configuration. Open it locally and enable a limited session. Default startup is paused; `--enable-for <minutes>` is an explicit alternative local startup authorization, capped by configuration. No startup task or hidden service is installed by the skill.

Restart Claude Desktop if required by its MCP configuration mechanism. Verify tool discovery, then read/write a disposable file in `work`. The adapter writes only MCP JSON to stdout; errors go to stderr. It ignores system HTTP proxy variables for the paired private endpoint and never follows HTTP redirects.

## Test and stop

Follow `verification.md`. Do not assume successful compilation equals successful Claude/Ubuntu/Windows integration. Use test files, a user-approved test window and test pages; do not probe unrelated third-party networks. Native input is only supported on Windows amd64. Each input consumes a fresh frame; focus/move/expiry failures require re-observation rather than blind retry.

Pause cancels outstanding tasks. End the agent with Ctrl+C when done. Revocation is persistent. Closing or pausing cannot undo a click, file upload, external command side effect or already transmitted content.

## Rollback

- Stop agent and adapter, revoke pairing, remove only the named `HostControl-Agent-Private` Windows firewall rule using `windows-firewall.ps1 -Apply -Remove` after authorization.
- Remove only `my-windows-pc` from Claude configuration, preserving other MCP servers.
- For Ubuntu, first close RDP and restore the backed-up xrdp configuration; disable/remove the owned `xrdp.service.d/hostcontrol.conf` and `hostcontrol-rdp-firewall.service` as appropriate. Delete only nft table `inet hostcontrol` after closing the protected listener. Never flush the system ruleset or disable unrelated firewall protection.
- Remove only this deployment's install/configuration directories after checking resolved paths. Keep or securely discard private credentials according to the user's request. Leaving the original DevSpace repository intact is fine.
