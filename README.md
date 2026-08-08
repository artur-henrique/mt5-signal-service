# Signal Copier — Servidor Python (Mac)

Serviço que escuta um canal do Telegram, interpreta os sinais (abertura,
fechamento, breakeven) e disponibiliza uma fila de sinais que os EAs no
computador slave consultam via long polling (HTTP).

Esta parte roda no **Mac**. O EA (MQL5) que vai rodar no computador slave
é o próximo passo, ainda não incluído aqui.

## O que este serviço faz

- Escuta o canal do Telegram em tempo real (Telethon)
- Interpreta sinais estruturados via regex (abertura de trade)
- Interpreta comandos em texto livre via IA (fechar, breakeven) como fallback
- Expõe um endpoint de long polling (`/api/signals/poll`) pros EAs consultarem
- Recebe logs remotos dos EAs (`/api/log`) pra você monitorar execução
- Tem endpoints administrativos pra criar/revogar acesso de clientes

## 1. Pré-requisitos

- Python 3.11 ou superior instalado no Mac (`python3 --version` pra checar)
- Uma conta no Telegram (a mesma que você usa normalmente)
- Uma API key da Anthropic (para o fallback de texto livre) — crie em
  https://console.anthropic.com se ainda não tiver

## 2. Obter as credenciais do Telegram

1. Acesse https://my.telegram.org e faça login com seu número
2. Vá em **API development tools**
3. Crie um app (qualquer nome/descrição serve) e anote o **api_id** e o
   **api_hash** gerados — vai precisar deles no passo 4

## 3. Instalar o projeto

Abra o Terminal no Mac, entre na pasta do projeto e crie um ambiente virtual:

```bash
cd caminho/para/mt5-signal-service

# cria um ambiente virtual isolado (recomendado, evita conflito com outros projetos)
python3 -m venv venv
source venv/bin/activate

# instala as dependências
pip install -r requirements.txt
```

Você vai saber que o ambiente virtual está ativo porque o terminal passa a
mostrar `(venv)` no início da linha. Sempre que for rodar o projeto de novo
em uma nova sessão do terminal, repita o `source venv/bin/activate` primeiro.

## 4. Configurar as variáveis de ambiente

Copie o arquivo de exemplo e preencha com seus dados:

```bash
cp .env.example .env
```

Abra o `.env` num editor de texto e preencha:

- `TELEGRAM_API_ID` e `TELEGRAM_API_HASH` — do passo 2
- `TELEGRAM_CHANNEL` — o @usuario do canal (ex: `@meucanaldeforex`) ou o ID
  numérico do canal, se ele não tiver @usuario público
- `ANTHROPIC_API_KEY` — sua API key da Anthropic
- `ADMIN_TOKEN` — invente uma string longa e aleatória (ex: gere uma com
  `python3 -c "import secrets; print(secrets.token_hex(24))"`) — isso protege
  os endpoints administrativos

## 5. Rodar o serviço

Com o ambiente virtual ativo (`(venv)` aparecendo no terminal):

```bash
python main.py
```

**Na primeira execução**, o Telethon vai pedir, direto no terminal:
1. Seu número de telefone (com código do país, ex: `+5511999999999`)
2. Um código que vai chegar no seu Telegram (app ou SMS)
3. Se sua conta tiver verificação em duas etapas, a senha dela também

Isso cria um arquivo `telegram_session.session` na pasta do projeto — nas
próximas vezes que rodar `python main.py`, não vai pedir login de novo
(a sessão fica salva localmente).

Se tudo estiver certo, você vai ver algo como:

```
INFO ... Listener do Telegram conectado e escutando o canal @seucanal
INFO ... Uvicorn running on http://0.0.0.0:8000
```

O serviço fica rodando escutando o canal e servindo a API ao mesmo tempo.
Pra parar, `Ctrl+C` no terminal.

## 6. Testar se está funcionando

Com o serviço rodando, abra **outro** terminal (deixe o primeiro rodando) e:

**Criar um cliente de teste** (simulando uma conta MT5 que vai receber sinais):

```bash
cd caminho/para/mt5-signal-service
source venv/bin/activate
python manage_clients.py criar --conta 8823456 --nome "Minha conta de teste"
```

Isso imprime um `token` — guarde ele, é o que o EA vai usar depois.

**Listar clientes:**

```bash
python manage_clients.py listar
```

**Simular o polling que o EA vai fazer** (troque `SEU_TOKEN` pelo token gerado):

```bash
curl "http://localhost:8000/api/signals/poll?token=SEU_TOKEN&account=8823456&since_id=0"
```

Isso deve responder `{"sinais":[]}` (fila vazia, se você ainda não mandou
nenhuma mensagem no canal).

**Testar de ponta a ponta:** mande uma mensagem no canal do Telegram que o
serviço está escutando, no formato de sinal que você mostrou antes. Você
deve ver no terminal onde o `python main.py` está rodando um log tipo
`Sinal #1 publicado: {...}`. Rodando o `curl` de novo (com `since_id=0`),
o sinal deve aparecer na resposta.

## 7. Alertas no Telegram (opcional, mas recomendado)

O servidor pode te avisar no Telegram quando algo dá errado — ativo não
reconhecido, ordem que falhou, ou a trava de segurança de lote grande
disparando — sem precisar checar o log da EC2 ou entrar no MT5.

**Como configurar:**

1. Defina um destino no `.env`:
   ```
   TELEGRAM_ALERT_CHAT=me
   ```
   `me` manda pra "Mensagens Salvas" da sua própria conta do Telegram —
   é a opção mais simples pra começar. Se preferir um canal/grupo
   separado só pra alertas, crie um, e use o `list_channels.py` (mesmo
   comando que usamos pra achar o canal de sinais) pra pegar o ID dele.

2. Reinicie o serviço. Não precisa de nenhuma credencial nova — os
   alertas usam a mesma sessão do Telegram que o listener já usa.

**O que gera alerta:**
- Uma mensagem do canal "parece" um sinal de trade (tem Entry/SL/Tp) mas
  o ativo não está no `known_symbols.txt`
- O EA reporta que uma ordem falhou, que um sinal foi ignorado por regra
  de risco (lote fora do range da conta), ou que a trava de segurança de
  lote grande (`InpMaxSinaisPorLote`) disparou

Eventos de rotina (ordem aberta com sucesso, heartbeat, etc.) não geram
alerta, pra não virar ruído — só os que realmente pedem sua atenção.

## 8. Próximos passos

- Revogar acesso de um cliente: `python manage_clients.py revogar --token TOKEN`
- Ver os logs que os EAs reportam:
  `curl -H "X-Admin-Token: SEU_ADMIN_TOKEN" http://localhost:8000/admin/logs`

## Estrutura dos arquivos

```
mt5-signal-service/
├── main.py               # ponto de entrada: roda listener + servidor juntos
├── server.py              # API HTTP (FastAPI) consultada pelos EAs
├── telegram_listener.py   # escuta o canal do Telegram (Telethon)
├── parser.py               # interpreta as mensagens (regex + fallback IA)
├── notifier.py              # envia alertas de erro pro Telegram
├── storage.py              # persistência em SQLite (clientes, sinais, logs)
├── manage_clients.py        # CLI para criar/revogar acesso de clientes
├── list_channels.py          # lista chats/canais do Telegram e seus IDs
├── known_symbols.txt          # lista de ativos que o parser reconhece
├── requirements.txt
├── .env.example
├── .gitignore
├── signal-copier.service      # unit file do systemd (produção)
├── mql5/                      # EA (MQL5) que roda no MT5
└── service.db                 # criado automaticamente na primeira execução
```
