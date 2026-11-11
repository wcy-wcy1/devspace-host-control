#!/usr/bin/env bash
# Plan by default. Apply only on the intended Ubuntu host after Tailscale login.
set -euo pipefail
apply=0
win_ip=''
ubuntu_ip=''
desktop_user=''
policy_file=''
clipboard=text
while (($#)); do
  case "$1" in
    --apply) apply=1; shift ;;
    --plan) shift ;;
    --windows-ip) win_ip="${2:?}"; shift 2 ;;
    --ubuntu-ip) ubuntu_ip="${2:?}"; shift 2 ;;
    --desktop-user) desktop_user="${2:?}"; shift 2 ;;
    --policy-file) policy_file="${2:?}"; shift 2 ;;
    --clipboard) clipboard="${2:?}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
[[ "$clipboard" == disabled || "$clipboard" == text ]] || { echo '--clipboard must be disabled or text' >&2; exit 2; }
python3 - "$win_ip" "$ubuntu_ip" <<'PY'
import ipaddress,sys
network=ipaddress.ip_network('100.64.0.0/10')
for value in sys.argv[1:]:
    if ipaddress.ip_address(value) not in network: raise SystemExit('Exact Tailscale IPv4 required by this installer')
if sys.argv[1]==sys.argv[2]: raise SystemExit('Two different devices required')
PY
if [[ -z "$desktop_user" || "$desktop_user" == root || ! "$desktop_user" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
  echo 'Existing non-root --desktop-user is required' >&2; exit 2
fi
if (( ! apply )); then
  printf 'PLAN: install XFCE/xrdp and official Claude Desktop; bind RDP to %s:3389; allow only %s via tailscale0; disable drive/device redirection; user=%s\n' "$ubuntu_ip" "$win_ip" "$desktop_user"
  echo 'No changes made. Before --apply, install/login Tailscale, apply and review exact grants, and provide --policy-file.'
  printf 'Clipboard=%s (default text; requires supported xrdp; files/images remain disabled).\n' "$clipboard"
  exit 0
fi
[[ $EUID -eq 0 ]] || { echo '--apply requires root' >&2; exit 2; }
source /etc/os-release
[[ "$ID" == ubuntu && ( "$VERSION_ID" == 22.04 || "$VERSION_ID" == 24.04 ) ]] || { echo 'Installer targets Ubuntu 22.04/24.04 only; review other versions manually' >&2; exit 2; }
id "$desktop_user" >/dev/null
command -v tailscale >/dev/null || { echo 'Install Tailscale using references/operations.md, authenticate, then rerun' >&2; exit 2; }
[[ "$(tailscale ip -4)" == "$ubuntu_ip" ]] || { echo 'Ubuntu VPN address does not match this device' >&2; exit 2; }
[[ -f "$policy_file" ]] || { echo '--policy-file must be the reviewed complete standard-JSON policy' >&2; exit 2; }
python3 "$script_dir/tailscale_policy.py" audit --windows-ip "$win_ip" --server-ip "$ubuntu_ip" --current "$policy_file"
if systemctl is-active --quiet xrdp.service; then
  echo 'Existing active xrdp detected. Review its users/configuration and arrange maintenance rather than disconnecting them automatically.' >&2
  exit 2
fi
# Prevent packages from exposing an unconfigured RDP listener while installing.
systemctl mask xrdp.service xrdp-sesman.service
fail_closed() {
  local result=$?
  trap - ERR
  systemctl mask --now xrdp.service xrdp-sesman.service >/dev/null 2>&1 || true
  echo 'Installation failed. RDP was stopped and left persistently masked; review before enabling it.' >&2
  exit "$result"
}
trap fail_closed ERR
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y xfce4 xrdp xorgxrdp dbus-x11 gnome-keyring libsecret-tools curl ca-certificates gnupg nftables xdg-utils
if ! command -v firefox >/dev/null && ! command -v chromium >/dev/null; then apt-get install -y firefox; fi
backup="/etc/xrdp/xrdp.ini.hostcontrol.$(date -u +%Y%m%dT%H%M%SZ).bak"
cp -n /etc/xrdp/xrdp.ini "$backup"
python3 "$script_dir/desktop_config.py" configure --server-ip "$ubuntu_ip" --clipboard "$clipboard" --apply
python3 "$script_dir/desktop_config.py" check
python3 "$script_dir/linux_session.py" --desktop-user "$desktop_user" --apply
install -d -m 700 /etc/hostcontrol
cat > /etc/hostcontrol/rdp.nft <<EOF
add table inet hostcontrol
flush table inet hostcontrol
table inet hostcontrol {
  chain rdp_guard {
    type filter hook input priority -150; policy accept;
    ip daddr $ubuntu_ip tcp dport 3389 iifname != "tailscale0" drop
    ip daddr $ubuntu_ip tcp dport 3389 ip saddr != $win_ip drop
  }
}
EOF
nft -c -f /etc/hostcontrol/rdp.nft
cat > /etc/systemd/system/hostcontrol-rdp-firewall.service <<'EOF'
[Unit]
Description=Exact source guard for HostControl private RDP
After=network-online.target tailscaled.service
Before=xrdp.service
[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f /etc/hostcontrol/rdp.nft
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
EOF
install -d /etc/systemd/system/xrdp.service.d
cat > /etc/systemd/system/xrdp.service.d/hostcontrol.conf <<'EOF'
[Unit]
Requires=hostcontrol-rdp-firewall.service
After=hostcontrol-rdp-firewall.service tailscaled.service
EOF
systemctl daemon-reload
systemctl enable --now hostcontrol-rdp-firewall.service
curl -fsSLo /usr/share/keyrings/claude-desktop-archive-keyring.asc https://downloads.claude.ai/claude-desktop/key.asc
claude_fpr="$(gpg --show-keys --with-colons /usr/share/keyrings/claude-desktop-archive-keyring.asc 2>/dev/null | awk -F: '$1=="fpr" {print $10; exit}')"
[[ "$claude_fpr" == 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE ]] || { echo 'Official Claude signing key identity mismatch' >&2; false; }
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/claude-desktop-archive-keyring.asc] https://downloads.claude.ai/claude-desktop/apt/stable stable main' > /etc/apt/sources.list.d/claude-desktop.list
apt-get update
apt-get install -y claude-desktop
python3 "$script_dir/launcher.py" linux --desktop-user "$desktop_user" --apply
systemctl unmask xrdp.service xrdp-sesman.service
systemctl enable --now xrdp.service
trap - ERR
printf 'Installed. Log in with the existing desktop account over %s:3389. Verify source rejection, the chosen clipboard mode and blocked drive/device redirection before production use. Backup: %s\n' "$ubuntu_ip" "$backup"
echo 'Generate a matching client file with desktop_config.py rdp, using the same --clipboard choice.'
