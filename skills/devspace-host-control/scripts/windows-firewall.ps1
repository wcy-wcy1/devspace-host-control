param(
    [Parameter(Mandatory)][string]$WindowsIP,
    [Parameter(Mandatory)][string]$UbuntuIP,
    [Parameter(Mandatory)][string]$AgentExe,
    [ValidateRange(1,65535)][int]$Port=7443,
    [switch]$Apply,
    [switch]$Remove
)
$ErrorActionPreference='Stop'
function Assert-VPNv4([string]$Value) {
    $parsed=[System.Net.IPAddress]::Parse($Value)
    $b=$parsed.GetAddressBytes()
    if ($b.Length -ne 4 -or $b[0] -ne 100 -or $b[1] -lt 64 -or $b[1] -gt 127) { throw 'This firewall helper requires exact Tailscale IPv4 addresses.' }
}
Assert-VPNv4 $WindowsIP; Assert-VPNv4 $UbuntuIP
if ($WindowsIP -eq $UbuntuIP) { throw 'Two different devices required.' }
$ruleName='HostControl-Agent-Private'
if (-not $Apply) {
    [pscustomobject]@{mode='PLAN';remove=[bool]$Remove;source=$UbuntuIP;destination=$WindowsIP;protocol='TCP';port=$Port;program=$AgentExe;rule=$ruleName} | ConvertTo-Json
    Write-Output 'No changes made. Apply only after the exact Tailscale policy has been installed and reviewed.'
    return
}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Applying firewall rules requires an elevated PowerShell.' }
if ($Remove) { Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule; return }
$agentPath=(Resolve-Path -LiteralPath $AgentExe).Path
if (-not (Get-NetIPAddress -IPAddress $WindowsIP -ErrorAction SilentlyContinue)) { throw 'Windows Tailscale IP is not assigned locally.' }
foreach ($profile in Get-NetFirewallProfile -PolicyStore ActiveStore) {
    if (-not $profile.Enabled -or $profile.DefaultInboundAction -ne 'Block') { throw 'Firewall profile must be enabled with effective inbound default deny; review policy first.' }
}
$conflicts=@()
foreach ($rule in Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow) {
    if ($rule.Name -eq $ruleName) { continue }
    $filter=$rule | Get-NetFirewallPortFilter
    if ($filter.Protocol -notin @('TCP','6','Any','256')) { continue }
    foreach ($candidate in @($filter.LocalPort)) {
        $matchesPort=$candidate -eq 'Any' -or $candidate -eq "$Port"
        if ($candidate -match '^(\d+)-(\d+)$') { $matchesPort=$Port -ge [int]$Matches[1] -and $Port -le [int]$Matches[2] }
        if ($matchesPort) { $conflicts += $rule.Name; break }
    }
}
if ($conflicts.Count) { throw ('Potential broader inbound rules require review; no rules changed: '+($conflicts -join ', ')) }
Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name $ruleName -DisplayName 'HostControl private paired Ubuntu' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -LocalAddress $WindowsIP -RemoteAddress $UbuntuIP -Program $agentPath -Profile Any | Out-Null
Write-Output 'Exact allow rule created. Agent binding rejects other IPs and address families; validate effective Tailscale grants and test from an unapproved device.'
