"""
Servidor HTTP que os EAs consultam via WebRequest().

Endpoints:
  GET  /api/signals/poll   -- long polling: EA manda seu último id conhecido
                               (since_id) + token + conta, e a requisição
                               fica "pendurada" até aparecer sinal novo ou
                               estourar o timeout.
  POST /api/log            -- EA reporta eventos (ordem aberta/fechada,
                               heartbeat) para você monitorar remotamente.

  Endpoints administrativos (exigem ADMIN_TOKEN no header X-Admin-Token):
  POST   /admin/clients        -- cria um cliente novo (token + conta MT5)
  GET    /admin/clients        -- lista clientes
  POST   /admin/clients/{token}/revoke  -- revoga o acesso de um cliente
  GET    /admin/logs           -- consulta os logs reportados pelos EAs
"""

import asyncio
import os
import time

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

import storage

app = FastAPI(title="Signal Copier Service")

# Long polling: tempo máximo (segundos) que a requisição fica aberta
# esperando por um sinal novo antes de responder vazia.
POLL_WAIT_SECONDS = 4.0
POLL_CHECK_INTERVAL = 0.5


@app.on_event("startup")
def _startup():
    storage.init_db()


def _check_admin(x_admin_token: str = Header(default="")) -> None:
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or x_admin_token != expected:
        raise HTTPException(status_code=401, detail="Token de administração inválido.")


# --------------------------------------------------------------------------
# Endpoint consumido pelo EA: long polling de sinais
# --------------------------------------------------------------------------

@app.get("/api/signals/poll")
async def poll_signals(
    token: str = Query(...),
    account: str = Query(...),
    since_id: int = Query(0),
):
    if not storage.validate_client(token, account):
        raise HTTPException(status_code=401, detail="Token/conta inválidos ou acesso revogado.")

    deadline = time.time() + POLL_WAIT_SECONDS
    while True:
        sinais = storage.get_signals_since(since_id)
        if sinais:
            return {"sinais": sinais}
        if time.time() >= deadline:
            return {"sinais": []}
        await asyncio.sleep(POLL_CHECK_INTERVAL)


# --------------------------------------------------------------------------
# Endpoint consumido pelo EA: log remoto
# --------------------------------------------------------------------------

class LogEvent(BaseModel):
    token: str
    account: str
    event_type: str  # ex: "sinal_recebido", "ordem_aberta", "ordem_fechada", "heartbeat", "erro"
    payload: dict = {}


@app.post("/api/log")
async def post_log(evento: LogEvent):
    if not storage.validate_client(evento.token, evento.account):
        raise HTTPException(status_code=401, detail="Token/conta inválidos ou acesso revogado.")
    storage.push_log(evento.token, evento.account, evento.event_type, evento.payload)
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Administração
# --------------------------------------------------------------------------

class NewClient(BaseModel):
    account_id: str
    nome: str = ""


@app.post("/admin/clients")
async def create_client(body: NewClient, x_admin_token: str = Header(default="")):
    _check_admin(x_admin_token)
    token = storage.create_client(body.account_id, body.nome)
    return {"token": token, "account_id": body.account_id, "nome": body.nome}


@app.get("/admin/clients")
async def list_clients(x_admin_token: str = Header(default="")):
    _check_admin(x_admin_token)
    rows = storage.list_clients()
    return [dict(r) for r in rows]


@app.post("/admin/clients/{token}/revoke")
async def revoke_client(token: str, x_admin_token: str = Header(default="")):
    _check_admin(x_admin_token)
    ok = storage.set_client_active(token, active=False)
    if not ok:
        raise HTTPException(status_code=404, detail="Cliente não encontrado.")
    return {"status": "revogado"}


@app.post("/admin/clients/{token}/activate")
async def activate_client(token: str, x_admin_token: str = Header(default="")):
    _check_admin(x_admin_token)
    ok = storage.set_client_active(token, active=True)
    if not ok:
        raise HTTPException(status_code=404, detail="Cliente não encontrado.")
    return {"status": "ativado"}


@app.get("/admin/logs")
async def get_logs(x_admin_token: str = Header(default=""), token: str = Query(default="")):
    _check_admin(x_admin_token)
    rows = storage.get_logs(token or None)
    return [dict(r) for r in rows]
