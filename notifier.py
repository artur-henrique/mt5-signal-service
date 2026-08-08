"""
Envia alertas para um chat/canal do Telegram DIFERENTE do canal de sinais,
para você saber que algo deu errado sem precisar checar os logs do
servidor ou entrar no MT5 diretamente.

Reaproveita a MESMA sessão do Telegram que o telegram_listener já usa --
não precisa de bot nem de credencial nova, só configurar
TELEGRAM_ALERT_CHAT no .env com o destino (ver .env.example).

Se TELEGRAM_ALERT_CHAT não estiver configurado, os alertas são
silenciosamente ignorados (não quebra nada, só não avisa).
"""

import logging
import os
from typing import Optional

logger = logging.getLogger("notifier")

# Setado pelo telegram_listener.py depois que o client conecta (client.start()).
_client = None


def registrar_client(client) -> None:
    """Chamado pelo telegram_listener após conectar, para o notifier poder
    reaproveitar a mesma sessão/conexão na hora de mandar um alerta."""
    global _client
    _client = client


async def enviar_alerta(mensagem: str) -> None:
    chat = os.environ.get("TELEGRAM_ALERT_CHAT", "").strip()
    if not chat or _client is None:
        return

    destino = int(chat) if chat.lstrip("-").isdigit() else chat

    try:
        await _client.send_message(destino, f"⚠️ Signal Copier\n\n{mensagem}")
    except Exception:
        logger.exception("Falha ao enviar alerta para o Telegram (não é crítico -- só o aviso que falhou).")
