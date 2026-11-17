# Local input panel for remote Claude

Experimental: offline protocol tests pass, but Chinese/multiline draft transfer
has not completed end-to-end acceptance in a real Claude session. Validate the
paired app version and clipboard path before treating this feature as ready.

This optional feature fills a remote Claude draft from a Windows text box.
The user reviews and sends in Claude. It does not submit messages, mirror Claude
responses or replace the remote desktop. Requires an active text-enabled RDP
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
3. Click **填入远程 Claude**. Keep RDP connected while waiting for confirmation.
4. Review the resulting Claude draft and send it in Claude yourself.

The panel retains the source text. It temporarily owns the clipboard while
waiting, and restores a snapshot only if its own request/result is still current.
If another app changes the clipboard, that change is preserved. A timeout is an
unknown result: inspect Claude before clicking again. Input cannot overwrite a
nonempty draft; it requires exactly one visible, editable Claude prompt with a
recognized accessibility hint, not an arbitrary focused application or field.

## Protocol and verification

Requests and acknowledgements use authenticated `HC-DRAFT/1` text packets over
the existing RDP clipboard. Payloads are bounded, timestamped and expire after
15 seconds. A consumed ID is persisted before UI input, so a lost response or
receiver restart does not replay it. Clipboard text and draft contents are never
logged or saved by the receiver. This is ordinary draft input; Claude may handle
drafts according to its own application behavior.

Run `input_bridge.py check` in the user's desktop session to count recognized
prompts without reading or printing their contents. Verify an actual local-panel
transfer with harmless Chinese and multiline text, observe it remains unsent,
then verify a second transfer refuses to replace that draft. Test disconnected
RDP, changed clipboard, timeout, invalid signatures and receiver restart too.
Offline tests do not establish live app or RDP clipboard compatibility. An app
update may require adjusting the prompt accessibility hints; fail visibly rather
than guessing coordinates or submitting messages. The receiver focuses only the
PID-verified Claude document frame and performs one Ctrl+V after checking the
keyboard focus and empty message field again; it never sends Enter. Concurrent
local activity inside the same Linux desktop can still change focus, so avoid
using another remote input tool during a transfer.

To remove this feature, close the panel, stop only its user receiver, remove the
named input-bridge helper, autostart entry, config and state directory, and remove
the added input-panel files from the private Windows launch directory. Removing
the opt-in config disables the accessibility flag on the next normal Claude
start. Keep the main agent, pairing, RDP and firewall configuration intact.
