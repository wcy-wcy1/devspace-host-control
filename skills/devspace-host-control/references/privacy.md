# Boundaries that must stay explicit

The approved target is the user's **real Windows desktop**, not a local VM. The user accepts that local information may be read. Do not quietly substitute a VM, Claude Code, or a browser-only client. That approval does not authorize routine fingerprint harvesting, credential collection or full-disk synchronization.

The network permits exact Tailscale device addresses and required ports. It also needs device identity, certificate pinning and local authorization. IP alone is not authentication. Other tailnet members are untrusted. Observe IPv6 as well as IPv4; bootstrap scripts intentionally use exact IPv4, and the agent binds one explicit address.

Independent Linux user/browser state and disabled drive/device redirection avoid mounting local profiles automatically. Text clipboard redirection is enabled by default, so copied text may cross the RDP session; use `--clipboard disabled` to turn it off. This is not a sandbox around the real Windows account. General commands and GUI interactions can reach information and services accessible to that account, including through alternate programs. The file API's workspace root restrictions do not restrict a shell.

Do not claim zero fingerprint leakage. Routine Claude traffic originates on Ubuntu, but tool results can reveal local identifiers. Screenshots can include private windows/overlays. `desktop_windows` deliberately returns window titles. The host exposes only a pseudonymous status, but arbitrary commands can explicitly query system identities. Errors are minimized, not universally anonymized. There is no universal image/text secret detector.

Tailscale direct connections and coordination may reveal underlying public endpoints/metadata to peers or infrastructure. Encryption does not hide data from an authorized endpoint. Requiring Ubuntu not to know the local public address needs a separately designed and measured gateway/relay path; simply selecting a relay is not proof that endpoint metadata is hidden. Account identity and Ubuntu's own network/browser characteristics remain visible to service providers. This release does not implement an anonymity gateway.

Windows-generated key files use current-user DPAPI plus directory ACL protection; Ubuntu private keys use restrictive filesystem permissions. This does not protect keys from an already compromised same-user process. Keep the CA signing key and all credential directories outside authorized file roots, archives and skill assets. Never ship a pre-paired certificate or reusable default secret.

The local web control panel has a per-run random token, loopback-only binding, exact Host/Origin checks and no remote enable tool. A real desktop controller may itself see or operate that panel if it appears in the authorized desktop. Consequently it is not a strong separation boundary against a malicious controller with full GUI rights. Ctrl+C/process termination and network revocation remain recovery paths.

The control program is an explicit foreground tool. Do not add persistence, auto-elevation, lock-screen bypass, credential forwarding, keylogging or security-tool exclusions. If a task needs startup automation, receive that request and implement a visible, reversible user-session startup mechanism separately.
