# C:\Scripts\Start-Rust-Interactive.ps1
param([string]$Server = "")
$ErrorActionPreference = "Stop"

$PsExec   = "C:\Tools\Sysinternals\PsExec.exe"
$Launcher = "C:\Scripts\Start-Rust.ps1"

if (-not (Test-Path $PsExec))   { throw "PsExec not found: $PsExec" }
if (-not (Test-Path $Launcher)) { throw "Launcher not found: $Launcher" }

# Find active desktop session
$sessionId = (Get-Process -Name explorer -ErrorAction SilentlyContinue | Select-Object -First 1).SessionId
if (-not $sessionId) {
  $active = (quser) -split "`n" | Where-Object { $_ -match "\sActive\s" } | Select-Object -First 1
  if ($active) {
    $parts = ($active -replace "^\s+","") -split "\s+"
    $sid = ($parts | Where-Object { $_ -match '^\d+$' } | Select-Object -First 1)
    if ($sid) { $sessionId = [int]$sid }
  }
}
if (-not $sessionId) { throw "No active desktop session found." }

# Build launcher call, optionally passing -Server
$launchCmd = if ($Server) {
  "& `"$Launcher`" -Server `"$Server`"; exit `$LASTEXITCODE"
} else {
  "& `"$Launcher`"; exit `$LASTEXITCODE"
}

# Run Start-Rust.ps1 in that session
$psexecArgs = @(
  "-accepteula",
  "-nobanner",
  "-i", "$sessionId",
  "-h",
  "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
  "-Command", $launchCmd
)

$proc = Start-Process -FilePath $PsExec -ArgumentList ($psexecArgs -join " ") -PassThru -WindowStyle Hidden
$proc.WaitForExit()
exit $proc.ExitCode
