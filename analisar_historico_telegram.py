"""
Reconstrói o Histórico de Sinais a partir do Telegram
========================================================
Como o registro em CSV só começou a existir agora, este script recupera
os sinais que JÁ FORAM ENVIADOS ao canal nos últimos dias, lendo o
histórico exportado do Telegram, e simula o resultado real de cada um
com dados históricos da BingX. O resultado é gravado no MESMO CSV que o
bot.py usa daqui pra frente — então depois disso, é tudo uma coisa só.

COMO EXPORTAR O HISTÓRICO DO TELEGRAM:
    1. Abra o Telegram Desktop (não funciona pelo celular/web)
    2. Entre no canal/grupo onde o robô posta os sinais
    3. Clique nos 3 pontinhos (⋮) > "Export chat history"
    4. Em "Format", marque "Machine-readable JSON"
    5. Desmarque fotos/vídeos (não precisa), exporte só o período desejado
    6. Isso gera uma pasta com um arquivo "result.json" dentro

COMO RODAR:
    python analisar_historico_telegram.py caminho/para/result.json
"""

import sys
import json
import re
import time
import logging
from datetime import datetime, timezone

import pandas as pd
import ccxt

import registro
from simulacao import simular_evolucao

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("historico_telegram")

exchange = ccxt.bingx({"enableRateLimit": True})
TIMEFRAME = "15m"

PADRAO_SINAL = re.compile(
    r"SINAL DE (COMPRA|VENDA).*?"
    r"Ativo:\s*([A-Z0-9]+/[A-Z0-9]+).*?"
    r"Pre[çc]o de Entrada:\s*([\d.]+).*?"
    r"Alvo 1:\s*([\d.]+).*?"
    r"Alvo 2:\s*([\d.]+).*?"
    r"Stop Loss:\s*([\d.]+)",
    re.DOTALL,
)


def extrair_texto_plano(campo_text) -> str:
    """O export do Telegram pode trazer 'text' como string simples ou como
    lista de pedaços (str ou dict com formatação). Junta tudo em texto puro."""
    if isinstance(campo_text, str):
        return campo_text
    if isinstance(campo_text, list):
        partes = []
        for item in campo_text:
            if isinstance(item, str):
                partes.append(item)
            elif isinstance(item, dict) and "text" in item:
                partes.append(item["text"])
        return "".join(partes)
    return ""


def parsear_export(caminho_json: str) -> list[dict]:
    with open(caminho_json, "r", encoding="utf-8") as f:
        dados = json.load(f)

    mensagens = dados.get("messages", [])
    sinais = []

    for msg in mensagens:
        if msg.get("type") != "message":
            continue

        texto = extrair_texto_plano(msg.get("text", ""))
        if "SINAL DE" not in texto:
            continue

        match = PADRAO_SINAL.search(texto)
        if not match:
            log.warning(f"Mensagem parecia um sinal mas não bateu com o padrão esperado (msg id {msg.get('id')}).")
            continue

        direcao, moeda, entrada, alvo1, alvo2, stop = match.groups()

        data_unix = msg.get("date_unixtime")
        if data_unix:
            dt_sinal = datetime.fromtimestamp(int(data_unix), tz=timezone.utc)
        else:
            dt_sinal = pd.to_datetime(msg["date"], utc=True)

        sinais.append({
            "moeda": moeda, "direcao": direcao,
            "entrada": float(entrada), "alvo1": float(alvo1),
            "alvo2": float(alvo2), "stop": float(stop),
            "datetime_sinal": dt_sinal,
        })

    log.info(f"{len(sinais)} sinal(is) encontrados no histórico exportado.")
    return sinais


def buscar_velas_apos(moeda: str, dt_sinal: pd.Timestamp) -> pd.DataFrame:
    desde_ms = int(dt_sinal.timestamp() * 1000)
    todas = []
    while True:
        candles = exchange.fetch_ohlcv(moeda, timeframe=TIMEFRAME, since=desde_ms, limit=1000)
        if not candles:
            break
        todas.extend(candles)
        if len(candles) < 1000:
            break
        desde_ms = candles[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)

    if not todas:
        return pd.DataFrame(columns=["datetime", "high", "low"])

    df = pd.DataFrame(todas, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df[df["datetime"] > dt_sinal].drop_duplicates(subset="datetime").sort_values("datetime")
    return df[["datetime", "high", "low"]]


def main():
    if len(sys.argv) < 2:
        print("Uso: python analisar_historico_telegram.py caminho/para/result.json")
        sys.exit(1)

    sinais = parsear_export(sys.argv[1])
    if not sinais:
        print("Nenhum sinal encontrado no arquivo exportado. Confira se o formato bate com o esperado.")
        return

    registro.garantir_csv()
    existentes = registro.carregar_sinais()

    for sinal in sinais:
        # evita duplicar caso você rode este script mais de uma vez
        ja_existe = not existentes.empty and (
            (existentes["moeda"] == sinal["moeda"]) &
            (existentes["direcao"] == sinal["direcao"]) &
            (existentes["datetime_sinal"] == sinal["datetime_sinal"])
        ).any()
        if ja_existe:
            continue

        log.info(f"Processando {sinal['moeda']} {sinal['direcao']} de {sinal['datetime_sinal']}...")
        velas = buscar_velas_apos(sinal["moeda"], sinal["datetime_sinal"])

        atr_aproximado = abs(sinal["entrada"] - sinal["stop"]) / 1.5  # reverte o ATR a partir do stop

        sinal_id = registro.registrar_sinal(
            moeda=sinal["moeda"], direcao=sinal["direcao"], entrada=sinal["entrada"],
            stop=sinal["stop"], alvo1=sinal["alvo1"], alvo2=sinal["alvo2"],
            atr=atr_aproximado, motivo="Recuperado do histórico do Telegram",
            datetime_sinal=sinal["datetime_sinal"],
        )

        if velas.empty:
            log.warning(f"[{sinal['moeda']}] Sem velas históricas suficientes, deixando como ABERTO.")
            continue

        resultado = simular_evolucao(
            direcao=sinal["direcao"], entrada=sinal["entrada"], stop_inicial=sinal["stop"],
            alvo1=sinal["alvo1"], alvo2=sinal["alvo2"], velas_futuras=velas,
        )

        if resultado["status"] != "ABERTO":
            df = registro.carregar_sinais()
            linha = df["id"] == sinal_id
            df.loc[linha, "status"] = resultado["status"]
            df.loc[linha, "resultado_r"] = resultado["resultado_r"]
            df.loc[linha, "datetime_saida"] = resultado["datetime_saida"]
            df.loc[linha, "preco_saida"] = resultado["preco_saida"]
            registro.salvar_sinais(df)
            log.info(f"[{sinal['moeda']}] Resultado: {resultado['status']} ({resultado['resultado_r']:.2f}R)")
        else:
            log.info(f"[{sinal['moeda']}] Ainda em aberto (não bateu stop nem alvo até agora).")

    print(f"\nConcluído. Resultados gravados em {registro.CAMINHO_CSV}.")
    print("Rode 'python gerar_relatorio.py' para ver o dashboard visual.")


if __name__ == "__main__":
    main()
