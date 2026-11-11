# HostControl

Independent host-control module added to DevSpace. Ubuntu Claude Desktop starts
`hostmcp` over MCP stdio; the adapter calls a Windows `hostagent` over pinned,
mutually authenticated TLS 1.3 HTTPS. There are no third-party Go dependencies.
This is a host mode, not a Kubernetes deployment or an existing DevSpace MCP API.

## Build and verify

Use Go 1.25 or newer:

```text
go test ./... -count=1
go vet ./...
```

`build.ps1` cross-compiles Windows amd64 `hostagent`/`hostctl` and Linux
amd64/arm64 `hostmcp`/`hostctl`. `-GoExe` accepts a portable Go executable.
Tests include Windows Job Object child-process cancellation, real mTLS,
workspace junction escape rejection, persistent revocation, output bounds,
MCP messages, local-control authorization and argument validation.

## Components

- `hostctl init --dir <new-empty-directory> --ip <Windows-Tailscale-IP>`:
  Windows-side CA and server identity. Generated Windows keys use current-user
  DPAPI and restrictive directory ACLs.
- `hostctl request --dir <new-empty-directory>`: Ubuntu generates its private
  key and public CSR. Export only the CSR for approval.
- `hostctl approve --dir <CA-directory> --csr <request> --out <new-cert>`:
  sign the public request; return only the client certificate and public CA.
- `hostctl fingerprint --cert <certificate>`: display public identity checksum.
- `hostagent --config <agent.json> --check`: validate exact VPN binding,
  approved identity, capabilities and existing workspaces without listening.
- `hostagent --config <agent.json>`: run in the actual Windows user session.
  Initially paused; open the private localhost control URL to enable a bounded
  session, pause or persistently revoke pairing. Ctrl+C stops the process.
- `hostmcp --config <client.json>`: Claude Desktop stdio entrypoint on Ubuntu.
  stdout is MCP only, diagnostic errors are stderr. No HTTP proxy or redirects.

Configuration generators, deployment scripts, complete workflow, integrity
manifest and verification records are packaged in the accompanying
`devspace-host-control` skill. The default generated workspace alias is `work`.
Credentials must stay outside workspaces and distributable source/skill assets.

## Implemented tools

`host_status`, `session_info`, `fs_list`, `fs_read`, `fs_write`, `fs_move`,
`exec_start`, `exec_poll`, `exec_cancel`, `desktop_windows`, `desktop_inspect`,
`desktop_focus`, `desktop_capture`, `desktop_click`, `desktop_type`,
`desktop_key`, `desktop_scroll`, `browser_open`.

Each desktop input consumes a fresh, 30-second, window-bound frame. Screenshots
are visible screen regions and may include overlapping windows. UI Automation
inspection uses the system Windows PowerShell and framework UIA libraries,
bounded to 500 elements and eight seconds; it does not require a .NET SDK.
Browser operation uses the real default browser and desktop tools. DOM automation,
interactive ConPTY, automatic certificate renewal and an anonymity relay are not
implemented.

## Boundaries

Real desktop and arbitrary program execution run as the current Windows user,
not in a sandbox. Workspace restrictions apply to file APIs only. Lock/UAC and
permission failures are not bypassed. The application does not hide local public
endpoints from Tailscale peers, automatically redact every secret, or guarantee
anonymous use. No startup task, remote session-enabling tool or hidden service is
installed. Pairing and network policy still require deployment-time verification.

Mutation deduplication lasts within the running process, is bounded to 2048
records retained up to eight hours, and is not durable across restart. Large
responses are not retained for replay. Do not retry unknown side effects.
File hash checks reduce accidental overwrites but are not a cross-process
compare-and-swap transaction; concurrent external writers can race the final
check. The GUI controller can also interact with applications accessible to the
authorized desktop user; the localhost panel is not a separate security boundary
against such a controller.

Upstream base: DevSpace commit
`8ff6260787edacfa2c0d30d1ff62358d36d482bc`, Apache-2.0. This module uses its
repository/workflow context and command/synchronization architecture as the
starting point; it does not import the Kubernetes helper's insecure stream
transport or its Linux SSH shell path as a public host service.
