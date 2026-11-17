# Local chat panel for remote Claude

Experimental Windows + CentOS Stream 9 integration. The real WPF panel and
Claude receiver have been verified with Chinese, multiline text, emoji,
latest-value coalescing, replacement, clearing, explicit Enter and scoped replies.
Actual OS IME composition, Ubuntu desktops and other Claude versions still
require acceptance. The README image renders the real XAML; it is a UI preview.

Ordinary typing synchronizes an owned draft. Enter/Send submits once;
Shift+Enter inserts a newline, and IME confirmation does not submit. Replies
appear as bounded plain text in a separate read-only pane. This does not mirror
all history, images or attachments, and is not a Claude API streaming client.
Keep the RDP desktop connected and Claude available. Never disable its GPU or
sandbox to make the panel work.

## Configure once

Reuse the known paired user and private per-user install directories. Upgrade
the Windows agent, panel and Linux receiver together, preserving keys and policy.
Save unsent text before closing an older panel. Never run two active panels on
the same conversation. The input feature does not add an MCP tool or enable
Windows exec/desktop capabilities.

Windows setup (PowerShell):

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/setup-claude-input.ps1 -PrivateDirectory PRIVATE_INPUT_DIRECTORY -LauncherDirectory PRIVATE_LAUNCH_DIRECTORY -AgentConfig PRIVATE_AGENT_JSON
```

`-AgentConfig` opts into the low-latency relay. It backs up the existing agent
config and adds only `input_relay_state`, outside all authorized file roots.
Restart the updated visible agent after preserving its session state. It writes
`relay-state.dpapi` next to `input-key.dpapi`; the panel discovers it there. The
relay state uses the existing host-agent loopback control listener with a
separate current-user DPAPI bearer. Keep these files outside workspaces, public
archives and logs. A malformed/unavailable configured relay fails closed; it
never silently falls back after an unknown operation.

Without `-AgentConfig`, a new installation uses legacy RDP text clipboard.
An existing relay configuration is preserved. Disable the relay only through
an explicit reviewed configuration change on both ends, not by deleting state
after a timeout.

The helper creates/reuses a random 32-byte bridge key protected by current-user
DPAPI and adds `Claude-Input.cmd`. Its launch JSON contains only the key path.
Transfer this separate bridge key in memory through the authenticated
administrative setup connection, never chat, command arguments, plaintext
staging or logs. Do not export TLS private keys. Feed the bridge key's hex value
to the Linux setup helper's stdin only with `--apply`:

```text
python3 scripts/input_bridge_setup.py --desktop-user DESKTOP_USER
python3 scripts/input_bridge_setup.py --desktop-user DESKTOP_USER --relay-client-config PRIVATE_PAIRED_MCP_CLIENT_JSON --apply
python3 scripts/launcher.py linux --desktop-user DESKTOP_USER --apply
```

The relay reuses that user's existing paired MCP client config, CA, client
certificate and server pin. Generate/retain each TLS key on its own device.
No extra public/listening port, firewall grant, SSH runtime channel, proxy,
Claude API key or cookie transfer is needed. Linux makes outbound persistent
HTTPS long-polls to the existing agent mTLS port (normally 7443); the original
exact source-IP/certificate/Origin checks still apply. The local input lease is
separate from file-tool authorization and expires after 30 minutes; pausing the
agent closes it, and pairing revocation blocks remote relay access. This feature
can send only signed input packets for the Linux receiver. It cannot enable file
access, change roots or operate arbitrary Windows windows.

Omit `--relay-client-config` only for the legacy text-clipboard transport. It
requires matching enabled RDP text redirection. The receiver's pairing config is
user-owned mode 0600 at `~/.config/hostcontrol/input-bridge.json`; an existing
different key is rejected. Use signed distro packages for `xclip`, `xdotool`,
Python GObject and the Atspi 2.0 typelib. The opt-in Claude launcher adds
`--force-renderer-accessibility`. Save work and quit Claude normally before its
first restart with this flag; no remote-debugging flag is needed.

The receiver starts at that user's XFCE login. For an existing desktop start
`python3 ~/.local/bin/hostcontrol-input-bridge.py receive` as that user with the
actual XFCE display and D-Bus environment. A singleton lock prevents duplicates.
Restart only this helper on updates; do not restart Claude or RDP unnecessarily.

## Each use

1. Connect through `Start-Claude.cmd`, then open an empty Claude message box.
2. Open `Claude-Input.cmd`. **同步输入** starts enabled. Relay mode queues edits
   without the old 600 ms typing debounce, after IME composition is committed.
   One update is in flight; newer edits coalesce to the latest value. Replacement,
   deletion and clearing apply only to the owned confirmed draft.
   If the panel is already open, another launch exits normally without opening
   a second instance. Use Alt+Tab to switch to **Claude 本地对话**; existing text
   is preserved.
3. **Enter / 发送** first confirms the latest text, then triggers one submit.
   The input stays frozen until a new exactly matching user turn is observed.
   Only confirmed acceptance clears local text. **Shift+Enter** adds a newline.
4. The upper pane updates only that turn's plain-text response, up to 24000
   characters with a truncation notice. You may draft the next message while
   Claude replies; submission waits for the current reply to finish.
5. **取回回复** reads the current last user turn only if it exactly matches the
   panel's last sent text, or the locally entered previous-message text after
   restart. It does not fill a draft or press Enter. Recovery retains local text
   and keeps draft synchronization paused. It never resends an uncertain message.
6. **复制** copies the displayed reply and pauses synchronization. Other Windows
   clipboard operations do not interrupt relay-mode typing. Legacy mode owns
   the Windows clipboard during synchronization and pauses on unrelated copies,
   preserving the newer content. It restores its prior snapshot on pause/close
   only if its own packet/result is still current.
7. After remote edits, manual sends, navigation or an unknown outcome, inspect
   Claude. Leave its editor empty, then click **连接空对话** to reconnect explicitly.
   A new stream cannot overwrite nonempty unowned text. Unchecking **同步输入**
   stops queued work and reply polling, but cannot cancel an already dispatched
   operation or Claude generation. Closing preserves drafts.

Zero debounce does not mean zero end-to-end delay. The UI scheduler, network
round trip and accessibility updates still take time. No unknown operation is
automatically retried. The local lease can be reopened explicitly after expiry.
Keep concurrent automation and manual remote input out of an active transfer.
Linux PRIMARY is shared with other Linux apps; a clipboard manager configured
to synchronize PRIMARY with CLIPBOARD can defeat clipboard independence.

## Input and reply guards

Both transports carry HMAC-authenticated bounded `HC-DRAFT/2` and `/3` packets.
Requests have expiring timestamps, stream IDs and monotonic revisions with the
last confirmed SHA-256. A consumed request ID is persisted before UI input;
only IDs are journaled, not draft/reply text. Relay delivery is claimed before
writing HTTP, and is not redelivered after disconnect. A matching ticket/session
is required for acknowledgement. Unknown outcomes block the stream.

The receiver identifies exactly one editable, visible Claude prompt by
accessibility hints, native application PID, document/frame context and expected
text. It retains the target and fails on context, focus or text changes. It
processes accessibility events and invalidates caches for reply snapshots.
Relay typing uses Linux PRIMARY selection and a middle paste at a point derived
from the verified prompt bounds, avoiding the RDP CLIPBOARD selection. Pointer
coordinates, native focus and empty text are checked before the click; replacement
first selects and clears only the matching owned draft. It waits for text readback
without replaying keys. Legacy mode uses guarded Ctrl+V. Ordinary draft updates
never synthesize Return. Window/focus races remain possible in a shared desktop.

Explicit submit is independently signed and requires the owned exact revision
and native window/focus. The lease stays blocked after Enter. A dispatched submit
is not model acceptance. Reply polling confirms the new matching user article,
binds its conversation panel and reads only its following assistant response.
Navigation, subsequent user turns and ambiguous metadata stop the watch. The
recovery action instead binds the existing last matching user article read-only.
Replies are never interpreted as XAML, HTML, commands or bridge instructions and
are not persisted. After restart, handle any nonempty remote draft yourself.

## Verification and limitations

Run `input_bridge.py check` inside the intended desktop to count recognized
prompts without printing their contents. Test a harmless Chinese/multiline/emoji
draft through the real local panel; verify coalescing, replacement, deletion,
clearing and no submission. In relay mode confirm ordinary Windows copies stay
unchanged. In a dedicated test conversation verify one Enter, acceptance before
local clearing and the matching reply. Test Shift+Enter, actual OS IME, repeated
Enter, queued-send cancellation, recovery without resending, new conversation,
remote edits, focus changes, timeout, disconnect, bad signatures and restarts.
Offline fixtures do not establish deployment acceptance.

The live relay WPF harness confirmed first-request queueing at 422 ms after
window initialization, two coalesced draft revisions and clearing in 7.44 s on
one cross-region run, with the Windows clipboard unchanged. These observations
are not latency guarantees. Historical clipboard-mode acknowledgements took
2.9–3.3 s after dispatch plus 600 ms debounce; its short Enter/reply test took
15.5 s. The updated relay Enter/reply fixtures completed in 8.1 s and 8.9 s, each with
one submit; read-only recovery completed in 3.6 s with no submit. Neither
transport is a native Claude API stream. Acceptance waiting stops
after 40 s; reply watching after ten minutes, without cancelling generation.

To remove only this feature, preserve drafts, close the panel, stop its user
receiver, and remove its named helper, autostart, bridge config/state and private
Windows panel launch files. Remove `input_relay_state` only after the agent is
stopped; preserve all main-agent pairing credentials, roots, firewall and RDP.
Removing the opt-in bridge config removes the accessibility flag on Claude's next
normal launch. Restore backups only if later edits will not be lost.
