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

## Files-only policy update — 2026-11-17

The preceding section records the original assembly, not this update's tool
discovery contract. Runtime binaries were rebuilt with portable Go 1.25.9 for
Windows amd64 and Linux amd64/arm64. Authenticated live policy now determines
advertised tools. Offline/old peers no longer produce the full 18-tool catalog.

Verified on Windows for this update:

- `go test ./... -count=1` and `go vet ./...` passed. The actual selected
  files-only config was also tested through the opt-in fixture harness under
  the approved directory, with no production listener/session authorization.
- New tests cover paused policy discovery, failed/old/malformed policy refusal,
  mixed workspace permissions, metadata filtering, direct readonly mutation
  rejection, stale mutation IDs, local-only mode changes, pause on mode save,
  restart persistence and external configuration edit refusal.
- Real loopback mTLS tests include authenticated files-only discovery while
  paused, alongside identity and source rejection checks.
- All 78 skill Python tests passed. The control-page JavaScript passed
  `node --check`; this is syntax checking, not visual browser acceptance.
- Live desktop screenshot/input acceptance was not requested for this update.
  No additional VM, proxy, routing or GPU changes were made.

Production deployment and actual Claude UI discovery are recorded separately in
the task's delivery report; building/testing alone does not establish those.

## Continuous draft synchronization — 2026-11-17

- All 89 skill Python tests passed on Windows, including real PowerShell/Python
  version-2 packet compatibility, revision/base validation, IME/debounce gating,
  latest-value coalescing, ownership conflicts, timeout/restart refusal and
  exact editor-focus checks. The 22 bridge tests also ran on CentOS (two
  Windows-only tests skipped).
- The deployed Windows client and CentOS receiver exchanged authenticated v2
  packets over the existing RDP clipboard, updating an actual Claude draft with
  Chinese, multiline text, emoji, replacement and clearing. Four acknowledgements
  took 3.015, 3.028, 2.938 and 3.270 seconds. Messages remained unsent.
- A WPF event harness ran the actual panel callbacks and live clipboard transport:
  edits during the first in-flight request coalesced into one newest revision,
  followed by replacement and clearing. All four revisions completed in 14.6
  seconds, including debounce. A final repeat with idle-clipboard guards took
  28.1 seconds; this transport has variable latency. The remote editor was empty
  after both successful runs. No per-keystroke latency guarantee is claimed.
- Actual Windows IME composition, arbitrary Claude versions and Ubuntu desktop
  acceptance were not exercised. This remains an experimental bridge.
- Restored the failed deployment's private xrdp listener, verified TCP access
  from the paired Windows device, and added on-failure restart to the existing
  service drop-in. Installer templates carry the same recovery setting.
  No firewall, ACL, folder permission, GPU or public listener was broadened.

## Explicit send and local reply display — 2026-11-17

- All 97 skill Python tests passed on Windows. The 30 bridge tests also passed
  on CentOS, with three Windows-only tests skipped. New coverage includes v3
  PowerShell/Python protocol compatibility, exact-revision explicit submission,
  journal-before-Enter ordering, no replay after unknown outcomes, IME/Shift/
  repeated-Enter gating, latest-sync-before-submit, scoped reply ownership,
  navigation/subsequent-turn refusal, text extraction and truncation.
- A WPF event harness raised the actual panel's PreviewKeyDown Enter event,
  used production RDP clipboard transport and the deployed CentOS receiver,
  and received the real Claude fixture reply. It observed exactly one submit
  request, handled Enter, and local input clearing after acceptance. The short
  turn completed in 15.5 seconds; this is one observation, not a latency bound.
- A separate WPF callback harness verified cancelling a queued submit while
  a sync was pending preserves an editable local draft, reconnect restores the
  panel state, and copying a reply stops polling and unlocks input. It did not
  send another model request. The actual panel XAML was rendered and inspected
  for the README preview; that image is a UI preview, not a response screenshot.
- The receiver was updated with pairing preserved and without restarting Claude.
  Existing conversation replies were excluded from the watched new turn. No
  additional network port, expanded Windows MCP capability, folder grant or
  GPU setting change was needed.
- Actual OS IME composition and native Shift+Enter key entry, long live replies,
  other Claude versions and Ubuntu desktop acceptance remain unverified.
  Reply snapshots contain plain text only, up to 24000 characters; they do not
  establish complete Markdown/image/history mirroring or API streaming.
