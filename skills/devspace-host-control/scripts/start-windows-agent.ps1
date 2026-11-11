param(
    [Parameter(Mandatory)][string]$Config,
    [string]$AgentExe = (Join-Path $PSScriptRoot '../assets/runtime/windows-amd64/hostagent.exe'),
    [ValidateRange(0,480)][int]$EnableForMinutes = 0
)
$ErrorActionPreference = 'Stop'
$hcAgentPath = (Resolve-Path -LiteralPath $AgentExe).Path
$hcConfigPath = (Resolve-Path -LiteralPath $Config).Path
$hcConfig = Get-Content -LiteralPath $hcConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($EnableForMinutes -gt [int]$hcConfig.session_minutes) {
    throw 'Requested authorization exceeds the configured session limit.'
}
& $hcAgentPath --config $hcConfigPath --check
if ($LASTEXITCODE -ne 0) { throw 'Agent configuration check failed.' }
$hcAgentArguments = @('--config', $hcConfigPath)
if ($EnableForMinutes -gt 0) {
    $hcAgentArguments += @('--enable-for', "$EnableForMinutes")
    Write-Host "Starting the explicitly requested $EnableForMinutes minute local session."
} else {
    Write-Host 'Agent starts paused. Open its private localhost URL locally to authorize a bounded session.'
}
Write-Host 'Keep the control URL private. Ctrl+C stops the agent. No startup task is installed.'
$hcOriginalModulePath = $env:PSModulePath
try {
    # Windows PowerShell UIA children must compute their own module directories;
    # inherited PowerShell 7 paths can break module autoloading.
    [Environment]::SetEnvironmentVariable('PSModulePath', $null, 'Process')
    & $hcAgentPath @hcAgentArguments
    if ($LASTEXITCODE -ne 0) { throw "Agent exited with code $LASTEXITCODE." }
} finally {
    $env:PSModulePath = $hcOriginalModulePath
}
