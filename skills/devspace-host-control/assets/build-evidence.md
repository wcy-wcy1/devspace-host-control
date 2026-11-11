# Build and verification evidence

Assembled: 2026-11-11T03:53:38+00:00.
Upstream: https://github.com/loft-sh/devspace ; fixed base `8ff6260787edacfa2c0d30d1ff62358d36d482bc`.
Implementation: added independent `hostcontrol/` module, version 0.1.0.
Original tracked DevSpace files were not changed. No upstream DevSpace test-suite result is claimed.

## Build

- Windows amd64 development host; portable Go 1.25.9, no external Go module dependencies.
- `build.ps1 -GoExe <portable-go> -OutputDirectory <runtime>` completed with CGO disabled and `-trimpath -ldflags '-s -w'`.
- Built Windows amd64 hostagent/hostctl, Linux amd64 hostmcp/hostctl, Linux arm64 hostmcp/hostctl.
- Runtime SHA256 and byte size are in `SHA256SUMS.json`; `scripts/verify_bundle.py` checks these and PE/ELF machine architecture.
- These binaries are not publisher-signed; checksums establish consistency with this bundle, not independent publisher identity.

## Checks actually run

- `go test ./... -count=1 -json`: **13 passed, 1 skipped, 0 failed**. Raw events: `verification/go-test.jsonl`.
- `go vet ./...`: passed.
- Real loopback TLS tests covered approved mTLS, wrong source, missing/unapproved client identity and wrong pinned server certificate.
- Tests covered paused/expired sessions, persisted revocation, mutation deduplication, hash conflict, path traversal, Windows directory-junction escape, local control-token/CSRF checks, bounded output and Windows Job Object child-process cancellation.
- PKI tests exercised current-user DPAPI key storage/loading and CSR approval. Generated test credentials remained in temporary directories and are not included.
- Windows INPUT ABI layout and rejection of malformed arguments before side effects passed.
- `python scripts/test_configure.py -v`: four tests passed, including rejection of an additive allow-all policy.
- `bash -n scripts/ubuntu-desktop.sh`: passed; plan mode ran in Git Bash. Windows firewall plan mode completed without mutation.
- Executable PKI/MCP smoke used the packaged Windows hostctl and a Windows hostmcp built from this source: initialize, notification and 18 tool schemas passed; stdout contained only protocol messages. No host endpoint was contacted in this smoke.
- Official Claude apt Release metadata and signing-key URL returned HTTPS 200 at packaging time. This was availability checking, not an Ubuntu installation or independent key-fingerprint verification.
- Skill frontmatter/resource validation and packaged checksums are final assembly checks; see the delivery report.

## Explicitly not verified here

- `TestDesktopCaptureOptIn` was skipped because live desktop access was not requested. No live screenshot, UIA inspection, mouse or keyboard automation was performed for acceptance.
- No Ubuntu execution, XFCE/xrdp/Claude installation, systemd/nftables application, effective tailnet policy change, live firewall change, actual Claude tool discovery or two-host end-to-end test.
- Linux executables were cross-compiled, not run on Linux. Browser/desktop reliability under mixed DPI, multiple displays, UAC and lock screen still needs deployment-time acceptance.
- The bundle provides no anonymity relay, DOM/Playwright integration, UIA element action dispatch, interactive ConPTY or unattended certificate renewal. It does not establish zero fingerprint leakage or full isolation of the real Windows host.

Source inspection corrected repeated EnumWindows callback allocation to use one synchronized callback. Ubuntu bootstrap failure handling stops and persistently masks RDP across reboot; its actual systemd behavior still needs Ubuntu verification.
