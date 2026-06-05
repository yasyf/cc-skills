#requires -version 5.1
<#
.SYNOPSIS
  Fetches the prebuilt slop-cop binary for the current host into the plugin's
  persistent data directory ($env:CLAUDE_PLUGIN_DATA\bin\slop-cop.exe) and
  writes its path to stdout. Run by the plugin's SessionStart hook and, as a
  fallback, by the slop-cop skill on Windows. Idempotent; safe to re-run.
#>

$ErrorActionPreference = 'Stop'

# Persistent home for the binary (survives plugin updates). Fall back to a
# cache dir when CLAUDE_PLUGIN_DATA is unset.
$dataDir = if ($env:CLAUDE_PLUGIN_DATA) { $env:CLAUDE_PLUGIN_DATA } else { Join-Path $env:LOCALAPPDATA 'slop-cop' }
$binDir = Join-Path $dataDir 'bin'
$binPath = Join-Path $binDir 'slop-cop.exe'

# Fast path: binary already works.
if (Test-Path $binPath) {
    try { & $binPath version | Out-Null; Write-Output $binPath; exit 0 } catch { }
}

switch ($env:PROCESSOR_ARCHITECTURE) {
    'AMD64' { $arch = 'amd64' }
    'ARM64' { $arch = 'arm64' }
    default { throw "install-binary.ps1: unsupported arch: $env:PROCESSOR_ARCHITECTURE" }
}

$zip = "slop-cop_windows_${arch}.zip"
# /releases/latest/download/<asset> is GitHub's native redirect to the newest
# release's asset. Invoke-WebRequest follows the 302 by default.
$url = "https://github.com/yasyf/slop-cop/releases/latest/download/$zip"

New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$tmp = New-Item -ItemType Directory -Path ([IO.Path]::GetTempPath()) -Name ([Guid]::NewGuid())
try {
    $archive = Join-Path $tmp.FullName 'slop-cop.zip'
    Write-Host "install-binary.ps1: downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
    Expand-Archive -LiteralPath $archive -DestinationPath $tmp.FullName -Force
    $src = Join-Path $tmp.FullName "slop-cop_windows_${arch}\slop-cop.exe"
    Move-Item -Force -Path $src -Destination $binPath
    & $binPath version | Out-Null
    Write-Host "install-binary.ps1: installed $binPath"
    Write-Output $binPath
}
finally {
    Remove-Item -Recurse -Force $tmp.FullName -ErrorAction SilentlyContinue
}
