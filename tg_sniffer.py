import os
from telethon import TelegramClient, events
API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
client = TelegramClient("rustrun", API_ID, API_HASH)

@client.on(events.NewMessage)
async def sniff(e):
    print(f"chat_id={e.chat_id} sender={e.sender_id} text={e.raw_text!r}", flush=True)

client.start()           # will prompt phone/code if not logged in
client.run_until_disconnected()

