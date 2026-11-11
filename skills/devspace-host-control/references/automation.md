# Autonomous deployment and resume

Use this mode when the user asks to install, automate, deploy in one step, resume,
or repair HostControl. Codex is the executor: the Python helpers provide discovery,
policy transactions and progress tracking; they are not a standalone unattended
installer. Actually execute the installation, pairing and verification with the
available host tools. Do not hand the user a command checklist as a substitute.

## Inputs and questions

Reuse the current session's authorization, server, native/container choice,
workspace and account information. Do not ask again for known answers. Inspect
first: local OS/architecture, installed clients, existing authenticated SSH,
remote `/etc/os-release`, memory/disk, listeners, firewall, existing xrdp and system timezone/NTP state. Reuse a known datacenter region; a current OS timezone is not proof of its location.
Read-only helper:

```text
python scripts/automation.py inspect --server SERVER --ssh-user SSH_USER
```

This uses the established SSH key/agent and strict known-host validation. If an
existing authenticated session uses another mechanism, use that session instead
of treating BatchMode failure as an installation failure. A new host key requires
verification over a trusted channel; never disable host-key checks.

Ask only for missing values or choices that affect the work, grouped in one short
question where possible:

- Which authorized server/SSH account, if not already provided? Use authenticated
  access or a local secure prompt. Never request passwords, API keys, private keys
  or tokens in chat, command arguments, journal files or skill assets.
- Which existing Windows directory may the file tools access? Reuse a previously
  selected directory. Default session duration is 30 minutes; do not require a
  question just to accept that default.
- For CentOS 9, disclose native Claude Desktop is an adaptation, not an officially
  supported distribution. Ask native adaptation versus an Ubuntu controller only
  if the choice is not already authorized. Never reinstall the OS or replace the
  real Windows target without an explicit user request.
- If Tailscale or Claude needs interactive sign-in, open the actual current login
  page and ask the user to sign in. Credentials stay in that browser. Continue
  independent installation work while waiting, then probe actual login status.
- If a change would alter unrelated services or global firewall behavior, prepare
  the exact change and ask about that impact. Ordinary installation, certificate
  exchange, exact-source rules and MCP merge within authorized deployment do not
  require repetitive approval.

Do not embed any server, username, actual IP, password, login link or certificate
from a previous installation into the reusable skill.

## Journal and recovery

Keep the journal and full-policy backups in a user-private deployment directory,
outside the skill, archives and allowed file workspaces. On Windows prefer
`%LOCALAPPDATA%/DevSpaceHostControl/automation/`; on Linux use a mode-0700 directory
below `~/.local/state/hostcontrol/`. Windows private keys remain DPAPI-protected
under the paired user's private credential directory. Do not run the agent as the
administrator account used for installing firewall rules.

Initialize once with actual values:

```text
python scripts/automation.py init --state STATE_FILE --server SERVER --workspace EXISTING_DIRECTORY --desktop-user hostcontrol
python scripts/automation.py next --state STATE_FILE
```

New journals select `text` clipboard by default; `init --clipboard disabled`
records an explicit opt-out. `next` reports the selected mode. Desktop evidence
must contain `clipboard_mode` equal to that choice, plus the returned checks for
clipboard verification, non-text redirection denial and valid Xorg arguments.
Old journals without the new desktop evidence remain readable, but desktop and
its dependent stages require new observations. A changed clipboard choice also
makes mismatched desktop evidence stale. Save/re-record the selected mode and
rerun its dependent acceptance before declaring completion.

Select the confirmed datacenter timezone with `init --timezone IANA_ZONE`, or
`set-timezone --state STATE_FILE --timezone IANA_ZONE` for an existing journal.
There is no Windows/local or IP-geolocation default. The `timezone` evidence must
name that selection; an unselected journal can record it only with confirmed
region evidence. Old journals must complete this new stage before desktop and
its dependents can count as verified. See [timezone.md](timezone.md).

Work on the returned ready stages. Record actual observations with an evidence
JSON containing `observed_at`, `source` (sanitized command or artifact reference)
and `checks` booleans named in `next`. Each required boolean must be true to mark
the stage verified. Use `--status waiting` or `failed` for incomplete work:

```text
python scripts/automation.py record --state STATE_FILE --stage discovery --evidence EVIDENCE_FILE
```

Recording a changed prerequisite invalidates dependent stages. On resume,
re-observe device IPs, policy, listeners and authorization state; journal entries
are history, not fresh live verification. Do not restart an installer on an active
matching desktop, regenerate working credentials, overwrite another MCP server
or blindly repeat a write after a lost response. Inspect and continue from the
first unmet condition. The added launcher stage must also be observed before a legacy journal can mark session/acceptance complete. A renamed/missing stage or failed check must not count as
completion.

## Execution stages

1. **Discovery.** Verify target ownership from user authorization, actual OS and
   architecture, existing services, capacity and chosen workspace. Verify bundle
   integrity with `verify_bundle.py` before executing bundled programs. Choose the
   OS-specific path; do not run apt scripts against CentOS.
2. **Timezone.** Follow [timezone.md](timezone.md): select the confirmed datacenter
   IANA zone, preview/apply it on Linux and read back system timezone/NTP state.
   Check XFCE/Claude overrides and record whether actual browser timezone was
   verified or still needs refresh. Keep the local Windows zone unchanged; never
   default to its timezone or hardcode a previous server's region. Missing location
   is a necessary input, while independent stages may continue. This stage uses
   `timezone_config.py` separately from the desktop bootstrap, also for an existing
   active installation; no installer rerun or forced session restart is needed.
3. **Tailscale.** Install from official packages only if absent. For CentOS Stream
   9 use the official `https://pkgs.tailscale.com/stable/centos/9/tailscale.repo`
   repository with repository/package GPG checking. For Ubuntu follow the actual
   release's documented repository. For Windows use the official installer or
   `winget install --id Tailscale.Tailscale --exact` and let the user complete UAC.
   Enable tailscaled on the server, then use `tailscale up --accept-routes=false
   --accept-dns=false` for a new connection. Do not reset unrelated existing VPN
   settings, advertise routes or select the Windows computer as an exit node.
   Open each current auth link locally, use the same account and recheck both
   `BackendState` values and tailnet membership. Never infer joining from a
   console screenshot alone. Use `tailscale_policy.py discover --server-name
   ACTUAL_NAME` on Windows (or `--server-ip ACTUAL_VPN_IP`) to obtain exact addresses.
4. **Policy.** Execute the complete transaction below, including saved-policy
   readback. Creating a fragment or receiving "saved" is not enough.
5. **Desktop.** Use `ubuntu-desktop.sh` for Ubuntu 22.04/24.04 or the authorized
   `centos-desktop.sh` adaptation for CentOS Stream 9 x86_64. Create a non-root
   desktop user only when absent; reuse an existing intended user. Keep a newly
   generated desktop password outside chat/logs/workspaces and show it only via
   the local secure credential UI. Preview the installer, then apply as part of
   the authorized deployment. Both installers require the exact live-reviewed
   policy and address pair. An existing active xrdp requires inspection/reuse,
   not another bootstrap. Use `desktop_config.py check` to diagnose startup before
   repairing it. Verify binding, source guard, valid Xorg parameters and the selected
   clipboard mode. Text is enabled by default on the server and new client `.rdp`
   files; file/image clipboard, drive/device/audio sharing remain disabled.
   Text mode requires verified xrdp >=0.9.24. Use `desktop_config.py rdp` to generate
   the matching client; save work and log out/reconnect for channel changes.
   Both installers call `tailscale_policy.py audit` on the complete reviewed
   policy; an offline audit alone does not prove it was saved live.
   The CentOS script verifies Anthropic's signing-key fingerprint, InRelease,
   index hash and official package hash before extracting the Debian binary.
   Keep Electron's sandbox enabled; do not work around failures with --no-sandbox
   or by disabling SELinux. Missing dependencies or unsupported systems stop only
   the affected stage and require a concrete diagnosis.
6. **Pairing.** Follow operations.md's CSR flow automatically: install binaries,
   generate keys on their owning machines, transfer only CSR/public certs,
   approve, compare fingerprints, generate configs and run agent `--check`.
   Reuse a verified valid pairing on resume. Never copy Windows CA/agent private
   keys to Linux or Linux client private keys to Windows. `configure.py` accepts
   `--server-ip` as an alias of the historical `--ubuntu-ip` argument.
7. **Firewall.** Inspect complete effective rules and IPv6. Use
   `windows-firewall.ps1 -GuardBroadRules` when exact program-scoped deny rules
   are needed to override an existing generic allow, such as Tailscale's inbound
   rule. This complements the exact allow and leaves unrelated rules intact.
   Disabled profiles require a concrete user decision before passing
   `-EnableDisabledProfiles`; prepare a before-state backup. Never bypass a UAC
   prompt. Do not open public 3389/7443 or close other services' ports. Verify
   resulting effective rules instead of treating exit code zero as proof.
8. **Claude.** Launch the actual GUI as the desktop user, obtain the real config
   path, back up and merge only `mcpServers.my-windows-pc`. Preserve all other MCP
   entries. Complete account login in the user's browser, restart the app if
   needed and observe real tool discovery. Process startup and stdio schema
   output alone do not verify Claude integration.
9. **Launcher.** Follow [launcher.md](launcher.md). Configure the chosen Linux
   user's login/reconnect helper (already part of new desktop installations),
   generate the personalized Windows double-click kit from the existing agent
   config and matching clipboard choice, and expose its CMD entry locally. No
   SSH password, new SSH grant, key or control URL is embedded. Reuse a matching
   agent rather than spawning a duplicate; keep its authorization state unchanged.
   Record the `launcher` stage only after these observations.
10. **Local session.** Use `start-windows-agent.ps1 -Config CONFIG` to isolate
   inherited PowerShell module paths; launch the agent visibly, paused, and
   open its private localhost page only on that Windows computer. Ask for local
   time-limited enable; a remote request cannot authorize itself. Installation
   automation does not mean perpetual desktop-control authorization. Keep the
   private control URL out of output, transcripts, files and remote config.
11. **Acceptance.** Run verification.md's real file roundtrip, harmless exec,
   fresh-frame GUI input, source/identity rejection and stop/pause checks. Start
   with an approved disposable window. Record failures and untested scenarios
   honestly. Stop/pause after the acceptance test unless the user asked to keep a
   live session. Only actual checks can mark the final stage complete.

## One-step policy transaction

This module operates on the **complete current document**, preserving SSH,
groups, tags, hosts, tests and other settings. It replaces only the recognized
default unrestricted grants/legacy ACL when `--replace-default` is supplied and
adds Windows→server TCP 3389 and server→Windows TCP 7443. It preserves provably
disjoint IPv4 business grants. Unknown selectors, overlapping rules, legacy
custom ACLs, automatic route approvals and possible IPv6 bypasses require review;
they are never silently deleted. Narrow conflicting rules only after resolving
their intended use with the user, then create a new plan.

Replacing default allow-all can affect other tailnet members. Inspect the device
list and existing uses first. For a new two-device deployment where only these
flows were requested, that restriction is already within scope. Otherwise ask
about the broader effect before replacing that default.

Prefer an existing authenticated admin browser, or an already available scoped
API authorization. Do not require a new API key if browser access works. If the
user chooses API access, have them enter a short-lived policy token in a **local
interactive terminal** using `--prompt-token`, or use an existing secure runtime
environment variable `HOSTCONTROL_TS_TOKEN`; never ask them to paste it in chat.
API requires `policy_file:read` and `policy_file` scopes. Do not store this token.

One-command API route (the directory must be a new private per-deployment path):

```text
python scripts/tailscale_policy.py sync --server-name ACTUAL_NAME --replace-default --out-dir PLAN_DIRECTORY
```

`sync` discovers the current tailnet and both exact IPv4 addresses, checks the selected server is online,
fetches the complete policy, saves its backup, plans, validates, conditionally
applies and reads back the saved document. An explicitly supplied mismatched
`--tailnet` is rejected. Supply `--prompt-token` only in a local
interactive terminal if an existing secure token environment is unavailable.
Omit `--replace-default` if replacing that default has not been authorized.

The same transaction can be split when a review/browser handoff is needed (all
paths are actual per-deployment paths; plan directories must be new):

```text
python scripts/tailscale_policy.py fetch --tailnet TAILNET --out CURRENT_FILE
python scripts/tailscale_policy.py plan --current CURRENT_FILE --windows-ip WINDOWS_IP --server-ip SERVER_IP --replace-default --out-dir PLAN_DIRECTORY
python scripts/tailscale_policy.py apply --tailnet TAILNET --plan-dir PLAN_DIRECTORY
```

`apply` fetches current policy, refuses changes since planning, validates against
the server, writes once with the current ETag/If-Match and fetches the saved
document again. Running it again with the same policy performs no write. HTTP
412 or an unknown write outcome is not retried. Re-fetch and inspect first.
Errors/warnings from policy validation block the write. An unavailable API uses
the authenticated browser route; it never falls back to a broader policy.

Browser route: use available browser/computer-use tools and their skill to read
the complete admin JSON editor, save it as CURRENT_FILE, run `plan`, insert the
entire generated `policy.json` **including outer braces**, save and re-open/read
the editor. Confirm the original full document has not changed immediately
before saving; refresh/re-plan if it did. Export the saved complete document as
LIVE_FILE and verify:

```text
python scripts/tailscale_policy.py verify --current LIVE_FILE --plan-dir PLAN_DIRECTORY
```

If browser automation is unavailable, give the user the full generated file and
ask for exactly one save, then acquire the actual saved policy through the API
or full editor export. Do not repeatedly ask for the same fragment or interpret
"saved" as effective-policy evidence. Policy readback verifies control-plane
configuration; still test the live RDP/agent access and unintended sources.

Public versus private: these grants govern Tailscale traffic. They do not open
public ports, modify cloud security groups or change ordinary public-IP SSH 22.
Do not add private-network SSH 22 unless requested. Existing SSH policy settings
are preserved, but SSH identity rules alone do not grant network port 22.

Before-state `original.hujson` and semantic hashes are retained per plan. A
rollback must be explicit, scoped, and conditional on the current policy still
matching this deployment's last write; do not automatically restore a broader
default or overwrite somebody else's intervening update.

API reference verified against the provider's published OpenAPI:
https://api.tailscale.com/api/v2?outputOpenapiSchema=true

## Local validation

```text
python -m unittest discover -s scripts -p 'test_*.py' -v
python scripts/verify_bundle.py
```

These offline checks cover policy parsing, preservation, conflict rejection,
conditional writes, no retry after unknown outcomes, idempotency, readback and
resume dependencies. They do not prove a live deployment, API permissions,
CentOS GUI compatibility, an elevated Windows firewall apply or Claude login.
