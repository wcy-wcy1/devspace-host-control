param(
    [Parameter(Mandatory)][string]$WindowsIP,
    [Parameter(Mandatory)][Alias('ServerIP')][string]$UbuntuIP,
    [Parameter(Mandatory)][string]$AgentExe,
    [ValidateRange(1,65535)][int]$Port=7443,
    [switch]$Apply,
    [switch]$GuardBroadRules,
    [switch]$EnableDisabledProfiles,
    [string]$ProfileBackup,
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
$guardNames=@('HostControl-Agent-RejectOtherIPv4','HostControl-Agent-RejectIPv6')
if (-not $Apply) {
    [pscustomobject]@{mode='PLAN';remove=[bool]$Remove;source=$UbuntuIP;destination=$WindowsIP;protocol='TCP';port=$Port;program=$AgentExe;rule=$ruleName;guardBroadRules=[bool]$GuardBroadRules;enableDisabledProfiles=[bool]$EnableDisabledProfiles} | ConvertTo-Json
    Write-Output 'No changes made. Apply only after the exact Tailscale policy has been installed and reviewed.'
    return
}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Applying firewall rules requires an elevated PowerShell.' }
if ($Remove) {
    foreach ($hcName in @($ruleName)+$guardNames) {
        Get-NetFirewallRule -Name $hcName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
    return
}
$agentPath=(Resolve-Path -LiteralPath $AgentExe).Path
if (-not (Get-NetIPAddress -IPAddress $WindowsIP -ErrorAction SilentlyContinue)) { throw 'Windows Tailscale IP is not assigned locally.' }
$hcProfiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore)
foreach ($profile in $hcProfiles) {
    if ($profile.DefaultInboundAction -ne 'Block') { throw 'Effective inbound default deny is required; review policy first. No rules changed.' }
    if (-not $profile.Enabled -and -not $EnableDisabledProfiles) { throw 'Disabled firewall profile detected. Ask about enabling it, then pass -EnableDisabledProfiles only if authorized. No rules changed.' }
}
$conflicts=@()
foreach ($rule in Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow) {
    if ($rule.Name -in (@($ruleName)+$guardNames)) { continue }
    $filter=$rule | Get-NetFirewallPortFilter
    if ($filter.Protocol -notin @('TCP','6','Any','256')) { continue }
    foreach ($candidate in @($filter.LocalPort)) {
        $matchesPort=$candidate -eq 'Any' -or $candidate -eq "$Port"
        if ($candidate -match '^(\d+)-(\d+)$') { $matchesPort=$Port -ge [int]$Matches[1] -and $Port -le [int]$Matches[2] }
        if ($matchesPort) { $conflicts += $rule.Name; break }
    }
}
if ($conflicts.Count -and -not $GuardBroadRules) { throw ('Potential broader inbound rules require review; no rules changed: '+($conflicts -join ', ')) }
if ($EnableDisabledProfiles) {
    if (-not $ProfileBackup) {
        $hcBackupDirectory=Join-Path $env:LOCALAPPDATA 'DevSpaceHostControl\automation'
        New-Item -ItemType Directory -Path $hcBackupDirectory -Force | Out-Null
        $ProfileBackup=Join-Path $hcBackupDirectory ('firewall-before-'+[guid]::NewGuid().ToString('N')+'.json')
    }
    if (Test-Path -LiteralPath $ProfileBackup) { throw 'Before-state backup already exists; choose a new path. No rules changed.' }
    $hcProfiles | Select-Object Name,Enabled,DefaultInboundAction | ConvertTo-Json | Set-Content -LiteralPath $ProfileBackup -Encoding UTF8
    Write-Output ('Firewall before-state backup: '+$ProfileBackup)
}
if ($GuardBroadRules) {
    # A program-scoped source-deny takes precedence over any generic allow rule.
    # Use the complement of the one approved IPv4 peer, plus an IPv6 deny.
    $hcBytes=[System.Net.IPAddress]::Parse($UbuntuIP).GetAddressBytes()
    $hcNumber=([uint64]$hcBytes[0]*16777216)+([uint64]$hcBytes[1]*65536)+([uint64]$hcBytes[2]*256)+[uint64]$hcBytes[3]
    function Convert-HCIPv4([uint64]$Number) {
        '{0}.{1}.{2}.{3}' -f (($Number -shr 24) -band 255),(($Number -shr 16) -band 255),(($Number -shr 8) -band 255),($Number -band 255)
    }
    $hcSources=@(('0.0.0.0-'+(Convert-HCIPv4 ($hcNumber-1))),((Convert-HCIPv4 ($hcNumber+1))+'-255.255.255.255'))
    foreach ($hcName in $guardNames) { Get-NetFirewallRule -Name $hcName -ErrorAction SilentlyContinue | Remove-NetFirewallRule }
    New-NetFirewallRule -Name $guardNames[0] -DisplayName 'HostControl deny other IPv4 sources' -Direction Inbound -Action Block -Protocol TCP -LocalPort $Port -RemoteAddress $hcSources -Program $agentPath -Profile Any | Out-Null
    New-NetFirewallRule -Name $guardNames[1] -DisplayName 'HostControl deny IPv6 access' -Direction Inbound -Action Block -Protocol TCP -LocalPort $Port -RemoteAddress '::/0' -Program $agentPath -Profile Any | Out-Null
}
Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name $ruleName -DisplayName 'HostControl private paired Ubuntu' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -LocalAddress $WindowsIP -RemoteAddress $UbuntuIP -Program $agentPath -Profile Any | Out-Null
if ($EnableDisabledProfiles) {
    $hcDisabled=@($hcProfiles | Where-Object { -not $_.Enabled } | Select-Object -ExpandProperty Name)
    if ($hcDisabled.Count) { Set-NetFirewallProfile -Name $hcDisabled -Enabled True }
}
Write-Output 'Exact allow rule created. Agent binding rejects other IPs and address families; validate effective Tailscale grants and test from an unapproved device.'
