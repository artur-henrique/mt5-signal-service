"""
Ponto de entrada do serviço.

Roda, no mesmo processo e loop de eventos asyncio:
  1) o listener do Telegram (telegram_listener.start_listener)
  2) o servidor HTTP (server.app, via uvicorn) que os EAs consultam

Uso:
    python main.py
"""

import asyncio
import logging
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

import storage
from telegram_listener import start_listener
from server import app


async def run_server():
    host = os.environ.get("SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("SERVER_PORT", "8000"))
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    storage.init_db()
    await asyncio.gather(
        run_server(),
        start_listener(),
    )


if __name__ == "__main__":
    asyncio.run(main())
