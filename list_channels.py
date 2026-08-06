"""
Lista todos os chats, grupos e canais da sua conta do Telegram, com o ID
de cada um -- útil para descobrir o ID exato do canal de sinais quando o
@username não funciona diretamente no TELEGRAM_CHANNEL do .env.

Usa as mesmas credenciais (TELEGRAM_API_ID / TELEGRAM_API_HASH) e a mesma
sessão (telegram_session.session) já usadas pelo telegram_listener.py --
então se você já rodou o main.py e fez login uma vez, este script não vai
pedir login de novo.

Uso:
    python list_channels.py
"""

import asyncio

from dotenv import load_dotenv
from telethon.tl.types import Channel, Chat, User

from telegram_listener import build_client

load_dotenv()


async def main():
    client = build_client()
    await client.start()

    print(f"{'TIPO':<10} {'ID':<16} {'USERNAME':<25} NOME")
    print("-" * 80)

    async for dialog in client.iter_dialogs():
        entity = dialog.entity

        if isinstance(entity, Channel):
            tipo = "canal" if entity.broadcast else "supergrupo"
        elif isinstance(entity, Chat):
            tipo = "grupo"
        elif isinstance(entity, User):
            tipo = "usuario"
        else:
            tipo = "?"

        username = f"@{entity.username}" if getattr(entity, "username", None) else "-"
        print(f"{tipo:<10} {entity.id!s:<16} {username:<25} {dialog.name}")

    print("\nDica: copie o ID (número) do canal certo e coloque em TELEGRAM_CHANNEL")
    print("no .env. Não precisa de sinal de menos nem prefixo -- use o número puro,")
    print("por exemplo: TELEGRAM_CHANNEL=1234567890")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
