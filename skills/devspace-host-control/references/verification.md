# Verification and evidence

## Source and offline checks

- `assets/source/hostcontrol` is the implementation snapshot. Run Go 1.25+ `go test ./... -count=1` and `go vet ./...` there. This module has no third-party Go dependencies. Build with its `build.ps1` or equivalent `GOOS/GOARCH` commands; Ubuntu adapter is available for amd64/arm64.
- Run `python -m unittest discover -s scripts -p 'test_*.py' -v` from the skill directory. This includes configuration, policy transactions, dependency invalidation, legacy-journal revalidation and desktop regressions using a repeated-parameter fixture, plus integration checks from a policy plan to installer audit and client generation. Check both desktop installer scripts with `bash -n`; preview installation, desktop configuration and firewall scripts without apply.
- Compare runtime files to `assets/SHA256SUMS.json` before using them. Do not execute binaries from a substituted bundle without checking their provenance.
- Windows tests cover real Job Object cancellation of a child process. A Windows directory junction is used when symlink privileges are unavailable, exercising `os.Root` escape rejection.
- The optional `HOSTCONTROL_DESKTOP_QA=1` test performs a read-only capture of the active desktop window, validates PNG dimensions and saves no image. It is not a mouse/keyboard end-to-end test.

## Deployed network and authentication

1. From the approved Windows device, connect to Ubuntu RDP. From an unapproved device, verify refusal. Test the actual deployed complete Tailscale policy, not only a generated JSON fragment.
2. From paired Ubuntu, reach only the explicit agent endpoint. Verify another tailnet peer, wrong/missing client certificate, wrong server fingerprint and unwanted address family fail. Binding, firewall and tailnet grants each need evidence.
3. Default agent startup must remain paused. Local enable admits authorized calls; expiry rejects new calls. Pause cancels tasks. Revocation survives restart. Wrong/empty configuration must not start a wildcard listener.
4. Confirm RDP clipboard matches the configured mode: text-only by default with clipboard file/image formats denied, or explicitly disabled. Confirm drive/device sharing remains unavailable. Test a harmless clipboard marker only after saving work, logging out and reconnecting with the matching client file. Do not inspect existing clipboard contents. Check that Linux was not given a LAN route or configured to use the local machine as an exit node.

## Server and application timezone

Confirm the selected IANA zone comes from the user's chosen datacenter region.
Read back system zone and NTP state separately, inspect selected-user XFCE/Claude
TZ/clock overrides, then compare the actual refreshed application/browser time.
No environment override does not prove Chromium caches were refreshed. Record
pending refresh/reopen honestly; keep Windows timezone and active sessions intact.
See [timezone.md](timezone.md).

## Real tool scenarios

1. Discover tools inside the actual Ubuntu Claude Desktop client. Read anonymous host status after local session authorization.
2. Create a disposable UTF-8 file under configured workspace `work`, read it, update using the returned hash, confirm backup contents, and verify traversal and a directory-link escape are rejected by file APIs.
3. Run a harmless command with a short timeout; inspect stdout/stderr and real exit code. Run a task with a child, cancel it, confirm both stopped. Verify output limits.
4. List windows, focus an approved test window, capture, then click/type Unicode with the returned frame. Re-capture before each next input. Verify moving the window, changing focus or waiting past 30 seconds causes rejection. Verify that an input frame cannot be reused.
5. Inspect a test window with `desktop_inspect`; verify actual UI Automation element names and bounds are returned, or that unsupported applications fail clearly. Open a test HTTP/HTTPS page with `browser_open`; interact through screenshot tools. This release does not expose DOM/Playwright tools or UIA element-based action dispatch. Do not report them as implemented.
6. Check display scaling and multiple displays, lock screen, UAC/error handling, output truncation, network failure and unknown outcomes. Do not automatically replay side effects after loss of a result. Deduplication is in-memory for the running agent; restart is not durable exactly-once execution.

## Reporting

Distinguish compiled, unit/integration tested, live-desktop observed, and deployed-client verified. Record skipped checks and actual reasons. Do not claim Ubuntu deployment, Claude discovery, firewall effectiveness, input reliability or public-IP hiding based solely on local tests. `assets/build-evidence.md` records the checks performed when this package was assembled.
