# ================================
# Start-Rust.ps1  (idempotent, robust)
# - Exit 1 if Rust already running (so the bot can report it)
# - Exit 0 on successful launch attempt
# - Nonzero on error
# ================================

param([string]$Server = "205.178.168.84")
$ErrorActionPreference = "Stop"
$Password = ""  # set if needed

function Is-RustRunning {
    $p = Get-Process -Name "RustClient" -ErrorAction SilentlyContinue
    return $null -ne $p
}

function Get-SteamExe {
    try {
        $cu = Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction Stop
        if ($cu.PSObject.Properties.Name -contains 'SteamExe' -and (Test-Path $cu.SteamExe)) { return $cu.SteamExe }
        if ($cu.PSObject.Properties.Name -contains 'SteamPath') {
            $p = Join-Path $cu.SteamPath 'steam.exe'
            if (Test-Path $p) { return $p }
        }
    } catch {}

    try {
        $lm32 = Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam' -ErrorAction Stop
        if ($lm32.PSObject.Properties.Name -contains 'InstallPath') {
            $p = Join-Path $lm32.InstallPath 'steam.exe'
            if (Test-Path $p) { return $p }
        }
    } catch {}

    foreach ($c in @("$Env:ProgramFiles(x86)\Steam\steam.exe", "$Env:ProgramFiles\Steam\steam.exe", "$Env:LocalAppData\Steam\steam.exe")) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    throw "Could not locate steam.exe automatically."
}

function Ensure-Steam {
    param([string]$SteamExe)
    if (-not (Get-Process -Name "steam" -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath $SteamExe
        Start-Sleep -Seconds 10
    }
}

# ---- Early exit if Rust is already up ----
if (Is-RustRunning) {
    Write-Host "Rust is already running; exiting."
    exit 1   # special "already running"
}

# ---- Build connect args ----
if ([string]::IsNullOrWhiteSpace($Password)) {
    $connectArg = "+connect $Server"
} else {
    $connectArg = "+connect $Server; password $Password"
}

try {
    # ---- Try via Steam first ----
    $steam = Get-SteamExe
    Ensure-Steam -SteamExe $steam
    Start-Process -FilePath $steam -ArgumentList @("-applaunch","252490",$connectArg)
} catch {
    # ---- Fallback: locate RustClient.exe directly ----
    $defaultRust = "$Env:ProgramFiles(x86)\Steam\steamapps\common\Rust\RustClient.exe"
    $rustExe = if (Test-Path $defaultRust) { $defaultRust } else { $null }
    if (-not $rustExe) {
        $libVdf = "$Env:ProgramFiles(x86)\Steam\steamapps\libraryfolders.vdf"
        if (Test-Path $libVdf) {
            $vdf = Get-Content $libVdf -Raw
            $paths = ([regex]'"path"\s+"([^"]+)"').Matches($vdf) | ForEach-Object { $_.Groups[1].Value }
            foreach ($lib in $paths) {
                $candidate = Join-Path $lib 'steamapps\common\Rust\RustClient.exe'
                if (Test-Path $candidate) { $rustExe = $candidate; break }
            }
        }
    }
    if (-not $rustExe) { Write-Error "RustClient.exe not found."; exit 3 }
    if ($Password) {
        Start-Process -FilePath $rustExe -ArgumentList @("+client.connect",$Server,";","password",$Password)
    } else {
        Start-Process -FilePath $rustExe -ArgumentList @("+client.connect",$Server)
    }
}

# ---- Confirm spawn briefly, then exit 0 ----
for ($i=0; $i -lt 30; $i++) {
    if (Is-RustRunning) { break }
    Start-Sleep -Seconds 1
}
Write-Host "Launch attempt complete."
exit 0
