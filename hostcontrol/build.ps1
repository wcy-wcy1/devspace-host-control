param([string]$OutputDirectory = (Join-Path $PSScriptRoot 'dist'), [string]$GoExe = 'go')
$ErrorActionPreference = 'Stop'
$oldOS,$oldArch,$oldCGO,$oldToolchain = $env:GOOS,$env:GOARCH,$env:CGO_ENABLED,$env:GOTOOLCHAIN
try {
    $env:CGO_ENABLED='0'; $env:GOTOOLCHAIN='local'
    Push-Location $PSScriptRoot
    foreach ($target in @(@('windows','amd64'),@('linux','amd64'),@('linux','arm64'))) {
        $env:GOOS=$target[0]; $env:GOARCH=$target[1]
        $dest = Join-Path $OutputDirectory ($target -join '-')
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        $commands = if ($target[0] -eq 'windows') { @('hostagent','hostctl') } else { @('hostmcp','hostctl') }
        foreach ($name in $commands) {
            $suffix = if ($target[0] -eq 'windows') { '.exe' } else { '' }
            & $GoExe build -trimpath -ldflags '-s -w' -o (Join-Path $dest ($name+$suffix)) ('./cmd/'+$name)
            if ($LASTEXITCODE -ne 0) { throw "build failed: $($target -join '-')/$name" }
        }
    }
} finally {
    Pop-Location
    $env:GOOS=$oldOS; $env:GOARCH=$oldArch; $env:CGO_ENABLED=$oldCGO; $env:GOTOOLCHAIN=$oldToolchain
}
Get-ChildItem -LiteralPath $OutputDirectory -Recurse -File | Get-FileHash -Algorithm SHA256 | Format-Table
