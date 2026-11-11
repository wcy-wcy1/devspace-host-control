param([string]$LaunchConfig = (Join-Path $PSScriptRoot 'launch.json'))
$ErrorActionPreference = 'Stop'

function Test-HCAgentMatch($Process, [string]$Executable, [string]$Config) {
    if (-not $Process -or -not $Process.ExecutablePath -or
        [IO.Path]::GetFullPath($Process.ExecutablePath) -ne $Executable) { return $false }
    $hcMatch = [regex]::Match($Process.CommandLine, '(?:^|\s)--?config(?:=|\s+)(?:"([^"]+)"|(\S+))')
    if (-not $hcMatch.Success) { return $false }
    $hcArgument = if ($hcMatch.Groups[1].Success) { $hcMatch.Groups[1].Value } else { $hcMatch.Groups[2].Value }
    return [IO.Path]::GetFullPath($hcArgument) -eq $Config
}

function Start-HostControl([string]$ConfigPath) {
    $hcRoot = Split-Path -Parent (Resolve-Path -LiteralPath $ConfigPath).Path
    $hcLaunch = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($hcLaunch.schema_version -ne 1) { throw 'Unsupported launcher configuration.' }
    $hcExe = (Resolve-Path -LiteralPath $hcLaunch.agent_exe).Path
    $hcConfig = (Resolve-Path -LiteralPath $hcLaunch.agent_config).Path
    $hcAgent = Get-Content -LiteralPath $hcConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($hcAgent.listen -ne ('{0}:{1}' -f $hcLaunch.listen_ip, $hcLaunch.listen_port)) {
        throw 'Agent listener changed. Regenerate the launcher from the current configuration.'
    }
    if (-not (Get-NetIPAddress -IPAddress $hcLaunch.listen_ip -ErrorAction SilentlyContinue)) {
        throw 'The paired Windows Tailscale address is unavailable. Sign in to Tailscale first.'
    }
    $hcRdp = Join-Path $hcRoot 'server-desktop.rdp'
    if (-not (Test-Path -LiteralPath $hcRdp)) { throw 'RDP connection file is missing.' }
    $hcListeners = @(Get-NetTCPConnection -LocalAddress $hcLaunch.listen_ip -LocalPort $hcLaunch.listen_port -State Listen -ErrorAction SilentlyContinue)
    if ($hcListeners.Count) {
        foreach ($hcListener in $hcListeners) {
            $hcProcess = Get-CimInstance Win32_Process -Filter ('ProcessId={0}' -f $hcListener.OwningProcess)
            if (-not (Test-HCAgentMatch $hcProcess $hcExe $hcConfig)) {
                throw 'The agent endpoint is owned by a different process/configuration; no duplicate was started.'
            }
        }
        Write-Host 'Reusing the configured agent with its current local session state.'
    } else {
        $hcWrapper = Join-Path $hcRoot 'start-agent.ps1'
        if (-not (Test-Path -LiteralPath $hcWrapper)) { throw 'Agent launcher is missing.' }
        # Visible user-session console. No enable flag, hidden service or elevation.
        $hcChild = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $hcWrapper)) -WindowStyle Normal -PassThru
        $hcReady = $false
        for ($hcAttempt = 0; $hcAttempt -lt 40; $hcAttempt++) {
            Start-Sleep -Milliseconds 250
            $hcChild.Refresh()
            if ($hcChild.HasExited) { throw 'The agent console exited before becoming ready.' }
            $hcCurrent = @(Get-NetTCPConnection -LocalAddress $hcLaunch.listen_ip -LocalPort $hcLaunch.listen_port -State Listen -ErrorAction SilentlyContinue)
            if ($hcCurrent.Count) {
                foreach ($hcListener in $hcCurrent) {
                    $hcProcess = Get-CimInstance Win32_Process -Filter ('ProcessId={0}' -f $hcListener.OwningProcess)
                    if (-not (Test-HCAgentMatch $hcProcess $hcExe $hcConfig)) {
                        throw 'Unexpected process at the configured endpoint; RDP was not opened.'
                    }
                }
                $hcReady = $true
                break
            }
        }
        if (-not $hcReady) { throw 'Agent did not become ready. Inspect its visible console.' }
        Write-Host 'Agent ready, initially paused. Use its private local page to enable a bounded session.'
    }
    Start-Process -FilePath 'mstsc.exe' -ArgumentList ('"{0}"' -f $hcRdp) | Out-Null
    Write-Host 'Remote Desktop opened. Claude starts in the Linux session on login/reconnect.'
}

if ($MyInvocation.InvocationName -ne '.') {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'This double-click entry requires Windows.' }
    # Serialize launch attempts; never run a second copy while this one is starting.
    $hcMutex = New-Object Threading.Mutex($false, 'Local\HostControl-DoubleClick-Launcher')
    $hcAcquired = $false
    try {
        try { $hcAcquired = $hcMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $hcAcquired = $true }
        if ($hcAcquired) { Start-HostControl $LaunchConfig }
        else { Write-Host 'A launcher is already starting. Wait for its agent console and Remote Desktop.' }
    } finally {
        if ($hcAcquired) { $hcMutex.ReleaseMutex() }
        $hcMutex.Dispose()
    }
}
