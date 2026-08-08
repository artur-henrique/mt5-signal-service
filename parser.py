"""
Parser das mensagens do canal do Telegram.

Duas categorias de mensagem, tratadas de formas diferentes:

1. Sinal estruturado de abertura (TRADE / BUY|SELL / Entry / SL / Tp)
   -> resolvido com regex, determinístico, sem custo, sem dependência externa.

2. Texto livre (fechar posição, mover para breakeven, etc.)
   -> não tem formato fixo, então cai para um fallback usando a API da
      Anthropic para classificar a intenção e extrair os ativos citados.

Cada sinal recebe um "id" próprio (ver storage.push_signal) que o EA grava
no comment da ordem no MT5 -- é o que permite, depois, um comando de texto
livre do tipo "close xauusd" ser vinculado à posição certa.
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

from anthropic import Anthropic

# Modelo usado só para classificar mensagens de texto livre (baixo custo/latência).
# Ajuste aqui se quiser usar outro modelo disponível na sua conta.
FALLBACK_MODEL = "claude-haiku-4-5-20251001"

_client: Optional[Anthropic] = None

KNOWN_SYMBOLS_PATH = Path(__file__).parent / "known_symbols.txt"
_known_symbols_regex: Optional[re.Pattern] = None


def _get_anthropic_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY não configurada -- necessária para o fallback do parser."
            )
        _client = Anthropic(api_key=api_key)
    return _client


def _get_known_symbols_regex() -> re.Pattern:
    """
    Carrega known_symbols.txt (uma vez, com cache) e monta um regex que
    acha qualquer um desses tickers em qualquer lugar do texto -- em vez
    de depender da posição das palavras, que varia entre mensagens do
    canal (ex: "SELL LIMIT GBPUSD" vs "(USDCHF) Sell Limit").
    """
    global _known_symbols_regex
    if _known_symbols_regex is not None:
        return _known_symbols_regex

    tickers = []
    if KNOWN_SYMBOLS_PATH.exists():
        for linha in KNOWN_SYMBOLS_PATH.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            tickers.append(linha.upper())

    if not tickers:
        # Lista vazia faria o regex casar com QUALQUER coisa (padrão vazio) --
        # em vez disso, usa um padrão que nunca casa, forçando cair no fallback.
        _known_symbols_regex = re.compile(r"(?!x)x")
        return _known_symbols_regex

    # Tickers mais longos primeiro, pra um ticker que "contém" outro
    # (não deveria acontecer com esses símbolos, mas por segurança) não
    # ser cortado pela metade.
    tickers.sort(key=len, reverse=True)
    padrao = r"\b(" + "|".join(re.escape(t) for t in tickers) + r")\b"
    _known_symbols_regex = re.compile(padrao, re.IGNORECASE)
    return _known_symbols_regex


def _buscar_ativo_conhecido(texto: str) -> Optional[str]:
    match = _get_known_symbols_regex().search(texto)
    return match.group(1).upper() if match else None


# --------------------------------------------------------------------------
# Categoria 1: sinal estruturado de abertura
# --------------------------------------------------------------------------

def try_parse_structured(texto: str) -> Optional[dict]:
    """
    Tenta reconhecer um sinal de abertura de trade. Extrai cada campo de
    forma independente da posição das palavras no texto (direção, ativo,
    entry/sl/tp, tipo de ordem podem vir em qualquer ordem/formato) --
    isso é importante porque o canal varia o formato entre mensagens
    (ex: "SELL LIMIT GBPUSD" vs "(USDCHF) Sell Limit").
    """
    ativo = _buscar_ativo_conhecido(texto)
    if not ativo:
        return None

    m_entry = re.search(r"Entry\s*-\s*([\d.]+)", texto, re.IGNORECASE)
    m_sl = re.search(r"\bSL\s*-\s*([\d.]+)", texto, re.IGNORECASE)
    m_tp = re.search(r"\bTp\s*-\s*([\d.]+)", texto, re.IGNORECASE)
    if not (m_entry and m_sl and m_tp):
        return None

    entry = float(m_entry.group(1))
    sl = float(m_sl.group(1))
    tp = float(m_tp.group(1))

    m_direcao = re.search(r"\b(BUY|SELL)\b", texto, re.IGNORECASE)
    direcao_inferida = False

    if m_direcao:
        direcao = m_direcao.group(1).upper()
    else:
        # Não veio BUY/SELL explícito -- tenta inferir pela posição
        # relativa de SL/TP em torno da entrada, que é matematicamente
        # inequívoca quando os valores fazem sentido: SL acima e TP
        # abaixo da entrada só pode ser venda, e vice-versa. Se não for
        # coerente (SL/TP do mesmo lado, ou iguais à entrada), não dá
        # pra inferir com segurança -- melhor ignorar do que arriscar.
        if sl > entry and tp < entry:
            direcao = "SELL"
            direcao_inferida = True
        elif sl < entry and tp > entry:
            direcao = "BUY"
            direcao_inferida = True
        else:
            return None

    tem_cmp = bool(re.search(r"\bCMP\b", texto, re.IGNORECASE))
    tem_market = bool(re.search(r"\bMARKET\b", texto, re.IGNORECASE))
    is_market = tem_cmp or tem_market
    # Nota: "STOP" (ordem stop pendente) é tratado como "limit" por enquanto --
    # o EA hoje só distingue mercado vs. limite.

    resultado = {
        "acao": "abrir",
        "ativo": ativo,
        "direcao": direcao,
        "tipo_ordem": "market" if is_market else "limit",
        "entry": entry,
        "sl": sl,
        "tp": tp,
    }
    if direcao_inferida:
        # Não vai pro EA como campo problemático (ele ignora chaves que
        # não conhece) -- é só pra o listener logar/alertar que a direção
        # foi deduzida, não veio explícita na mensagem.
        resultado["direcao_inferida"] = True

    return resultado


def parece_sinal_sem_ativo_reconhecido(texto: str) -> bool:
    """
    Detecta o caso "isso claramente parece um sinal de trade (tem Entry/SL/Tp),
    mas nenhum ativo conhecido foi encontrado" -- provavelmente um par novo
    que ainda não está no known_symbols.txt. Usado só para logar um aviso
    mais específico do que "mensagem ignorada", ajudando a perceber rápido
    quando precisa adicionar um ticker novo na lista.
    """
    tem_entry_sl_tp = (
        re.search(r"Entry\s*-\s*[\d.]+", texto, re.IGNORECASE)
        and re.search(r"\bSL\s*-\s*[\d.]+", texto, re.IGNORECASE)
        and re.search(r"\bTp\s*-\s*[\d.]+", texto, re.IGNORECASE)
    )
    return bool(tem_entry_sl_tp) and _buscar_ativo_conhecido(texto) is None


# --------------------------------------------------------------------------
# Categoria 2: texto livre -> fallback via IA
# --------------------------------------------------------------------------

_FALLBACK_SYSTEM_PROMPT = """\
Você interpreta mensagens curtas de um canal de sinais de forex/ouro e \
classifica a intenção do autor. Responda APENAS com um JSON válido, sem \
nenhum texto antes ou depois, no seguinte formato:

{
  "acao": "fechar" | "breakeven" | "cancelar_pendente" | "reativar_pendente" | "modificar" | "desconhecido",
  "escopo": "especifico" | "todas_pendentes" | "todas",
  "ativos": ["XAUUSD", "GBPCHF"],
  "novo_sl": 1.0850,
  "novo_tp": 1.0950
}

Regras:
- "acao": "fechar" quando o autor pede para fechar posição(ões) JÁ ABERTAS;
  "breakeven" quando pede para mover o stop para o ponto de entrada;
  "cancelar_pendente" quando pede para NÃO deixar uma ordem pendente executar
  ainda / segurar a entrada (ex: "don't set limits yet", "hold off", "cancel
  the pending"), avisando que um sinal mais preciso virá depois;
  "reativar_pendente" quando, depois de ter pedido pra segurar, o autor libera
  a mesma entrada original pra ser colocada de novo (ex: "you can set it now",
  "go ahead with the original entry", "set it now at the same levels");
  "modificar" quando pede para alterar o stop loss e/ou o take profit de uma
  operação para um valor numérico NOVO e explícito (ex: "change stop loss to
  1.0820", "move TP to 4200 on XAUUSD", "new sl/tp of EURUSD: sl 1.0800 tp
  1.0950"). Se a mensagem falar em mudar SL/TP mas NÃO trouxer nenhum valor
  numérico novo, classifique como "desconhecido" (não dá pra agir sem valor);
  "desconhecido" quando a mensagem não é um comando de gerenciamento claro.
- "escopo": "especifico" quando se refere a ativo(s) citado(s) nominalmente
  (inclusive quando citado apenas como "it"/"this" referindo-se ao sinal
  respondido, veja a regra de contexto abaixo);
  "todas_pendentes" quando pede para fechar/cancelar todas as ordens pendentes;
  "todas" quando pede para fechar tudo, sem especificar.
- "ativos": lista dos tickers mencionados (ex: XAUUSD, EURUSD, GBPCHF, USDMXN),
  mesmo que venham com "$" na frente (ex: "$GBPCHF" -> "GBPCHF"). Lista vazia
  se nenhum ativo for citado ou se o escopo for "todas"/"todas_pendentes".
- "novo_sl" e "novo_tp": presentes SOMENTE quando "acao" é "modificar" e a
  mensagem trouxer aquele valor numérico explicitamente. Omita (não inclua)
  o campo que não foi mencionado -- por exemplo, se só o SL foi citado, não
  inclua "novo_tp" no JSON. Nunca invente um valor.

Quando o contexto do sinal original (a mensagem que está sendo respondida)
for fornecido antes do texto, use-o para desambiguar -- por exemplo, se a
mensagem original era um sinal de XAUUSD e a resposta diz apenas "cancel it"
ou "change stop loss of it" sem citar o ativo, entenda que "ativos" é
["XAUUSD"] e "escopo" é "especifico".
"""


def parse_free_text_via_ia(texto: str, contexto_sinal_original: Optional[dict] = None) -> dict:
    """Fallback via IA para mensagens que não batem com o formato estruturado."""
    client = _get_anthropic_client()

    if contexto_sinal_original:
        mensagem_usuario = (
            "Contexto: esta mensagem é uma resposta (reply) a um sinal anterior:\n"
            f"{json.dumps(contexto_sinal_original, ensure_ascii=False)}\n\n"
            f"Mensagem a classificar: {texto}"
        )
    else:
        mensagem_usuario = texto

    response = client.messages.create(
        model=FALLBACK_MODEL,
        max_tokens=300,
        system=_FALLBACK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": mensagem_usuario}],
    )
    raw = "".join(block.text for block in response.content if block.type == "text").strip()
    # remove eventuais cercas de código, caso o modelo insista em incluir
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"acao": "desconhecido", "escopo": "especifico", "ativos": []}

    resultado = {
        "acao": parsed.get("acao", "desconhecido"),
        "escopo": parsed.get("escopo", "especifico"),
        "ativos": [a.upper().lstrip("$") for a in parsed.get("ativos", [])],
    }
    if "novo_sl" in parsed and parsed["novo_sl"] is not None:
        resultado["novo_sl"] = float(parsed["novo_sl"])
    if "novo_tp" in parsed and parsed["novo_tp"] is not None:
        resultado["novo_tp"] = float(parsed["novo_tp"])
    return resultado


# --------------------------------------------------------------------------
# Entrada principal
# --------------------------------------------------------------------------

def parse_message(texto: str, contexto_sinal_original: Optional[dict] = None) -> Optional[dict]:
    """
    Ponto de entrada único do parser.

    `contexto_sinal_original` é o sinal de abertura original (com id, ativo,
    direcao, entry, sl, tp) quando a mensagem atual é uma resposta (reply) a
    ele no Telegram -- usado tanto para desambiguar comandos de texto livre
    quanto para reconstruir a ordem em caso de "reativar_pendente".

    Retorna um dict pronto para virar um sinal na fila, ou None se a
    mensagem não corresponder a nada acionável (ex: só "manage your risk").
    """
    estruturado = try_parse_structured(texto)
    if estruturado:
        return estruturado

    interpretado = parse_free_text_via_ia(texto, contexto_sinal_original)

    if interpretado["acao"] == "desconhecido":
        return None

    if interpretado["acao"] == "cancelar_pendente":
        resultado = {
            "acao": "cancelar_pendente",
            "escopo": interpretado["escopo"],
            "ativos": interpretado["ativos"],
        }
        if contexto_sinal_original:
            resultado["sinal_id_referenciado"] = contexto_sinal_original.get("id")
            if not resultado["ativos"] and contexto_sinal_original.get("ativo"):
                resultado["ativos"] = [contexto_sinal_original["ativo"]]
        return resultado

    if interpretado["acao"] == "reativar_pendente":
        # Reaproveita entry/SL/TP do sinal original -- vira um novo sinal de
        # abertura "abrir" de verdade, reusando o mesmo caminho que o EA já
        # sabe processar (nao precisa de nenhuma acao nova do lado do EA).
        if not contexto_sinal_original or contexto_sinal_original.get("acao") != "abrir":
            # Sem contexto suficiente pra reconstruir a ordem -- nao da pra agir.
            return None
        return {
            "acao": "abrir",
            "ativo": contexto_sinal_original["ativo"],
            "direcao": contexto_sinal_original["direcao"],
            "tipo_ordem": contexto_sinal_original.get("tipo_ordem", "limit"),
            "entry": contexto_sinal_original["entry"],
            "sl": contexto_sinal_original["sl"],
            "tp": contexto_sinal_original["tp"],
        }

    if interpretado["acao"] == "modificar":
        novo_sl = interpretado.get("novo_sl")
        novo_tp = interpretado.get("novo_tp")
        if novo_sl is None and novo_tp is None:
            # Pediu pra mudar SL/TP mas nenhum valor novo veio junto --
            # nao da pra agir com seguranca, melhor ignorar do que adivinhar.
            return None

        resultado = {
            "acao": "modificar",
            "escopo": interpretado["escopo"],
            "ativos": interpretado["ativos"],
        }
        if novo_sl is not None:
            resultado["novo_sl"] = novo_sl
        if novo_tp is not None:
            resultado["novo_tp"] = novo_tp
        if contexto_sinal_original:
            resultado["sinal_id_referenciado"] = contexto_sinal_original.get("id")
            if not resultado["ativos"] and contexto_sinal_original.get("ativo"):
                resultado["ativos"] = [contexto_sinal_original["ativo"]]
        return resultado

    if interpretado["acao"] in ("fechar", "breakeven"):
        resultado = {
            "acao": interpretado["acao"],
            "escopo": interpretado["escopo"],
            "ativos": interpretado["ativos"],
        }
        if contexto_sinal_original:
            # Referência precisa: a mensagem é resposta a um sinal específico,
            # então a ação deve valer só pra ELE -- não pra qualquer posição
            # do mesmo ativo (que poderia haver mais de uma).
            resultado["sinal_id_referenciado"] = contexto_sinal_original.get("id")
            if not resultado["ativos"] and contexto_sinal_original.get("ativo"):
                resultado["ativos"] = [contexto_sinal_original["ativo"]]
        return resultado

    return interpretado
