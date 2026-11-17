param([Parameter(Mandatory=$true)][string]$PrivateDirectory,
      [Parameter(Mandatory=$true)][string]$LauncherDirectory)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Security
New-Item -ItemType Directory -Force -Path $PrivateDirectory | Out-Null
$keyFile=Join-Path $PrivateDirectory 'input-key.dpapi'
if (-not (Test-Path -LiteralPath $keyFile)) {
    $key=New-Object byte[] 32
    $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($key) } finally { $rng.Dispose() }
    try {
        $protected=[Security.Cryptography.ProtectedData]::Protect($key,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
        $stream=[IO.File]::Open($keyFile,[IO.FileMode]::CreateNew)
        try { $stream.Write($protected,0,$protected.Length) } finally { $stream.Dispose() }
    } finally { [Array]::Clear($key,0,$key.Length) }
}
New-Item -ItemType Directory -Force -Path $LauncherDirectory | Out-Null
$scriptSource=Join-Path $PSScriptRoot 'claude-input.ps1'
$scriptTarget=Join-Path $LauncherDirectory 'claude-input.ps1'
if (Test-Path -LiteralPath $scriptTarget) { Copy-Item -LiteralPath $scriptTarget -Destination ($scriptTarget+'.backup-'+[Guid]::NewGuid().ToString('N')) }
Copy-Item -LiteralPath $scriptSource -Destination $scriptTarget
$keyJson=@{key_path=[IO.Path]::GetFullPath($keyFile)} | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $LauncherDirectory 'input-launch.json'),$keyJson,[Text.UTF8Encoding]::new($false))
$wrapper=@'
$ErrorActionPreference='Stop'
$config=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'input-launch.json') -Raw -Encoding UTF8 | ConvertFrom-Json
& (Join-Path $PSScriptRoot 'claude-input.ps1') -KeyPath $config.key_path
'@
[IO.File]::WriteAllText((Join-Path $LauncherDirectory 'open-claude-input.ps1'),$wrapper,[Text.UTF8Encoding]::new($true))
$cmd="@echo off`r`nsetlocal`r`nset `"PSModulePath=`"`r`npowershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File `"%~dp0open-claude-input.ps1`"`r`nif errorlevel 1 pause`r`n"
[IO.File]::WriteAllText((Join-Path $LauncherDirectory 'Claude-Input.cmd'),$cmd,[Text.Encoding]::ASCII)
Write-Output 'Created Claude-Input.cmd. Pair the receiver privately before using it; no credential is printed.'
