"""
Backtest do Robô de Sinais Cripto
==================================
Testa a MESMA lógica de sinal do bot.py (Suporte/Resistência + Volume +
Tendência multi-timeframe + RSI + Stop/Alvo via ATR) sobre dados
históricos reais da BingX, sem look-ahead bias (todos os indicadores
usam apenas dados disponíveis até o fechamento da vela anterior).

COMO RODAR:
    1. Instale as dependências:  pip install ccxt pandas numpy
    2. Rode:  python backtest.py
    3. Ao terminar, ele gera dois arquivos CSV:
       - backtest_resumo_por_moeda.csv  -> métricas por ativo
       - backtest_trades_detalhado.csv  -> cada trade simulado, linha a linha

IMPORTANTE:
    Este script precisa de acesso à internet para baixar histórico da
    BingX. Rode na sua máquina local ou no Railway (ex: `railway run
    python backtest.py`). Não precisa (e não deve) rodar continuamente,
    é só para validar a estratégia antes/depois de ajustes.

METODOLOGIA:
    - Um trade por vez por moeda (sem sobreposição de posições no mesmo ativo).
    - Entrada no fechamento da vela de sinal.
    - Ao tocar o Alvo 1, o stop é movido para o preço de entrada
      (trade vira "risco zero"), simulando gestão realista.
    - Resultado de cada trade medido em múltiplos de R (R = distância
      até o stop). Isso separa a qualidade da estratégia da alavancagem
      ou tamanho de posição escolhidos.
"""

import time

import numpy as np
import pandas as pd
import ccxt

from bot import (
    TIMEFRAME, TIMEFRAME_TENDENCIA, PERIODO_SR, PERIODO_VOLUME,
    MULTIPLICADOR_VOLUME, RSI_SOBRECOMPRA, RSI_SOBREVENDA,
    ATR_STOP_MULT, ATR_ALVO1_MULT, ATR_ALVO2_MULT,
    EMA_TENDENCIA, LISTA_DE_MOEDAS, calcular_rsi, calcular_atr,
)

exchange = ccxt.bingx({"enableRateLimit": True})

DIAS_BACKTEST = 180  # ~6 meses de histórico. Aumente/diminua conforme quiser.


# =========================================================
# COLETA DE DADOS HISTÓRICOS
# =========================================================

def baixar_historico(moeda: str, timeframe: str, dias: int) -> pd.DataFrame:
    """Baixa candles históricos paginando (a BingX limita ~1000 velas/chamada)."""
    ms_por_candle = exchange.parse_timeframe(timeframe) * 1000
    agora = exchange.milliseconds()
    desde = agora - dias * 24 * 60 * 60 * 1000

    todos = []
    while desde < agora:
        candles = exchange.fetch_ohlcv(moeda, timeframe=timeframe, since=desde, limit=1000)
        if not candles:
            break
        todos.extend(candles)
        ultimo_ts = candles[-1][0]
        if ultimo_ts == desde:
            break
        desde = ultimo_ts + ms_por_candle
        time.sleep(exchange.rateLimit / 1000)
        if len(candles) < 1000:
            break

    df = pd.DataFrame(todos, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


# =========================================================
# INDICADORES (idênticos ao bot.py, sem look-ahead)
# =========================================================

def preparar_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["resistencia"] = df["high"].shift(1).rolling(window=PERIODO_SR).max()
    df["suporte"] = df["low"].shift(1).rolling(window=PERIODO_SR).min()
    df["volume_medio"] = df["volume"].shift(1).rolling(window=PERIODO_VOLUME).mean()
    df["rsi"] = calcular_rsi(df["close"], 14)
    df["atr"] = calcular_atr(df, 14)
    return df


def preparar_tendencia(df_1h: pd.DataFrame) -> pd.DataFrame:
    df_1h = df_1h.copy()
    df_1h["ema"] = df_1h["close"].ewm(span=EMA_TENDENCIA, adjust=False).mean()
    df_1h["vies"] = np.where(
        df_1h["close"] > df_1h["ema"], "ALTA",
        np.where(df_1h["close"] < df_1h["ema"], "BAIXA", "NEUTRO"),
    )
    return df_1h[["datetime", "vies"]]


# =========================================================
# SIMULAÇÃO DE TRADES
# =========================================================

def simular_trades(moeda: str, df: pd.DataFrame) -> pd.DataFrame:
    """Percorre o histórico vela a vela (sem look-ahead), gera sinais na
    mesma lógica do bot.py e simula o resultado de cada trade em R."""
    trades = []
    aberto = None

    for i in range(len(df)):
        linha = df.iloc[i]

        # ---- gerencia trade já aberto ----
        if aberto is not None:
            if aberto["direcao"] == "COMPRA":
                if linha["low"] <= aberto["stop"]:
                    aberto["resultado_r"] = 0.0 if aberto["alvo1_tocado"] else -1.0
                    aberto["saida"] = aberto["stop"]
                    aberto["motivo_saida"] = "BREAKEVEN" if aberto["alvo1_tocado"] else "STOP"
                    trades.append(aberto)
                    aberto = None
                elif linha["high"] >= aberto["alvo2"]:
                    aberto["resultado_r"] = ATR_ALVO2_MULT / ATR_STOP_MULT
                    aberto["saida"] = aberto["alvo2"]
                    aberto["motivo_saida"] = "ALVO2"
                    trades.append(aberto)
                    aberto = None
                elif linha["high"] >= aberto["alvo1"] and not aberto["alvo1_tocado"]:
                    aberto["alvo1_tocado"] = True
                    aberto["stop"] = aberto["entrada"]  # breakeven após alvo 1
            else:  # VENDA
                if linha["high"] >= aberto["stop"]:
                    aberto["resultado_r"] = 0.0 if aberto["alvo1_tocado"] else -1.0
                    aberto["saida"] = aberto["stop"]
                    aberto["motivo_saida"] = "BREAKEVEN" if aberto["alvo1_tocado"] else "STOP"
                    trades.append(aberto)
                    aberto = None
                elif linha["low"] <= aberto["alvo2"]:
                    aberto["resultado_r"] = ATR_ALVO2_MULT / ATR_STOP_MULT
                    aberto["saida"] = aberto["alvo2"]
                    aberto["motivo_saida"] = "ALVO2"
                    trades.append(aberto)
                    aberto = None
                elif linha["low"] <= aberto["alvo1"] and not aberto["alvo1_tocado"]:
                    aberto["alvo1_tocado"] = True
                    aberto["stop"] = aberto["entrada"]
            continue  # sem sobreposição: não abre novo trade com um já em curso

        # ---- procura novo sinal ----
        if pd.isna(linha["suporte"]) or pd.isna(linha["volume_medio"]) or pd.isna(linha["atr"]) or pd.isna(linha["rsi"]):
            continue

        volume_confirmado = linha["volume"] > (linha["volume_medio"] * MULTIPLICADOR_VOLUME)
        sinal_compra = linha["low"] <= linha["suporte"] and volume_confirmado and linha["rsi"] < RSI_SOBRECOMPRA
        sinal_venda = linha["high"] >= linha["resistencia"] and volume_confirmado and linha["rsi"] > RSI_SOBREVENDA

        if not (sinal_compra or sinal_venda):
            continue

        direcao = "COMPRA" if sinal_compra else "VENDA"
        if direcao == "COMPRA" and linha["vies"] == "BAIXA":
            continue
        if direcao == "VENDA" and linha["vies"] == "ALTA":
            continue

        entrada = linha["close"]
        atr = linha["atr"]
        if direcao == "COMPRA":
            stop = entrada - atr * ATR_STOP_MULT
            alvo1 = entrada + atr * ATR_ALVO1_MULT
            alvo2 = entrada + atr * ATR_ALVO2_MULT
        else:
            stop = entrada + atr * ATR_STOP_MULT
            alvo1 = entrada - atr * ATR_ALVO1_MULT
            alvo2 = entrada - atr * ATR_ALVO2_MULT

        aberto = {
            "moeda": moeda, "direcao": direcao,
            "datetime_entrada": linha["datetime"],
            "entrada": entrada, "stop": stop,
            "alvo1": alvo1, "alvo2": alvo2,
            "alvo1_tocado": False,
        }

    return pd.DataFrame(trades)


# =========================================================
# MÉTRICAS
# =========================================================

def calcular_metricas(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {"total_trades": 0}

    ganhos = trades_df[trades_df["resultado_r"] > 0]
    perdas = trades_df[trades_df["resultado_r"] < 0]

    taxa_acerto = len(ganhos) / len(trades_df) * 100
    soma_ganhos = ganhos["resultado_r"].sum()
    soma_perdas = abs(perdas["resultado_r"].sum())
    profit_factor = soma_ganhos / soma_perdas if soma_perdas > 0 else float("inf")

    ordenado = trades_df.sort_values("datetime_entrada").reset_index(drop=True)
    ordenado["capital_r"] = ordenado["resultado_r"].cumsum()
    pico = ordenado["capital_r"].cummax()
    max_drawdown_r = (ordenado["capital_r"] - pico).min()

    return {
        "total_trades": len(trades_df),
        "taxa_acerto_pct": round(taxa_acerto, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "expectativa_r_por_trade": round(trades_df["resultado_r"].mean(), 2),
        "resultado_total_r": round(trades_df["resultado_r"].sum(), 2),
        "max_drawdown_r": round(max_drawdown_r, 2),
    }


# =========================================================
# EXECUÇÃO
# =========================================================

def main():
    resultados_gerais = []
    todos_trades = []

    for moeda in LISTA_DE_MOEDAS:
        print(f"Baixando histórico de {moeda}...")
        df_15m = baixar_historico(moeda, TIMEFRAME, DIAS_BACKTEST)
        df_1h = baixar_historico(moeda, TIMEFRAME_TENDENCIA, DIAS_BACKTEST)

        if len(df_15m) < 100 or len(df_1h) < EMA_TENDENCIA + 10:
            print(f"  Dados insuficientes para {moeda}, pulando.")
            continue

        df_15m = preparar_indicadores(df_15m)
        df_1h_vies = preparar_tendencia(df_1h)

        df_15m = pd.merge_asof(
            df_15m.sort_values("datetime"),
            df_1h_vies.sort_values("datetime"),
            on="datetime", direction="backward",
        )
        df_15m["vies"] = df_15m["vies"].fillna("NEUTRO")

        trades = simular_trades(moeda, df_15m)
        if not trades.empty:
            todos_trades.append(trades)

        metricas = calcular_metricas(trades)
        metricas["moeda"] = moeda
        resultados_gerais.append(metricas)
        print(f"  {moeda}: {metricas}")

    resumo = pd.DataFrame(resultados_gerais)
    resumo.to_csv("backtest_resumo_por_moeda.csv", index=False)

    if todos_trades:
        trades_completos = pd.concat(todos_trades, ignore_index=True)
        trades_completos.to_csv("backtest_trades_detalhado.csv", index=False)
        print("\n=== RESULTADO CONSOLIDADO (todas as moedas, período de "
              f"{DIAS_BACKTEST} dias) ===")
        print(calcular_metricas(trades_completos))
    else:
        print("\nNenhum trade gerado no período. Considere aumentar DIAS_BACKTEST "
              "ou revisar os parâmetros da estratégia.")

    print("\nArquivos salvos: backtest_resumo_por_moeda.csv, backtest_trades_detalhado.csv")


if __name__ == "__main__":
    main()
