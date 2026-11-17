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


## Low-latency relay, reply recovery and panel design — 2026-11-17

- All 103 skill Python tests passed on Windows. All 36 bridge tests ran on
  CentOS, with three Windows-only cases skipped. New cases cover read-only
  exact-last-turn recovery, no resend, zero-debounce/IME gating, pinned relay
  endpoints, guarded PRIMARY paste, asynchronous text readback, pointer mismatch
  refusal and retrying only read-only transient accessibility snapshots.
- `go test ./... -count=1` and `go vet ./...` passed. Relay tests cover local
  bearer/lease separation, expiry, bounds, one outstanding packet, claimed
  delivery without replay, exact result tickets, default opt-out, the existing
  source/certificate gate, revocation, DPAPI state and refusal to use the input
  bearer to enable Windows file tools. All six runtime assets were rebuilt with
  the portable Go 1.25.9 toolchain from the changed production source.
- The actual deployed WPF panel and CentOS Claude exchanged Chinese/multiline/
  emoji drafts over the existing outbound mTLS port. The final production run
  queued its first request 422 ms after window initialization, confirmed the
  newest of two coalesced edits at 5.44 s, and cleared the remote draft at 7.44 s.
  Its Windows clipboard marker remained unchanged. The transport has zero
  application debounce; these are measured round trips, not per-keystroke or
  zero-latency promises. Linux PRIMARY remains shared with other Linux apps.
- New empty conversations were opened without restarting the receiver. Two
  subsequent explicit Enter fixtures each produced one submit request, the real
  matching Claude response and local clearing after acceptance, in 8.1 s and
  8.9 s. Recovery of the preceding completed fixture took 3.6 s, sent no submit,
  retained the local source text and left draft synchronization paused.
- Investigation also observed fail-closed unknown outcomes and one changed
  clipboard marker during a concurrent desktop run. No unknown submission was
  replayed; remote state was read to confirm acceptance. Do not promise reliable
  input amid concurrent manual remote actions, clipboard-manager PRIMARY/CLIPBOARD
  synchronization, arbitrary application versions or focus changes.
- The actual final XAML was rendered and visually inspected. A layout harness
  checked normal and minimum window sizes, long replies, wrapping status text
  and nonoverlapping controls. WPF callback checks also covered queued-send
  cancellation, draft preservation, reconnect and copy/pause unlocking.
  The README image is a real-XAML preview, not a live model-response screenshot.
- Windows agent, Linux receiver and local launch files were upgraded with pairing
  preserved. Production policy was read back as files-only, with only the
  previously approved root. No MCP exec/desktop capability, firewall grant,
  public port or GPU setting was added. Temporary metadata-only diagnostics were
  removed and the normal receiver restored. Actual OS IME and Ubuntu acceptance
  remain unverified.


## Agent restart and reply clock skew — 2026-11-17

- Diagnosed the stopped Windows agent and stale loopback state without reading
  user message contents. Restarted the existing files-only agent visibly and
  paused; no file session was automatically enabled. A signed non-input poll
  confirmed the paired receiver was reachable in 0.45 s without UI input.
- The receiver's valid response timestamp was about four seconds ahead of the
  Windows clock, exceeding the previous two-second future limit. Windows now
  permits at most ten seconds of future reply skew, still verifies the HMAC and
  exact request ID, and still rejects responses over twenty seconds old. Neither
  system clock nor timezone was changed.
- Reconnect explicitly reloads the current DPAPI endpoint and token. Missing or
  invalid configured state fails closed. Pending input prevents reconnect; no
  automatic resend, fallback or local text clearing is introduced. Pre-dispatch
  connection failures now show a useful agent-start instruction instead of the
  raw GetResult/TaskCanceled exception.
- All 106 skill Python tests passed on Windows. New tests use temporary DPAPI
  fixtures for endpoint/token rotation and rejection, bounded reply clock skew,
  and the actual WPF reconnect callback with draft preservation, pending-submit
  refusal and missing-state failure. No production credential is in fixtures.
- The real deployed draft test synchronized Chinese/multiline/emoji edits and
  clearing with its clipboard marker unchanged. First request queued at 452 ms,
  newest coalesced draft confirmed at 2.99 s, and final clear at 4.09 s. No Claude
  message was submitted. Source packaging/integrity checks were rerun; Go
  production code and runtime binaries were unchanged in this fix.

## Opt-in Claude to Codex jobs — 2026-11-17

- Feature-branch checks on Windows: `go test ./... -count=1` and `go vet ./...`
  passed; all 9 Node runner tests and all 106 existing skill Python tests passed.
  The Go suite includes 12 Codex-specific tests for default-off discovery,
  validated local configuration, exact-commit Git export, argument refusal,
  durable request IDs, bounded continuation, cancellation/restart, cleanup
  failure, fixed container arguments and verified artifact retrieval.
- The Git integration test used a real disposable local repository, ran actual
  Git init/fetch/archive commands and refused a commit outside the approved ref.
  Only that test fixture permits local file transport; production accepts fixed
  credential-free HTTPS repositories and disables Git file/ext transports.
- Runner tests execute real child processes through a fixture CLI, preserving
  actual failed-test exit codes, producing a real new-file Git diff and writing
  valid PNG bytes. They cover UTF-8 split across output chunks, bounded output,
  timeout and malformed/oversized event refusal without model access.
- Go integration tests replace Docker execution with a fixture. Native PNG
  content survives the MCP JSON-RPC boundary; empty text, large escaped text,
  ASCII PDF, hash tampering, links and oversized artifact cases are checked.
  Fixture images are not screenshots of a running application.
- All six runtime assets were rebuilt with portable Go 1.25.9. Source mirroring,
  runtime hashes/architecture and release archive integrity were checked using
  the repository packaging tools. The skill passed its quick validator.
- The installed Docker CLI could not reach `dockerDesktopLinuxEngine` because
  its named pipe was absent. The runner image was not built or started, no real
  Codex login/model job ran, and actual Claude image/resource display remains
  unverified. Docker containment/network behavior requires live acceptance.
- No production agent, installed skill, account, file grant, session, firewall,
  server or main-thread checkout was changed. This capability remains opt-in.

## Real Codex/container/MCP acceptance — 2026-11-17

- Started the existing Docker Desktop Linux engine and built the browser-enabled
  QA recipe with official Node 22, Git, Chromium and Codex CLI 0.169.0. The tested
  immutable image ID was
  `sha256:60fe5e9449ed46d6e385c63b8b02eba2545d4a2440fbe46ba24aa25d06f8eab0`.
  Docker Hub connectivity initially failed; the official base image was fetched
  through Google's Docker Hub cache, validating compressed/uncompressed layer
  digests before import. QA build traffic used a build-scoped local proxy.
- Used a new private runner login. Device-code login was disabled in the account;
  normal browser OAuth via the official Windows CLI completed with a dedicated
  `CODEX_HOME` and file credential store. No main Codex profile was copied and no
  account security setting was changed. A minimal Node-only login attempt also
  exposed missing system CA roots/non-executable temporary storage; TLS validation
  remained enabled throughout recovery.
- `TestCodexLiveIntegration` passed against actual Git, Docker and model calls in
  138.78 seconds. It fetched the public `octocat/Hello-World` master commit
  `7fd1a60b01f91b314f59955a4e4d4e80d8edf11d`. Three real model tasks produced
  test analysis, a new-file patch and a second-round patch preserving prior edits.
  Stable request replay did not create duplicate work; a third round was refused.
- The locally fixed intentionally failing suite returned exit code 7 and
  `tests.passed: false`, while Codex completed its analysis. Chromium actually
  rendered a synthetic page with a fresh challenge marker and wrote a 640x480 PNG.
  Successful Chromium command events were observed, the PNG survived actual
  loopback pinned mTLS plus MCP JSON-RPC as native image content, and the returned
  screenshot was visually inspected. This is a synthetic-page screenshot, not a
  production application test or an image shown inside Claude Desktop.
- Inspected the actual running container: read-only root, all capabilities
  dropped, no-new-privileges, 2 GiB memory, bridge network and only the five
  approved bind mounts with the correct read/write modes. Actual writes to the
  root/source/baseline were refused. An executable in bounded temporary build
  scratch ran successfully after explicitly allowing `exec` on that tmpfs while
  retaining `nosuid,nodev`. No host permissions were expanded.
- Cancelled a real sleeping test container before model analysis, waited for the
  terminal cancelled state, independently confirmed container absence and
  verified that pausing the QA session blocked subsequent result reads. No QA
  task container remained. Private acceptance metadata and the returned PNG were
  retained locally; login material was excluded from source and release bundles.
- The existing production agent, Claude Desktop configuration, firewall,
  pairing and main-thread checkout were preserved. Real Claude Desktop tool
  discovery and its rendering of these images/resources remain unverified.
- Final regression checks passed: Go tests/vet, all 9 runner tests, all 106
  Python tests and a cached rebuild from the distributed `Dockerfile.qa` recipe.
  All six runtime binaries were rebuilt after the temporary-mount correction,
  then source mirroring, hashes/architecture and release archives were refreshed.
## Scoped image previews in the local panel — 2026-11-17

- All 116 Python skill tests passed on Windows. New cases cover image content
  versus toolbar icons, signed per-turn image IDs, navigation/removal refusal,
  discard after capture races, bounded packets, PNG type/dimension checks,
  corrupt-image decode rejection, and real WPF image cards/scroll viewports at
  normal and minimum widths. The real recovery callback preserves local drafts;
  a busy relay is distinguished from an unavailable agent. CentOS ran 46 bridge
  and image tests successfully, with six Windows-only cases skipped.
- The user's existing reply exposed a named SVG content image in an inline
  frame. Its initially clipped position was rejected. During acceptance only,
  a scoped accessibility scroll brought the whole image into view; production
  image requests do not scroll or send keys. The verified native-window crop
  produced a 690×470 PNG (32391 bytes), visually inspected as the landscape.
- A real production WPF event harness recovered the existing exact-last-user
  reply over the deployed mTLS relay, then requested and rendered one image
  card automatically. It received 121 text characters and the 690×470 preview
  in 11.49 seconds, with zero draft-write requests and zero submit requests.
  Reencoding that displayed bitmap to PNG succeeded. The SaveFileDialog click
  itself was not automated. No new model message was sent for image QA.
- A previous local input lease remained occupied after the older panel closed.
  Authenticated HTTP 409 identified that condition. After the user saved and
  closed the panel, only the Windows agent was restarted, with file access
  paused and pairing preserved. The receiver was updated separately; Claude,
  hardware acceleration, RDP, file roots and network policy were unchanged.
  Temporary remote image/last-turn QA files were removed. Desktop UI automation
  was stopped when the user pressed Escape; subsequent verification used the
  application's own WPF harness and the established administrative connection.
- These are labeled preview screenshots of fully visible images, not original
  SVG/file downloads. Up to four content images are scoped to one completed
  watched reply; each PNG is at most 80000 bytes and 1024 pixels per side. No
  arbitrary URL/file fetch, root-desktop capture, browser rendering, new MCP
  capability or port was added. Hidden/clipped images, standalone artifact
  sidebars, other Claude versions and Ubuntu acceptance remain outside the
  observed live result. Shared-desktop focus/layout races remain possible.
- Source/package/skill validation passed. Go production code and all runtime
  binaries are unchanged; no Go rebuild was required for this Python/WPF update.

## Actual Claude Desktop orchestration acceptance — 2026-11-17

- Upgraded the paired Windows agent and CentOS MCP adapter from the reviewed
  feature binaries, backing up both. Preserved exact-device network rules,
  certificate pairing, the existing file root and files-only generic policy;
  added only the separately configured Codex capability for the public QA repo.
  The human explicitly authorized a local 30-minute session. Claude restarted
  normally with an empty draft; hardware acceleration stayed enabled.
- Actual Claude Desktop discovered and called the seven Codex tools. Its first
  read-only project discovery timed out twice while the approval UI was outside
  the visible viewport. No job was started by those failed attempts. A fresh
  read-only retry succeeded after scrolling the exact approval into view and
  granting once; permanent tool approval was not enabled.
- Claude itself started implement job e80144aae1de4a7ca92e41e1b1ff1e0b against
  the pinned public Hello-World commit. A real Codex model generated a six-digit
  random visual challenge entirely inside container temporary storage and used
  real Chromium to render its page into a 640x480 PNG. Independent inspection
  found the challenge absent from all first-round exported text, report and patch.
- Claude fetched that artifact as a native MCP image, read 693536, then called
  codex_continue as job 07f006d531b1476188888541a03fb80b with the observed digits.
  The second real Codex call preserved the original marker and appended the
  observed code plus Desktop orchestration verified. Claude fetched and checked
  the returned patch; independent on-disk inspection matched all three lines.
  Both jobs completed with exit code 0 and both named containers were absent.
- This run used no fixed test suite (tests is null); the earlier programmatic
  exit-7/cancellation/isolation acceptance remains a separate result. This proves
  desktop tool discovery, real delegation, native-image reading and continuation
  for the configured synthetic public QA scenario. It does not establish arbitrary
  project compatibility, Windows GUI control, original-file downloads from Claude
  artifacts or local-panel display of every intermediate tool card.
- Private job states, reports, challenge PNG, exact patch and the scoped QA
  transcript are retained locally without account credentials in the repository.
  See docs/claude-codex-acceptance.md for the sanitized acceptance record.
- After merging the existing scoped-image-preview changes into this feature
  branch, all 116 Python skill tests and all 9 Node runner tests passed again.
  Go source/runtime hashes match the previously live-tested feature binaries;
  no Go source was changed during this desktop acceptance.

## Local control page redesign — 2026-11-17

- Extracted the existing embedded control HTML into a Go-embedded control.html;
  the compiled agent still serves all markup, CSS, SVG and JavaScript locally.
  No CDN, font request, remote assets or new listener was introduced. Existing
  localhost bearer-token handling, CSP, host/origin checks and API routes remain.
- Added translucent light/dark panels, responsive session/permission grouping,
  readable capability labels, explicit loading/error/revoked/empty states,
  keyboard focus and reduced-motion support. Pairing revocation remains under
  a separate disclosure. Permission changes still pause the authorized session.
- Real headed Chromium through Playwright CLI checked 1280px desktop and
  390px/320px narrow views using an isolated loopback mock, never the production
  control token. Actual mock enable/pause and read-only save actions succeeded;
  saved permissions paused the mock session. No horizontal overflow was observed.
  Revoked and unavailable states were inspected. On failed status reads, enabling
  fails closed while local pause/revoke remain available to attempt stopping.
- Paused/active light and active dark screenshots were visually reviewed. The
  README screenshot explicitly labels simulated data. The dark primary button
  and save label have measured text contrast ratios 5.14:1 and 5.67:1.
- Go tests and vet passed; all 116 Python tests passed. All six runtime binaries
  were rebuilt, then repository source mirrors and bundle/archive hashes refreshed.
  Production agent was not restarted during this UI work; the running instance
  continues to serve its previous page until normally upgraded/restarted.
