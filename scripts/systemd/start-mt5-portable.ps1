param(
    [Parameter(Mandatory = $true)]
    [string]$TerminalPath
)

$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path -LiteralPath $TerminalPath).Path
$workingDirectory = Split-Path -Parent $resolved
$running = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue |
    Where-Object {
        try { $_.Path -eq $resolved } catch { $false }
    }

if (-not $running) {
    Start-Process -FilePath $resolved -ArgumentList "/portable" -WorkingDirectory $workingDirectory
}

