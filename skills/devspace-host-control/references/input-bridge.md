# Local chat panel for remote Claude

Experimental: Windows + CentOS Stream 9 acceptance verified real Claude draft
updates with Chinese/multiline text, coalesced edits, deletion and clearing,
plus explicit Enter submission and the corresponding real Claude reply.
The WPF event harness exercised the real clipboard transport; actual OS IME
composition and other app versions still require acceptance. Validate the paired
app version and clipboard path on each deployment.

This optional feature synchronizes a remote Claude draft from a Windows text box,
submits on explicit Enter/Send, and displays that turn's reply in a separate
read-only pane. Ordinary typing never submits. It does not mirror all history,
images or attachments, and does not replace the remote desktop. Requires an active text-enabled RDP
connection; disabled clipboard mode cannot use it. No additional network port,
SSH runtime connection, Claude API key or account-cookie transfer is needed.

## Configure the paired devices

Reuse the known desktop user and private per-user install location. On Windows:

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/setup-claude-input.ps1 -PrivateDirectory PRIVATE_INPUT_DIRECTORY -LauncherDirectory PRIVATE_LAUNCH_DIRECTORY
```

This creates a random bridge message key protected by current-user DPAPI, and
adds `Claude-Input.cmd` to the private launch directory. Existing keys are reused.
The launch configuration refers to the protected key by path, not its contents.
Keep the directory private and outside project/release archives. The HMAC key is
for draft transport; never export existing TLS private keys for this feature.

The executor transfers this separate bridge key in memory over the already
authenticated administrative setup connection, without chat, command arguments,
logs or plaintext staging files. Feed its hex value to the Linux setup helper's
stdin (only with `--apply`):

```text
python3 scripts/input_bridge_setup.py --desktop-user DESKTOP_USER
python3 scripts/input_bridge_setup.py --desktop-user DESKTOP_USER --apply
python3 scripts/launcher.py linux --desktop-user DESKTOP_USER --apply
```

The receiver stores this key in the chosen user's mode-0600
`~/.config/hostcontrol/input-bridge.json`. A different existing pairing is rejected.
Use the distro's signed repositories for `xclip`, `xdotool` and GObject introspection with
the Atspi 2.0 typelib (`python3-gi`/`gir1.2-atspi-2.0` on Ubuntu;
`python3-gobject-base`/`at-spi2-core` on CentOS). Do not enable remote debugging or
disable Electron's sandbox. The updated Claude launcher adds
`--force-renderer-accessibility` only when this opt-in config exists.

The receiver starts with that user's XFCE login. For the current session, start
`python3 ~/.local/bin/hostcontrol-input-bridge.py receive` as the intended desktop
user using the actual XFCE environment; it holds a per-user singleton lock.
After saving work, reopen Claude normally through the configured launcher so its
accessibility flag takes effect. Do not terminate a running Claude without the
user's saved-work/restart confirmation. A closed window may leave the app running
in its tray; inspect the intended user's main executable before taking action.

## Each use

1. Use `Start-Claude.cmd` to connect and open Claude, then open a conversation.
2. Double-click `Claude-Input.cmd` on Windows and enter Chinese or multiline text.
3. **实时同步** is enabled by default. Pause typing for about 600 ms to queue the
   latest text. IME compositions are deferred until committed. Edits, deletions
   and clearing the local box update the owned remote draft too.
4. Press **Enter** or click **发送** to submit the latest draft once; **Shift+Enter**
   inserts a newline. IME composition confirmation is not a submit. The panel
   first confirms the latest sync, freezes input through submission confirmation,
   and clears local text only after finding the new matching user turn in Claude.
5. Read the corresponding reply in the upper pane. Polling updates plain text
   during generation; the limit is 24000 characters, with a truncation notice.
   You may compose the next draft after acceptance, but cannot submit it until
   the current reply completes. **复制回复** copies text and pauses the bridge.
6. Uncheck **实时同步** to stop queued sends and reply polling; this cannot cancel
   an already dispatched update, submit or Claude generation. After remote
   edits/manual submission, a detected conversation change or any uncertain
   outcome, synchronization stops. Handle the remote draft yourself, leave its
   editor empty, then click **重新连接空草稿** to explicitly start a new stream.

600 ms is a debounce delay, not an end-to-end latency promise. Only one request
is in flight; newer edits coalesce into the latest value after acknowledgement.
No automatic retry follows a timeout. Closing the panel leaves both drafts intact.
The panel is a singleton per Windows session. Upgrade both receiver and panel;
old receivers cannot process version-3 chat operations. A legacy draft-only
panel may remain open during upgrade so its unsent text is not destroyed; preserve
that text and close it before actively using the new panel. Do not run both
against the same clipboard at once.

The panel retains the source text. After the first update it owns the clipboard
through the active synchronization session. It restores a local snapshot when
paused/closed, only if its own request/result is still current. Restoring between
consecutive requests races RDP clipboard announcements and can lose updates.
If another app changes the clipboard, that change is preserved. A timeout is an
unknown result: inspect Claude before reconnecting. The first update cannot overwrite
a nonempty draft; it requires exactly one visible, editable Claude prompt with a
recognized accessibility hint, not an arbitrary focused application or field.
Later updates validate the retained editor directly (no full application-tree
rescan), its document/frame context and exact previous
text hash. Any mismatch blocks that stream. Ownership and hashes live in memory
only; after receiver restart a nonempty draft requires manual handling. Context
checks depend on the app's accessibility metadata and are not a Claude conversation
ID API; avoid remote navigation while synchronizing.

## Protocol and verification

Synchronization requests and acknowledgements use authenticated `HC-DRAFT/2` text packets over
the existing RDP clipboard. Payloads are bounded, timestamped and expire after
15 seconds. A consumed ID is persisted before UI input, so a lost response or
receiver restart does not replay it. Clipboard text and draft contents are never
logged or saved by the receiver. Each update carries a stream ID, monotonic
revision and SHA-256 of the last confirmed text. Empty text is valid for clearing
an owned draft. Legacy one-shot `HC-DRAFT/1` remains empty-editor-only.
Explicit submit and reply polls use authenticated `HC-DRAFT/3` packets on the
same transport. Submit requires the owned editor's exact confirmed revision,
native PID/window/focus and expected text; its request ID is journaled before
Enter and the lease stays blocked afterward. A dispatched submit is not proof
of model acceptance. Reply polling confirms a new user article matching the
submitted text, binds that article and conversation panel, then reads only its
following assistant response. Navigation, subsequent user turns and ambiguous
accessibility metadata stop the watch. Old conversation replies are not shown.
Replies are plain text, never interpreted as XAML, HTML, commands or bridge
instructions, and are not persisted by the bridge. The ID-only journal remains.
After a restart or unknown outcome, inspect remote state; never auto-resubmit.
Ordinary draft updates remain non-submitting; Claude may handle drafts according
to its own application behavior.

Run `input_bridge.py check` in the user's desktop session to count recognized
prompts without reading or printing their contents. Verify an actual local-panel
transfer with harmless Chinese and multiline text, observe it remains unsent,
then verify append, replacement, deletion and clearing. In a dedicated empty
conversation submit one harmless test using Enter, confirm exactly one user
turn, acceptance before local clearing, and matching reply display. Check
Shift+Enter, IME commit, repeated Enter, queued-send cancellation and copying a
reply while paused. Verify remote edits,
submission, target/context changes and another panel's stream refuse replacement.
Test IME composition, rapid edits during a pending request, disconnected RDP,
changed clipboard, timeout, invalid signatures and receiver restart too.
Offline tests do not establish live app or RDP clipboard compatibility. An app
update may require adjusting the prompt accessibility hints; fail visibly rather
than guessing coordinates or submitting messages. The receiver focuses only the
PID-verified Claude document frame. It rechecks the exact focused editor, window
and expected previous text before Ctrl+V; replacing an owned draft uses Ctrl+A
first, and clearing uses BackSpace. Ordinary sync never sends Enter; only a
separately authenticated, explicit submit can do so after validation. Concurrent
local activity inside the same Linux desktop can still change focus, so avoid
using another remote input tool during a transfer.

On the observed cross-region deployment, version-2 request acknowledgements took
about 2.9–3.3 seconds after dispatch, in addition to the 600 ms debounce. Do not
describe this clipboard transport as instantaneous per-keystroke mirroring.
The version-3 WPF event harness verified one Enter submission and the actual
Claude response appearing locally in 15.5 seconds for a short fixture. This is
polling over RDP clipboard, not an API streaming connection. Acceptance wait
stops after 40 seconds; reply watching stops after ten minutes, without cancelling
Claude's generation. Actual OS IME and other app versions remain unverified.

To remove this feature, close the panel, stop only its user receiver, remove the
named input-bridge helper, autostart entry, config and state directory, and remove
the added input-panel files from the private Windows launch directory. Removing
the opt-in config disables the accessibility flag on the next normal Claude
start. Keep the main agent, pairing, RDP and firewall configuration intact.
