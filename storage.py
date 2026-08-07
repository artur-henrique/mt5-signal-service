"""
Camada de persistência do serviço.

Usa SQLite (um único arquivo local, service.db) para guardar:
  - clients: os tokens de acesso vinculados a cada conta MT5
  - signals: a fila de sinais gerados a partir do Telegram
  - logs: eventos que os EAs reportam de volta (ordem aberta, fechada, heartbeat)

SQLite é suficiente para o volume desse serviço (dezenas/centenas de clientes
fazendo polling a cada poucos segundos) e evita depender de um banco externo
enquanto o projeto está em fase de teste.
"""

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "service.db"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                token       TEXT PRIMARY KEY,
                account_id  TEXT NOT NULL,
                nome        TEXT,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                payload          TEXT NOT NULL,
                telegram_msg_id  INTEGER,
                created_at       REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                token       TEXT NOT NULL,
                account_id  TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                payload     TEXT,
                created_at  REAL NOT NULL
            )
            """
        )
        # Migracao leve: se o banco ja existia de antes (sem a coluna nova),
        # adiciona ela agora. Ignora o erro se a coluna ja existir.
        try:
            conn.execute("ALTER TABLE signals ADD COLUMN telegram_msg_id INTEGER")
        except sqlite3.OperationalError:
            pass
        conn.commit()


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------

def create_client(account_id: str, nome: str = "") -> str:
    """Cria um novo cliente e retorna o token gerado."""
    token = uuid.uuid4().hex
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO clients (token, account_id, nome, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (token, str(account_id), nome, time.time()),
        )
        conn.commit()
    return token


def set_client_active(token: str, active: bool) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE clients SET active = ? WHERE token = ?", (1 if active else 0, token)
        )
        conn.commit()
        return cur.rowcount > 0


def get_client(token: str) -> Optional[sqlite3.Row]:
    with _lock, _connect() as conn:
        return conn.execute("SELECT * FROM clients WHERE token = ?", (token,)).fetchone()


def list_clients() -> list[sqlite3.Row]:
    with _lock, _connect() as conn:
        return conn.execute("SELECT * FROM clients ORDER BY created_at DESC").fetchall()


def validate_client(token: str, account_id: str) -> bool:
    """Confirma que o token existe, está ativo e bate com a conta MT5 informada."""
    row = get_client(token)
    if row is None:
        return False
    if not row["active"]:
        return False
    if str(row["account_id"]) != str(account_id):
        return False
    return True


# --------------------------------------------------------------------------
# Signals (fila que o Telegram listener alimenta e o /poll consome)
# --------------------------------------------------------------------------

def push_signal(payload: dict, telegram_msg_id: Optional[int] = None) -> int:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO signals (payload, telegram_msg_id, created_at) VALUES (?, ?, ?)",
            (json.dumps(payload, ensure_ascii=False), telegram_msg_id, time.time()),
        )
        conn.commit()
        return cur.lastrowid


def clear_signals() -> int:
    """
    Esvazia a fila de sinais (apaga todas as linhas), mas -- de propósito --
    NÃO reinicia o contador de id. Isso é importante: assim, os próximos
    sinais publicados continuam recebendo ids maiores que qualquer id antigo,
    então um EA que já tenha um "último id" salvo continua funcionando
    normalmente (não vê nada de novo até um sinal genuinamente novo chegar),
    e uma conta nova (começando do zero) também não encontra nada pra
    reprocessar, porque a fila está vazia. Retorna quantas linhas foram apagadas.
    """
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM signals")
        conn.commit()
        return cur.rowcount


def get_signal_by_telegram_msg_id(telegram_msg_id: int) -> Optional[dict]:
    """
    Busca o sinal de ABERTURA original que foi gerado a partir de uma
    mensagem específica do Telegram -- usado quando o usuário responde
    (reply) aquela mensagem no canal, para reaproveitar entry/SL/TP.
    """
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT id, payload FROM signals WHERE telegram_msg_id = ? ORDER BY id DESC LIMIT 1",
            (telegram_msg_id,),
        ).fetchone()
    if row is None:
        return None
    item = json.loads(row["payload"])
    item["id"] = row["id"]
    return item


def get_signals_since(since_id: int, limit: int = 20) -> list[dict]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, payload, created_at FROM signals WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit),
        ).fetchall()
    result = []
    for r in rows:
        item = json.loads(r["payload"])
        item["id"] = r["id"]
        result.append(item)
    return result


def get_latest_signal_id() -> int:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT MAX(id) AS max_id FROM signals").fetchone()
        return row["max_id"] or 0


# --------------------------------------------------------------------------
# Logs (eventos reportados pelos EAs)
# --------------------------------------------------------------------------

def push_log(token: str, account_id: str, event_type: str, payload: Optional[dict] = None) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO logs (token, account_id, event_type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (token, str(account_id), event_type, json.dumps(payload or {}, ensure_ascii=False), time.time()),
        )
        conn.commit()


def get_logs(token: Optional[str] = None, limit: int = 200) -> list[sqlite3.Row]:
    with _lock, _connect() as conn:
        if token:
            return conn.execute(
                "SELECT * FROM logs WHERE token = ? ORDER BY id DESC LIMIT ?", (token, limit)
            ).fetchall()
        return conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
