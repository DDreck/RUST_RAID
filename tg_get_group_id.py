import os
from telethon import TelegramClient

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION = "rustrun"  # uses your saved login session

async def main(client: TelegramClient):
    print("Listing your recent chats...\n")
    async for d in client.iter_dialogs(limit=100):
        uname = getattr(d.entity, "username", None)
        print(f"{d.id:>16}  {d.name:<40}  @{uname}")

with TelegramClient(SESSION, API_ID, API_HASH) as client:
    client.loop.run_until_complete(main(client))
