#!/usr/bin/env bash
# Native CentOS 9 adaptation. Claude Desktop does not officially support EL9.
set -euo pipefail
apply=0
win_ip=''
server_ip=''
desktop_user=''
policy_file=''
clipboard=text
while (($#)); do
  case "$1" in
    --apply) apply=1; shift ;;
    --plan) shift ;;
    --windows-ip) win_ip="${2:?}"; shift 2 ;;
    --server-ip) server_ip="${2:?}"; shift 2 ;;
    --desktop-user) desktop_user="${2:?}"; shift 2 ;;
    --policy-file) policy_file="${2:?}"; shift 2 ;;
    --clipboard) clipboard="${2:?}"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
[[ "$clipboard" == disabled || "$clipboard" == text ]] || { echo '--clipboard must be disabled or text' >&2; exit 2; }
python3 - "$win_ip" "$server_ip" "$desktop_user" <<'PY'
import ipaddress,re,sys
for value in sys.argv[1:3]:
    address=ipaddress.ip_address(value)
    if address.version!=4 or address not in ipaddress.ip_network('100.64.0.0/10'):
        raise SystemExit('Exact Tailscale IPv4 addresses required')
if sys.argv[1]==sys.argv[2]: raise SystemExit('Two different devices required')
if not re.fullmatch(r'[a-z_][a-z0-9_-]*',sys.argv[3]) or sys.argv[3]=='root':
    raise SystemExit('Existing non-root desktop user required')
PY
if (( ! apply )); then
  printf 'PLAN: Native CentOS 9 XFCE/xrdp; signed official Debian Claude package adapted locally; RDP=%s:3389 source=%s user=%s; no public RDP or Windows control port.\n' "$server_ip" "$win_ip" "$desktop_user"
  echo 'Requires an authorized native-CentOS choice, Tailscale login, a reviewed live policy and an existing desktop user. No changes made.'
  printf 'Clipboard=%s (default text; files/images/devices remain disabled).\n' "$clipboard"
  exit 0
fi
[[ $EUID -eq 0 ]] || { echo '--apply requires root' >&2; exit 2; }
source /etc/os-release
[[ "$ID" == centos && "$VERSION_ID" == 9 ]] || { echo 'This adaptation targets CentOS Stream 9 only' >&2; exit 2; }
[[ "$(uname -m)" == x86_64 ]] || { echo 'Native CentOS adaptation currently supports x86_64 only' >&2; exit 2; }
id "$desktop_user" >/dev/null
[[ "$(tailscale ip -4)" == "$server_ip" ]] || { echo 'Server VPN address does not match' >&2; exit 2; }
[[ -f "$policy_file" ]] || { echo 'Reviewed full --policy-file required' >&2; exit 2; }
python3 "$script_dir/tailscale_policy.py" audit --windows-ip "$win_ip" --server-ip "$server_ip" --current "$policy_file"
if systemctl is-active --quiet xrdp.service; then
  echo 'An active RDP service exists. Inspect it and reuse a matching deployment; do not disconnect active users.' >&2
  exit 2
fi
systemctl mask xrdp.service xrdp-sesman.service
fail_closed() {
  local result=$?
  trap - ERR
  systemctl mask --now xrdp.service xrdp-sesman.service >/dev/null 2>&1 || true
  echo 'Installation failed; RDP is stopped and masked. Existing SSH and other firewall tables were preserved.' >&2
  exit "$result"
}
trap fail_closed ERR
dnf install -y dnf-plugins-core
dnf config-manager --set-enabled crb
dnf install -y epel-release
dnf install -y xrdp xorgxrdp xfce4-session xfce4-panel xfce4-settings xfwm4 xfdesktop thunar xfce4-terminal \
  dbus-x11 gnome-keyring gnome-keyring-pam libsecret firefox xdg-utils xorg-x11-server-Xorg xorg-x11-xinit xorg-x11-xauth \
  dejavu-sans-fonts google-noto-sans-cjk-ttc-fonts nftables gtk3 libnotify nss at-spi2-core mesa-libgbm \
  libdrm libXtst libuuid pipewire-libs xdg-desktop-portal xdg-desktop-portal-gtk alsa-lib libXScrnSaver \
  libxkbcommon-x11 xorg-x11-server-Xvfb bubblewrap socat curl gnupg2 binutils tar xz
python3 "$script_dir/linux_session.py" --desktop-user "$desktop_user" --centos-pam --apply

# Download only from the official repository; validate its published identity,
# signed index, index SHA256 and package SHA256 before extracting anything.
cache="$(mktemp -d /var/tmp/hostcontrol-claude.XXXXXXXX)"
chmod 700 "$cache"
cd "$cache"
base=https://downloads.claude.ai/claude-desktop/apt/stable
curl --retry 2 --connect-timeout 10 --max-time 60 -fsSL https://downloads.claude.ai/claude-desktop/key.asc -o key.asc
fpr="$(gpg --show-keys --with-colons key.asc 2>/dev/null | awk -F: '$1=="fpr" {print $10; exit}')"
[[ "$fpr" == 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE ]] || { echo 'Official signing key identity mismatch' >&2; false; }
gpg --batch --dearmor -o key.gpg key.asc
curl --retry 2 --connect-timeout 10 --max-time 60 -fsSL "$base/dists/stable/InRelease" -o InRelease
gpgv --keyring "$cache/key.gpg" InRelease
curl --retry 2 --connect-timeout 10 --max-time 60 -fsSL "$base/dists/stable/main/binary-amd64/Packages" -o Packages
python3 - <<'PY'
import hashlib,pathlib,re,json
release=pathlib.Path('InRelease').read_text()
section=re.search(r'^SHA256:\n((?: .+\n)+)',release,re.M)
if not section: raise SystemExit('Missing signed SHA256 index')
expected=next(row.split()[0] for row in section.group(1).splitlines()
              if row.split()[2]=='main/binary-amd64/Packages')
if hashlib.sha256(pathlib.Path('Packages').read_bytes()).hexdigest()!=expected:
    raise SystemExit('Signed index SHA256 mismatch')
entries=[]
for block in pathlib.Path('Packages').read_text().split('\n\n'):
    item={line.split(': ',1)[0]:line.split(': ',1)[1] for line in block.splitlines()
          if ': ' in line and not line.startswith(' ')}
    if item.get('Package')=='claude-desktop' and item.get('Architecture')=='amd64': entries.append(item)
if not entries: raise SystemExit('No official amd64 package available')
item=max(entries,key=lambda x:tuple(int(v) for v in re.findall(r'\d+',x['Version'])))
if not re.fullmatch(r'pool/main/c/claude-desktop/claude-desktop_[A-Za-z0-9.+~_-]+_amd64\.deb',item['Filename']):
    raise SystemExit('Unexpected package download path')
pathlib.Path('selected.json').write_text(json.dumps(item))
pathlib.Path('selected.path').write_text(item['Filename'])
PY
package_path="$(cat selected.path)"
curl --retry 2 --connect-timeout 10 --max-time 300 -fsSL "$base/$package_path" -o claude-desktop.deb
python3 - <<'PY'
import pathlib,json,hashlib
item=json.loads(pathlib.Path('selected.json').read_text())
if hashlib.sha256(pathlib.Path('claude-desktop.deb').read_bytes()).hexdigest()!=item['SHA256']:
    raise SystemExit('Official package SHA256 mismatch')
PY
install -d -m 0755 /opt/claude-desktop-centos
if [[ -e /opt/claude-desktop-centos/usr ]]; then
  echo 'Claude installation already exists. Inspect/version-check it rather than overwriting it.' >&2
  false
fi
ar x claude-desktop.deb
tar --no-same-owner -xf data.tar.* -C /opt/claude-desktop-centos
claude=/opt/claude-desktop-centos/usr/lib/claude-desktop/claude-desktop
[[ -x "$claude" ]]
if ldd "$claude" | grep -q 'not found'; then
  echo 'Unresolved CentOS dependencies; native adaptation is incomplete' >&2
  false
fi
[[ ! -e /usr/local/bin/claude-desktop ]] || { echo 'Existing Claude launcher requires review' >&2; false; }
ln -s "$claude" /usr/local/bin/claude-desktop
install -m 0644 /opt/claude-desktop-centos/usr/share/applications/com.anthropic.Claude.desktop /usr/share/applications/com.anthropic.Claude.desktop
home_dir="$(getent passwd "$desktop_user" | cut -d: -f6)"
runuser -u "$desktop_user" -- env HOME="$home_dir" XDG_CONFIG_HOME="$home_dir/.config" "$claude" --version
runuser -u "$desktop_user" -- env HOME="$home_dir" XDG_CONFIG_HOME="$home_dir/.config" \
  xdg-mime default com.anthropic.Claude.desktop x-scheme-handler/claude

# Configure before enabling. Never bind RDP on the public address.
backup="/etc/xrdp/xrdp.ini.hostcontrol.$(date -u +%Y%m%dT%H%M%SZ).bak"
cp -n /etc/xrdp/xrdp.ini "$backup"
python3 "$script_dir/desktop_config.py" configure --server-ip "$server_ip" --clipboard "$clipboard" --apply
python3 "$script_dir/desktop_config.py" check
python3 "$script_dir/launcher.py" linux --desktop-user "$desktop_user" --apply
install -d -m 700 /etc/hostcontrol
cat > /etc/hostcontrol/rdp.nft <<EOF
add table inet hostcontrol
flush table inet hostcontrol
table inet hostcontrol {
  chain rdp_guard {
    type filter hook input priority -150; policy accept;
    tcp dport 3389 ip daddr != $server_ip drop
    meta nfproto ipv6 tcp dport 3389 drop
    ip daddr $server_ip tcp dport 3389 iifname != "tailscale0" drop
    ip daddr $server_ip tcp dport 3389 ip saddr != $win_ip drop
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
systemctl unmask xrdp.service xrdp-sesman.service
systemctl enable --now xrdp.service
trap - ERR
printf 'Installed native CentOS adaptation. RDP=%s:3389 user=%s. Claude login, MCP and GUI acceptance are still required. Backup=%s\n' "$server_ip" "$desktop_user" "$backup"
echo 'Generate a matching client file with desktop_config.py rdp, using the same --clipboard choice.'
