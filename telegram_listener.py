"""
Escuta o canal do Telegram em tempo real e, para cada mensagem nova,
manda pro parser e -- se resultar em algo acionável -- publica na fila
de sinais (storage.push_signal).

Na primeira execução, o Telethon vai pedir pra você fazer login (número
de telefone + código recebido no Telegram) -- isso cria um arquivo de
sessão local (telegram_session.session) pra não pedir de novo nas próximas
vezes.
"""

import logging
import os

from telethon import TelegramClient, events

import storage
import notifier
from parser import parse_message, parece_sinal_sem_ativo_reconhecido

logger = logging.getLogger("telegram_listener")

SESSION_NAME = "telegram_session"


def build_client() -> TelegramClient:
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    return TelegramClient(SESSION_NAME, api_id, api_hash)


def _parse_channel_setting(raw: str):
    """
    TELEGRAM_CHANNEL pode ser um @username (string) ou um ID numérico
    (string só com dígitos, opcionalmente com "-"). Convertemos pra int
    quando for numérico, porque é assim que o Telethon espera IDs de chat.
    """
    raw = raw.strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw


async def start_listener() -> None:
    channel_setting = _parse_channel_setting(os.environ["TELEGRAM_CHANNEL"])
    client = build_client()
    await client.start()
    notifier.registrar_client(client)

    # Aquece o cache de entidades da sessão -- sem isso, o Telethon pode
    # falhar com "Cannot find any entity corresponding to X" ao tentar
    # resolver um ID numérico que a sessão ainda não tinha "visto".
    await client.get_dialogs()

    try:
        entity = await client.get_entity(channel_setting)
    except ValueError as exc:
        raise RuntimeError(
            f"Não consegui encontrar o canal '{channel_setting}' nesta conta do "
            "Telegram. Confirme se TELEGRAM_CHANNEL está com o ID/username correto "
            "(rode list_channels.py para conferir) e se esta conta realmente "
            "participa desse canal."
        ) from exc

    logger.info("Canal resolvido: %s (id=%s)", getattr(entity, "title", entity), entity.id)

    @client.on(events.NewMessage(chats=entity))
    async def handler(event):
        texto = event.raw_text or ""
        logger.info("Mensagem recebida: %s", texto[:120].replace("\n", " "))

        contexto_sinal_original = None
        reply_to_id = event.reply_to_msg_id  # None se a mensagem não for uma resposta
        if reply_to_id:
            contexto_sinal_original = storage.get_signal_by_telegram_msg_id(reply_to_id)
            if contexto_sinal_original:
                logger.info(
                    "Mensagem é resposta ao sinal #%s (%s)",
                    contexto_sinal_original.get("id"),
                    contexto_sinal_original.get("ativo"),
                )
            else:
                logger.info("Mensagem é resposta, mas não encontrei sinal original vinculado.")

        try:
            sinal = parse_message(texto, contexto_sinal_original=contexto_sinal_original)
        except Exception:
            logger.exception("Falha ao interpretar mensagem, ignorando.")
            await notifier.enviar_alerta(
                f"Falha ao interpretar uma mensagem do canal (veja o log do servidor pro detalhe).\n"
                f"Mensagem: {texto[:200]}"
            )
            return

        if sinal is None:
            if parece_sinal_sem_ativo_reconhecido(texto):
                logger.warning(
                    "Mensagem parece um sinal de trade (tem Entry/SL/Tp), mas o ativo NÃO foi "
                    "reconhecido -- pode ser um par novo que falta em known_symbols.txt. "
                    "Mensagem: %s",
                    texto[:200].replace("\n", " "),
                )
                await notifier.enviar_alerta(
                    "Sinal recebido com um ativo que eu não reconheço (pode ser um par novo).\n"
                    "Adicione o ticker em known_symbols.txt e reinicie o serviço.\n\n"
                    f"Mensagem: {texto[:300]}"
                )
            else:
                logger.info("Mensagem sem ação acionável, ignorada.")
            return

        # Guarda o ID da mensagem do Telegram só em sinais de abertura --
        # é isso que permite uma resposta futura a ESTA mensagem ser
        # vinculada de volta a este sinal específico.
        telegram_msg_id = event.id if sinal.get("acao") == "abrir" else None

        signal_id = storage.push_signal(sinal, telegram_msg_id=telegram_msg_id)
        logger.info("Sinal #%s publicado: %s", signal_id, sinal)

    logger.info("Listener do Telegram conectado e escutando o canal.")
    await client.run_until_disconnected()
