import asyncio, os, socket, subprocess
from wakeonlan import send_magic_packet
import paramiko
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- load env file ---
ENV = {}
with open(os.path.expanduser("~/rustbot.env")) as f:
    for line in f:
        line=line.strip()
        if not line or line.startswith("#"): continue
        k,v = line.split("=",1)
        ENV[k]=v

BOT_TOKEN   = ENV["TELEGRAM_BOT_TOKEN"]
PC_MAC      = ENV["PC_MAC"]
BROADCAST_IP= ENV.get("BROADCAST_IP","255.255.255.255")
PC_IP       = ENV["PC_HOSTNAME_OR_IP"]
SSH_USER    = ENV["SSH_USERNAME"]
SSH_PASS    = ENV.get("SSH_PASSWORD","")
RUST_SERVER = ENV["RUST_SERVER"]
RUST_PASS   = ENV.get("RUST_PASSWORD","")
PING_TIMEOUT= int(ENV.get("PING_TIMEOUT_SECONDS","300"))

# Powershell launcher command
POWERSHELL_CMD = r'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Scripts\Start-Rust-Interactive.ps1"'

def host_is_up(host: str) -> bool:
    # Prefer ping; fall back to TCP/22 probe
    try:
        r = subprocess.run(["ping","-c","1","-W","1",host], stdout=subprocess.DEVNULL)
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
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if host_is_up(host):
            return True
        await asyncio.sleep(2)
    return False

def run_ssh_command(host, username, password, command):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if password:
        client.connect(hostname=host, username=username, password=password, timeout=20)
    else:
        client.connect(hostname=host, username=username, timeout=20)  # key auth
    try:
        stdin, stdout, stderr = client.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="ignore")
        err = stderr.read().decode(errors="ignore")
        return exit_status, out, err
    finally:
        client.close()

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = "online ✅" if host_is_up(PC_IP) else "offline ⛔"
    await update.message.reply_text(f"PC appears {status}.")

async def rust_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Waking/Checking PC…")
    # Send WoL anyway; harmless if already on
    try:
        send_magic_packet(PC_MAC, ip_address=BROADCAST_IP, port=9)
    except Exception as e:
        await update.message.reply_text(f"ℹ️ WoL send error (ignored if PC is already on): {e}")

    up = await wait_for_online(PC_IP, PING_TIMEOUT)
    if not up:
        await update.message.reply_text("❌ PC didn’t appear online in time (ping).")
        return

    # Give Windows services time to come up (sshd, Steam autologin, etc.)
    await asyncio.sleep(20)

    # Retry SSH a few times with backoff
    tries, last_err = 5, None
    for i in range(tries):
        try:
            code, out, err = run_ssh_command(PC_IP, SSH_USER, SSH_PASS, POWERSHELL_CMD)
            if code == 1:
                await update.message.reply_text("💡 Rust is already running on your PC — skipping launch.")
            elif code == 0:
                await update.message.reply_text("✅ Rust launch command succeeded.")
            else:
                clip = (out or err or "").strip()
                if len(clip) > 400: clip = clip[:400] + "…"
                await update.message.reply_text(f"⚠️ Script exited with code {code}.\n{clip}")
            return
        except Exception as e:
            last_err = e
            await asyncio.sleep(5 * (i + 1))  # 5s,10s,15s,20s,25s

    await update.message.reply_text(f"❌ SSH failed after retries: {last_err}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("pingpc", ping_command))
    app.add_handler(CommandHandler("rust", rust_command))
    app.run_polling()

if __name__ == "__main__":
    main()
