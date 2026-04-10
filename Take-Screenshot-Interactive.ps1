# C:\Scripts\Take-Screenshot-Interactive.ps1
# Runs Take-Screenshot.ps1 in the active desktop session via PsExec.
$ErrorActionPreference = "Stop"
$PsExec = "C:\Tools\Sysinternals\PsExec.exe"
$Script = "C:\Scripts\Take-Screenshot.ps1"

if (-not (Test-Path $PsExec)) { throw "PsExec not found: $PsExec" }
if (-not (Test-Path $Script)) { throw "Script not found: $Script" }

# Get active session ID via query user
$sessionId = $null
try {
    $lines = & query user 2>$null
    foreach ($line in $lines) {
        if ($line -match '\s+(\d+)\s+Active') {
            $sessionId = [int]$Matches[1]
            break
        }
    }
} catch {}

# Fallback: explorer process session
if (-not $sessionId) {
    $sessionId = (Get-Process -Name explorer -ErrorAction SilentlyContinue | Select-Object -First 1).SessionId
}
if (-not $sessionId) { throw "No active desktop session found." }

Write-Host "[SS] Using session $sessionId"

$psArgs = "-accepteula -nobanner -s -i $sessionId -h powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Script`""
$proc = Start-Process -FilePath $PsExec -ArgumentList $psArgs -PassThru -WindowStyle Hidden
$proc.WaitForExit()
exit $proc.ExitCode
