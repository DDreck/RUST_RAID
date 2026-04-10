# rust_raid — Project Overview

Automated system that receives an IFTTT Telegram message, triggers an ESP32 alarm, and (with physical button confirmation) boots a Windows PC and launches Rust.

## Flow

1. IFTTT detects a Rust wipe/raid notification → sends message to Telegram group
2. **rust_userbot.py** (Raspberry Pi `drew@10.0.0.229`) receives it via Telethon
3. Triggers ESP32 alarm for 2 seconds (buzzer + LED blink)
4. User presses the **physical button** on the ESP32 → ESP32 calls Pi's HTTP server at `:8080/trigger`
5. Pi sends Wake-on-LAN packet to Windows PC, waits for it to come online
6. SSHes in and runs the PowerShell launcher → Steam opens Rust and connects to the server
7. 20 seconds after Rust starts → Pi SSHes in, takes a screenshot of the middle monitor, and sends it to the Telegram group

## Files

| Local name | Deployed location | Purpose |
|------------|------------------|---------|
| `pi.py` | `~/rust_userbot.py` on Pi | Telegram userbot, WOL, SSH orchestration, HTTP trigger server |
| `esp32.ino` | Arduino IDE | Alarm controller (buzzer + LED + button + Pi callback) |
| `Start-Rust-Interactive.ps1` | `C:\Scripts\Start-Rust-Interactive.ps1` | PsExec wrapper — runs Start-Rust.ps1 in active desktop session |
| `Start-Rust.ps1` | `C:\Scripts\Start-Rust.ps1` | Finds Steam, launches Rust with +connect, accepts `-Server` param |
| `Take-Screenshot.ps1` | `C:\Scripts\Take-Screenshot.ps1` | Takes screenshot via PrintScreen+Clipboard, saves to C:\Temp\rust_ss.png — must run via PsExec |
| `Take-Screenshot-Interactive.ps1` | `C:\Scripts\Take-Screenshot-Interactive.ps1` | PsExec wrapper — runs Take-Screenshot.ps1 in active desktop session |
| `Get-ScreenshotB64.ps1` | `C:\Scripts\Get-ScreenshotB64.ps1` | Reads C:\Temp\rust_ss.png, outputs as base64 to stdout |
| `rustbot.env` | `~/rust_userbot.env` on Pi | All secrets and config (loaded by rust_userbot.py) — **not in repo** |

> The Pi home dir has no `rust_raid/` subfolder — all files live directly in `~`.

## ESP32 Hardware (DOIT DevKit V1)

| Pin | Assignment |
|-----|-----------|
| 17 (TX2) | External LED |
| 2 | Onboard LED (kept OFF) |
| 16 (RX2) | Active buzzer (LOW = ON) |
| 4 | Button (INPUT_PULLUP, to GND) |

mDNS hostname: `esp32-alarm.local`

HTTP endpoints:
- `GET /alarm/start` — start 2-second alarm (auto-clears lockout)
- `GET /alarm/stop` — stop alarm + enter lockout
- `GET /status` — returns `{"alarm": bool, "locked": bool}`

On button press, the ESP32 calls `http://10.0.0.229:8080/trigger` (Pi's HTTP server).

## Pi HTTP Server

The Pi runs an aiohttp server on port 8080 alongside the Telegram bot.

- `GET /trigger` — fires the boot + Rust launch sequence (with COOLDOWN_SECS cooldown)

## Telegram Commands

| Command | Action |
|---------|--------|
| `/rust` (or trigger phrase) | Fire alarm only — press ESP32 button to launch |
| `/boot` | WOL only — boot PC without Rust or alarm |
| `/rustnoalarm` | WOL + launch Rust, skip alarm |
| `/screenshot` | Manually take and send a screenshot of the middle monitor |
| `/alarm` | Test ESP32 alarm (2s) |
| `/stopalarm` | Stop ESP32 alarm immediately |
| `/server` | Show current Rust server IP |
| `/setserver <ip>` | Change Rust server IP (persisted to `~/rust_server.txt`) |
| `/help` | List all commands with current server IP |

## Screenshot Flow

Screenshots cannot be taken directly via SSH — the SSH process has no display context. The two-step approach via PsExec is required:

1. SSH → `Take-Screenshot-Interactive.ps1` → PsExec (`-s -i [SessionID]`) → `Take-Screenshot.ps1` runs in active desktop session → saves `C:\Temp\rust_ss.png`
2. SSH → `Get-ScreenshotB64.ps1` → reads file, outputs ASCII base64 → Pi decodes + sends to Telegram

### How Take-Screenshot.ps1 works
Uses `SendKeys.SendWait('{PRTSC}')` to fire the PrintScreen key in the interactive session, reads the image from the clipboard, crops to the middle monitor using virtual screen coordinates, resizes to max 1280px wide, saves as PNG.

### How Take-Screenshot-Interactive.ps1 works
1. Runs `query user` and parses the active session ID with regex `\s+(\d+)\s+Active`
2. Falls back to finding explorer's SessionId if query user returns nothing
3. Calls PsExec with `-s -i [SessionID] -h` to run Take-Screenshot.ps1 as SYSTEM in the active interactive session

### Why these specific approaches were chosen (hard-won findings)
- **GDI `GetDC(IntPtr.Zero)` does NOT work via SSH** — the SSH process has no window station with a display; GetDC returns null, BitBlt silently does nothing, resulting in a tiny/corrupt PNG (~105 bytes)
- **`Screen.AllScreens` returns bad bounds via SSH** — screen enumeration doesn't work without a display context
- **`GetWindowDC(GetDesktopWindow())` also fails via SSH** — same root cause
- **PsExec needs `-s` (SYSTEM) and `-i [SessionID]`** — without `-s`, PsExec may not have permission to inject into the session; without correct session ID it runs in session 0 (no display)
- **Session ID via `query user`** — more reliable than getting explorer's SessionId; use regex `\s+(\d+)\s+Active` to parse the output
- **`Get-ScreenshotB64.ps1` uses `[Console]::OutputEncoding = ASCII`** — prevents PowerShell UTF-16 encoding artifacts from corrupting the base64 string when read back via SSH in Python

User has 3 monitors. Rust is on the **middle monitor** (index 1 when screens sorted by X/left position). PrintScreen captures all monitors; the script crops using virtual screen coordinates (`SystemInformation.VirtualScreen.X/Y` as origin offset).

## Key Config (`~/rust_userbot.env` on Pi)

```
SSH_HOST=10.0.0.75          # Windows PC local IP
SSH_USER=dreww
PC_MAC=60-CF-84-84-1B-D9    # NIC MAC for WOL
BROADCAST_IP=10.0.0.255
PC_HOSTNAME_OR_IP=10.0.0.75
PING_TIMEOUT_SECONDS=600
COOLDOWN_SECS=20
ESP32_HOST=esp32-alarm.local
PI_HTTP_PORT=8080
GROUP_ID=-1002958072911
TRIGGER_PHRASES=/rust
WIN_CMD=powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Scripts\Start-Rust-Interactive.ps1"
```

## Rust Server

`205.178.168.84` — default, stored in `~/rust_server.txt` on Pi (changeable via `/setserver`).
No port needed — Rust default port is used.

## Deployment

**IMPORTANT: All 5 PowerShell scripts must be deployed. Missing any one will cause silent failures.**
`Get-ScreenshotB64.ps1` in particular is easy to forget — if it's missing, the bot reports a base64 decode error rather than a clear "file not found" message.

```bash
# Pi
scp pi.py drew@10.0.0.229:~/rust_userbot.py
ssh drew@10.0.0.229 "nohup python3 /home/drew/rust_userbot.py > /home/drew/rustbot.log 2>&1 &"
ssh drew@10.0.0.229 "cat ~/rustbot.log"

# Windows PS scripts (run all 5 every time)
cp Start-Rust-Interactive.ps1        C:/Scripts/Start-Rust-Interactive.ps1
cp Start-Rust.ps1                    C:/Scripts/Start-Rust.ps1
cp Take-Screenshot.ps1               C:/Scripts/Take-Screenshot.ps1
cp Take-Screenshot-Interactive.ps1   C:/Scripts/Take-Screenshot-Interactive.ps1
cp Get-ScreenshotB64.ps1             C:/Scripts/Get-ScreenshotB64.ps1

# ESP32: flash esp32.ino via Arduino IDE
# PI_HOST hardcoded as "10.0.0.229", PI_PORT 8080
```

### Restarting the bot
Kill and start must be separate SSH commands — chaining with `&&` or `;` causes exit code 255:
```bash
ssh drew@10.0.0.229 'pkill -f userbot'   # exit 1 = not running (OK), exit 0 = killed
ssh drew@10.0.0.229 "nohup python3 /home/drew/rust_userbot.py > /home/drew/rustbot.log 2>&1 &"
ssh drew@10.0.0.229 "cat ~/rustbot.log"
```

## Notes

- Session file `rustrun.session` lives in Pi home dir — keeps Telegram auth persistent
- PsExec is at `C:\Tools\Sysinternals\PsExec.exe`
- SSH to Pi: `ssh drew@10.0.0.229` (key-based, no password prompt needed from this machine)
- `pkill -f rust_userbot.py` fails via bash double-quote expansion — use single quotes: `pkill -f userbot`
- `C:\Temp\` is created by Take-Screenshot.ps1 if missing
