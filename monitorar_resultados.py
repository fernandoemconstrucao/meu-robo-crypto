"""
Monitor de Resultados
=======================
Roda periodicamente (pode ser um cron job separado no Railway, ou você
mesmo roda manualmente quando quiser atualizar) e verifica todos os
sinais com status ABERTO no CSV de registro, buscando as velas mais
recentes da BingX para ver se bateram Stop, Alvo 1 (breakeven) ou Alvo 2.

COMO RODAR:
    python monitorar_resultados.py

Sugestão: configure isso como um segundo serviço no Railway (ou um
"Cron Job" separado) rodando a cada 15-30 minutos, apontando para o
MESMO volume/arquivo CSV que o bot.py usa (variável REGISTRO_CSV_PATH).
"""

import time
import logging

import pandas as pd
import ccxt

import registro
from simulacao import simular_evolucao

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("monitor")

exchange = ccxt.bingx({"enableRateLimit": True})
TIMEFRAME = "15m"


def buscar_velas_desde(moeda: str, desde_dt: pd.Timestamp) -> pd.DataFrame:
    desde_ms = int(desde_dt.timestamp() * 1000)
    candles = exchange.fetch_ohlcv(moeda, timeframe=TIMEFRAME, since=desde_ms, limit=500)
    if not candles:
        return pd.DataFrame(columns=["datetime", "high", "low"])
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    # descarta a própria vela do sinal, olha só as posteriores
    df = df[df["datetime"] > desde_dt]
    return df[["datetime", "high", "low"]]


def main():
    df = registro.carregar_sinais()
    abertos = df[df["status"] == "ABERTO"]

    if abertos.empty:
        log.info("Nenhum sinal em aberto para monitorar.")
        return

    log.info(f"Monitorando {len(abertos)} sinal(is) em aberto...")

    for idx, sinal in abertos.iterrows():
        try:
            velas = buscar_velas_desde(sinal["moeda"], sinal["datetime_sinal"])
            if velas.empty:
                continue

            resultado = simular_evolucao(
                direcao=sinal["direcao"], entrada=sinal["entrada"],
                stop_inicial=sinal["stop"], alvo1=sinal["alvo1"], alvo2=sinal["alvo2"],
                velas_futuras=velas,
            )

            if resultado["status"] == "ABERTO":
                continue  # ainda não definiu, deixa pro próximo ciclo

            df.loc[idx, "status"] = resultado["status"]
            df.loc[idx, "resultado_r"] = resultado["resultado_r"]
            df.loc[idx, "datetime_saida"] = resultado["datetime_saida"]
            df.loc[idx, "preco_saida"] = resultado["preco_saida"]

            log.info(f"[{sinal['moeda']}] {sinal['direcao']} -> {resultado['status']} "
                      f"({resultado['resultado_r']:.2f}R)" if resultado["resultado_r"] is not None
                      else f"[{sinal['moeda']}] segue em aberto")

            time.sleep(exchange.rateLimit / 1000)

        except Exception as e:
            log.exception(f"Erro ao monitorar sinal {sinal['id']}: {e}")

    registro.salvar_sinais(df)
    log.info("Monitoramento concluído, CSV atualizado.")


if __name__ == "__main__":
    main()
