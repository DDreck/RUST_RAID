#!/usr/bin/env python3
import os, asyncio, time, socket, subprocess, base64, re, datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telethon import TelegramClient, events
from wakeonlan import send_magic_packet
import aiohttp
from aiohttp import web

# --- Load config ---
load_dotenv(os.path.expanduser('~/rust_userbot.env'))

API_ID   = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
SESSION  = os.getenv('TELEGRAM_SESSION','rustrun')

# Target group
GROUP_ID_ENV   = os.getenv('GROUP_ID','').strip()
GROUP_USERNAME = os.getenv('GROUP_USERNAME','').strip() or None

# Triggers & allowlist
TRIGGERS  = [t.strip().lower() for t in os.getenv('TRIGGER_PHRASES','/rust').split(',') if t.strip()]
ALLOWLIST = [u.strip().lower() for u in os.getenv('ALLOWED_BOT_USERNAMES','').split(',') if u.strip()]

# Windows / SSH
SSH_HOST  = os.getenv('SSH_HOST')
SSH_USER  = os.getenv('SSH_USER','dreww')
SSH_PASS  = os.getenv('SSH_PASSWORD','')
SSH_KEY   = os.getenv('SSH_KEY','')
WIN_CMD   = os.getenv('WIN_CMD',  r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Scripts\Start-Rust-Interactive.ps1"')
BOOT_CMD  = os.getenv('BOOT_CMD', r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Scripts\Boot-Only.ps1"')
SS_TAKE_CMD = r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Scripts\Take-Screenshot-Interactive.ps1"'
SS_READ_CMD = r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Scripts\Get-ScreenshotB64.ps1"'

# WOL / reachability
PC_MAC        = os.getenv('PC_MAC','').strip()
BROADCAST_IP  = os.getenv('BROADCAST_IP','255.255.255.255').strip()
PC_IP_OR_HOST = os.getenv('PC_HOSTNAME_OR_IP', SSH_HOST).strip()
PING_TIMEOUT  = int(os.getenv('PING_TIMEOUT_SECONDS','300'))
COOLDOWN      = int(os.getenv('COOLDOWN_SECS','20'))
last_run      = [0.0]

# Scheduled server joins
_scheduled_jobs: dict = {}  # job_id -> {'task': Task, 'target': datetime, 'server': str}
_job_id_counter = [0]

# ESP32 endpoints
ESP32_HOST       = os.getenv('ESP32_HOST', 'esp32-alarm.local').strip()
ESP32_URL_START  = f'http://{ESP32_HOST}/alarm/start'
ESP32_URL_STOP   = f'http://{ESP32_HOST}/alarm/stop'
ESP32_URL_STATUS = f'http://{ESP32_HOST}/status'

# HTTP server port for ESP32 button callbacks
PI_HTTP_PORT = int(os.getenv('PI_HTTP_PORT', '8080'))

# Rust server IP — persisted to ~/rust_server.txt
RUST_SERVER_FILE = os.path.expanduser('~/rust_server.txt')
RUST_SERVER      = [os.getenv('RUST_SERVER', '205.178.168.84')]

def load_rust_server():
    if os.path.exists(RUST_SERVER_FILE):
        s = open(RUST_SERVER_FILE).read().strip()
        if s:
            RUST_SERVER[0] = s

def save_rust_server(ip: str):
    RUST_SERVER[0] = ip
    with open(RUST_SERVER_FILE, 'w') as f:
        f.write(ip)

load_rust_server()

# Timezone — persisted to ~/rust_timezone.txt
TIMEZONE_MAP = {
    'EST': 'America/New_York',
    'CST': 'America/Chicago',
    'MST': 'America/Denver',
    'PST': 'America/Los_Angeles',
}
TZ_FILE    = os.path.expanduser('~/rust_timezone.txt')
CURRENT_TZ = ['America/New_York']  # default EST

def load_timezone():
    if os.path.exists(TZ_FILE):
        s = open(TZ_FILE).read().strip()
        if s:
            CURRENT_TZ[0] = s

def save_timezone(iana: str):
    CURRENT_TZ[0] = iana
    with open(TZ_FILE, 'w') as f:
        f.write(iana)

load_timezone()

def parse_schedule_dt(date_str: str, time_str: str) -> datetime.datetime:
    tz  = ZoneInfo(CURRENT_TZ[0])
    now = datetime.datetime.now(tz)
    # Parse time (24h HH:MM)
    try:
        t = datetime.datetime.strptime(time_str, '%H:%M')
    except ValueError:
        raise ValueError(f"Invalid time '{time_str}' — use HH:MM (24h, e.g. 15:30)")
    # Parse date — accept YYYY-MM-DD, MM/DD, or MM-DD
    parsed = None
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d', '%m-%d'):
        try:
            parsed = datetime.datetime.strptime(date_str, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(f"Invalid date '{date_str}' — use YYYY-MM-DD or MM/DD")
    year = parsed.year if parsed.year != 1900 else now.year
    target = now.replace(year=year, month=parsed.month, day=parsed.day,
                         hour=t.hour, minute=t.minute, second=0, microsecond=0)
    # If no year given and target already passed, roll to next year
    if parsed.year == 1900 and target <= now:
        target = target.replace(year=now.year + 1)
    return target

# Shared refs for button-triggered launch
_client = None
_target = None

# --- Helper functions ---
def match_text(s: str) -> bool:
    s = (s or '').lower()
    return any(t in s for t in TRIGGERS)

def is_valid_server(s: str) -> bool:
    return bool(re.match(r'^\d{1,3}(\.\d{1,3}){3}(:\d{1,5})?$', s.strip()))

def host_is_up(host: str) -> bool:
    try:
        r = subprocess.run(
            ["/bin/ping","-c","1","-W","1",host],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass
    try:
        with socket.create_connection((host, 22), timeout=1.5):
            return True
    except Exception:
        return False

async def wait_for_online(host: str, timeout_s: int) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if host_is_up(host):
            return True
        await asyncio.sleep(2)
    return False

async def run_ssh_cmd(command):
    base = []
    if SSH_PASS:
        base = ['sshpass', '-p', SSH_PASS, 'ssh', '-o', 'StrictHostKeyChecking=no']
    else:
        base = ['ssh', '-o', 'StrictHostKeyChecking=no']
    if SSH_KEY:
        base += ['-i', SSH_KEY]
    base += [f'{SSH_USER}@{SSH_HOST}', command]

    proc = await asyncio.create_subprocess_exec(
        *base, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors='ignore'), err.decode(errors='ignore')

# --- ESP32 triggers ---
async def trigger_esp32_start():
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(ESP32_URL_START) as r:
                txt = (await r.text()).strip()
                print(f"[ESP32] {ESP32_URL_START} -> {r.status} {txt}", flush=True)
                return txt or f"HTTP {r.status}"
    except Exception as e:
        print(f"[ESP32] start error: {e}", flush=True)
        return f"ESP32 error: {e}"

async def trigger_esp32_stop():
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(ESP32_URL_STOP) as r:
                txt = (await r.text()).strip()
                print(f"[ESP32] {ESP32_URL_STOP} -> {r.status} {txt}", flush=True)
                return txt or f"HTTP {r.status}"
    except Exception as e:
        print(f"[ESP32] stop error: {e}", flush=True)
        return f"ESP32 error: {e}"

# --- Screenshot: take in interactive session, read back as base64, send to Telegram ---
async def take_and_send_screenshot(chat):
    # Step 1: take screenshot in active desktop session via PsExec
    code, out, err = await run_ssh_cmd(SS_TAKE_CMD)
    if code != 0:
        detail = (out.strip() + "\n" + err.strip()).strip() or f"exit code {code}"
        await _client.send_message(chat, f"Screenshot (step 1) failed:\n{detail}")
        return
    # Step 2: read the saved file as base64
    code, b64_out, err = await run_ssh_cmd(SS_READ_CMD)
    if code != 0 or not b64_out.strip():
        await _client.send_message(chat, f"Screenshot read failed: {err.strip() or 'no output'}")
        return
    try:
        image_data = base64.b64decode(b64_out.strip().replace('\r', '').replace('\n', ''))
        ss_path = '/tmp/rust_ss.png'
        with open(ss_path, 'wb') as f:
            f.write(image_data)
        await _client.send_file(chat, ss_path, caption=f"Rust on {RUST_SERVER[0]}")
    except Exception as e:
        await _client.send_message(chat, f"Screenshot error: {e}")
    finally:
        try:
            os.remove('/tmp/rust_ss.png')
        except Exception:
            pass

# --- Button-triggered launch (called via ESP32 HTTP callback) ---
async def do_launch():
    global _client, _target
    if _client is None or _target is None:
        return
    if time.monotonic() - last_run[0] < COOLDOWN:
        print("[LAUNCH] Skipped by cooldown", flush=True)
        return
    last_run[0] = time.monotonic()

    await _client.send_message(_target, "Button pressed! Waking PC...")
    if PC_MAC:
        send_magic_packet(PC_MAC, ip_address=BROADCAST_IP, port=9)

    if not await wait_for_online(PC_IP_OR_HOST, PING_TIMEOUT):
        await _client.send_message(_target, "PC did not come online in time.")
        return

    await _client.send_message(_target, "PC booted and online. Preparing to launch Rust...")
    await asyncio.sleep(20)

    cmd = WIN_CMD + f' -Server "{RUST_SERVER[0]}"'
    await _client.send_message(_target, f"Launching Rust on {RUST_SERVER[0]}...")
    code, out, err = await run_ssh_cmd(cmd)
    if code == 0:
        await _client.send_message(_target, "Rust started! Taking screenshot in 20s...")
        await asyncio.sleep(20)
        await take_and_send_screenshot(_target)
    elif code == 1:
        await _client.send_message(_target, "Rust already running.")
    else:
        await _client.send_message(_target, f"SSH error: {out or err}")

async def run_scheduled_join(job_id: int, target_dt: datetime.datetime, server: str):
    global _client, _target
    delay = (target_dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
    _scheduled_jobs.pop(job_id, None)
    if _client is None or _target is None:
        return
    print(f"[SCHED] Job #{job_id} firing — joining {server}", flush=True)
    await _client.send_message(_target, f"Scheduled join: waking PC to join {server}...")
    if PC_MAC:
        send_magic_packet(PC_MAC, ip_address=BROADCAST_IP, port=9)
    if not await wait_for_online(PC_IP_OR_HOST, PING_TIMEOUT):
        await _client.send_message(_target, "Scheduled join: PC did not come online in time.")
        return
    await _client.send_message(_target, "PC online. Launching Rust...")
    await asyncio.sleep(20)
    cmd = WIN_CMD + f' -Server "{server}"'
    code, out, err = await run_ssh_cmd(cmd)
    if code == 0:
        await _client.send_message(_target, "Rust started! Taking screenshot in 20s...")
        await asyncio.sleep(20)
        await take_and_send_screenshot(_target)
    elif code == 1:
        await _client.send_message(_target, "Rust already running.")
    else:
        await _client.send_message(_target, f"SSH error: {out or err}")

async def handle_button_trigger(request):
    print("[HTTP] Button trigger received from ESP32", flush=True)
    asyncio.create_task(do_launch())
    return web.Response(text="OK")

# --- Telegram setup ---
client = TelegramClient(SESSION, API_ID, API_HASH)

async def resolve_target():
    if GROUP_ID_ENV:
        try:
            return await client.get_entity(int(GROUP_ID_ENV))
        except Exception as e:
            print(f"[ERR] Could not resolve GROUP_ID={GROUP_ID_ENV}: {e}", flush=True)
    if GROUP_USERNAME:
        try:
            return await client.get_entity(GROUP_USERNAME)
        except Exception as e:
            print(f"[ERR] Could not resolve GROUP_USERNAME={GROUP_USERNAME}: {e}", flush=True)
    print("[ERR] Set GROUP_ID or GROUP_USERNAME in rust_userbot.env", flush=True)
    return None

# --- Main ---
async def main():
    global _client, _target

    await client.start()
    target = await resolve_target()
    if not target:
        return

    _client = client
    _target = target

    title = getattr(target, 'title', None) or ('@' + getattr(target, 'username', ''))
    print(f"[OK] Listening in: {title} (id {target.id})", flush=True)
    print(f"[OK] Rust server: {RUST_SERVER[0]}", flush=True)

    # Start HTTP server for ESP32 button callbacks
    app = web.Application()
    app.router.add_get('/trigger', handle_button_trigger)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PI_HTTP_PORT).start()
    print(f"[HTTP] Button trigger server listening on :{PI_HTTP_PORT}", flush=True)

    @client.on(events.NewMessage(chats=target))
    async def on_new(evt: events.NewMessage.Event):
        sender = await evt.get_sender()
        sname  = (getattr(sender, 'username', '') or '').lower()
        text   = (evt.message.message or '').strip()
        textl  = text.lower()

        if ALLOWLIST and sname not in ALLOWLIST:
            return

        # --- /help ---
        if textl.startswith("/help"):
            cmds = [
                f"{TRIGGERS[0] if TRIGGERS else '/rust'} - fire ESP32 alarm; press button to boot PC + launch Rust",
                "/boot - wake PC only (WOL, no Rust, no alarm)",
                "/rustnoalarm - boot PC and launch Rust, skip alarm",
                "/screenshot - take and send screenshot of middle monitor",
                "/alarm - test ESP32 alarm (2s buzz)",
                "/stopalarm - stop ESP32 alarm immediately",
                f"/server - show current Rust server (now: {RUST_SERVER[0]})",
                "/setserver <ip> or <ip:port> - change Rust server IP",
                "/scheduleserverjoin MM/DD HH:MM - schedule PC boot + join at a time",
                "/cancelschedule [id] - list or cancel scheduled joins",
                f"/settimezone EST|CST|MST|PST - set schedule timezone (now: {next((k for k,v in TIMEZONE_MAP.items() if v == CURRENT_TZ[0]), CURRENT_TZ[0])})",
                "/help - show this help",
            ]
            await evt.reply("Commands:\n" + "\n".join(f"  {c}" for c in cmds))
            return

        # --- /server ---
        if textl.startswith("/server") and not textl.startswith("/setserver"):
            await evt.reply(f"Current Rust server: `{RUST_SERVER[0]}`")
            return

        # --- /setserver ---
        if textl.startswith("/setserver"):
            parts = text.split(None, 1)
            if len(parts) < 2 or not is_valid_server(parts[1]):
                await evt.reply("Usage: /setserver <ip> or /setserver <ip:port>")
                return
            new_ip = parts[1].strip()
            save_rust_server(new_ip)
            await evt.reply(f"Rust server set to `{new_ip}`")
            return

        # --- /screenshot ---
        if textl.startswith("/screenshot"):
            await evt.reply("Taking screenshot...")
            await take_and_send_screenshot(target)
            return

        # --- /alarm ---
        if textl.startswith("/alarm"):
            result = await trigger_esp32_start()
            await evt.reply(f"ESP32 Alarm: `{result}`")
            return

        # --- /stopalarm ---
        if textl.startswith("/stopalarm"):
            result = await trigger_esp32_stop()
            await evt.reply(f"ESP32 Alarm stopped: `{result}`")
            return

        # --- /boot ---
        if textl.startswith("/boot"):
            await evt.reply("Booting PC (no Rust, no alarm)...")
            if PC_MAC:
                send_magic_packet(PC_MAC, ip_address=BROADCAST_IP, port=9)
            if await wait_for_online(PC_IP_OR_HOST, PING_TIMEOUT):
                await evt.reply("PC booted and online.")
                if BOOT_CMD and SSH_HOST:
                    await run_ssh_cmd(BOOT_CMD)
            else:
                await evt.reply("PC did not come online in time.")
            return

        # --- /rustnoalarm ---
        if textl.startswith("/rustnoalarm"):
            await evt.reply(f"Starting Rust on {RUST_SERVER[0]} (no alarm)...")
            if PC_MAC:
                send_magic_packet(PC_MAC, ip_address=BROADCAST_IP, port=9)
            if not await wait_for_online(PC_IP_OR_HOST, PING_TIMEOUT):
                await evt.reply("PC did not come online in time.")
                return
            await evt.reply("PC booted. Launching Rust...")
            await asyncio.sleep(20)
            cmd = WIN_CMD + f' -Server "{RUST_SERVER[0]}"'
            code, out, err = await run_ssh_cmd(cmd)
            if code == 0:
                await evt.reply("Rust started! Taking screenshot in 20s...")
                await asyncio.sleep(20)
                await take_and_send_screenshot(target)
            elif code == 1:
                await evt.reply("Rust already running.")
            else:
                await evt.reply(f"SSH error: {out or err}")
            return

        # --- /settimezone ---
        if textl.startswith('/settimezone'):
            parts = text.split(None, 1)
            if len(parts) < 2:
                abbr = next((k for k, v in TIMEZONE_MAP.items() if v == CURRENT_TZ[0]), CURRENT_TZ[0])
                await evt.reply(f"Current timezone: {abbr}\nUsage: /settimezone EST|CST|MST|PST")
                return
            tz_input = parts[1].strip().upper()
            if tz_input not in TIMEZONE_MAP:
                await evt.reply(f"Unknown timezone '{tz_input}'. Options: EST, CST, MST, PST")
                return
            save_timezone(TIMEZONE_MAP[tz_input])
            await evt.reply(f"Timezone set to {tz_input} ({TIMEZONE_MAP[tz_input]})")
            return

        # --- /scheduleserverjoin ---
        if textl.startswith("/scheduleserverjoin"):
            parts = text.split(None, 3)
            if len(parts) < 3:
                await evt.reply("Usage: /scheduleserverjoin MM/DD HH:MM\nExample: /scheduleserverjoin 04/10 15:30")
                return
            try:
                target_dt = parse_schedule_dt(parts[1], parts[2])
            except ValueError as e:
                await evt.reply(f"Error: {e}")
                return
            if target_dt <= datetime.datetime.now(datetime.timezone.utc):
                await evt.reply("That time is already in the past.")
                return
            _job_id_counter[0] += 1
            job_id = _job_id_counter[0]
            server = RUST_SERVER[0]
            task = asyncio.create_task(run_scheduled_join(job_id, target_dt, server))
            _scheduled_jobs[job_id] = {'task': task, 'target': target_dt, 'server': server}
            time_str = target_dt.strftime('%Y-%m-%d %H:%M %Z')
            await evt.reply(f"Scheduled! Will join `{server}` at {time_str}\nJob ID: `{job_id}`")
            return

        # --- /cancelschedule ---
        if textl.startswith("/cancelschedule"):
            parts = text.split(None, 1)
            if len(parts) < 2:
                if not _scheduled_jobs:
                    await evt.reply("No scheduled joins.")
                else:
                    lines = [f"  #{jid}: {j['target'].strftime('%Y-%m-%d %H:%M %Z')} → `{j['server']}`"
                             for jid, j in sorted(_scheduled_jobs.items())]
                    await evt.reply("Scheduled joins:\n" + "\n".join(lines))
                return
            try:
                job_id = int(parts[1].strip())
            except ValueError:
                await evt.reply("Usage: /cancelschedule <id>")
                return
            job = _scheduled_jobs.pop(job_id, None)
            if job:
                job['task'].cancel()
                time_str = job['target'].strftime('%Y-%m-%d %H:%M %Z')
                await evt.reply(f"Cancelled job #{job_id} ({time_str} → `{job['server']}`).")
            else:
                await evt.reply(f"No job #{job_id} found.")
            return

        # --- /rust or trigger phrase - just fire the alarm ---
        if not match_text(textl):
            return
        esp32_result = await trigger_esp32_start()
        if 'error' in esp32_result.lower():
            await evt.reply(f"ESP32 unreachable: `{esp32_result}`")
        else:
            await evt.reply("Raid/wipe notification! Press the button to launch Rust.")

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
