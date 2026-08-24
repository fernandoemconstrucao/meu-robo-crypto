"""
Simula a evolução de um trade específico (entrada, stop, alvo1, alvo2) a
partir de uma sequência de velas futuras — a mesma lógica de gestão usada
no backtest.py: ao tocar o Alvo 1, o stop vai para o preço de entrada.
"""

import pandas as pd


def simular_evolucao(direcao: str, entrada: float, stop_inicial: float,
                      alvo1: float, alvo2: float, velas_futuras: pd.DataFrame) -> dict:
    """
    velas_futuras precisa ter colunas: datetime, high, low (em ordem cronológica,
    todas com timestamp POSTERIOR ao sinal).

    Retorna dict com: status ("WIN_ALVO2", "BREAKEVEN", "STOP", ou "ABERTO"),
    resultado_r (float ou None se ainda aberto), datetime_saida, preco_saida.
    """
    stop = stop_inicial
    alvo1_tocado = False
    risco_inicial = abs(entrada - stop_inicial)

    if risco_inicial == 0:
        return {"status": "ERRO", "resultado_r": None, "datetime_saida": None, "preco_saida": None}

    for _, vela in velas_futuras.iterrows():
        if direcao == "COMPRA":
            if vela["low"] <= stop:
                resultado_r = 0.0 if alvo1_tocado else -1.0
                status = "BREAKEVEN" if alvo1_tocado else "STOP"
                return {"status": status, "resultado_r": resultado_r,
                        "datetime_saida": vela["datetime"], "preco_saida": stop}
            if vela["high"] >= alvo2:
                resultado_r = (alvo2 - entrada) / risco_inicial
                return {"status": "WIN_ALVO2", "resultado_r": resultado_r,
                        "datetime_saida": vela["datetime"], "preco_saida": alvo2}
            if vela["high"] >= alvo1 and not alvo1_tocado:
                alvo1_tocado = True
                stop = entrada
        else:  # VENDA
            if vela["high"] >= stop:
                resultado_r = 0.0 if alvo1_tocado else -1.0
                status = "BREAKEVEN" if alvo1_tocado else "STOP"
                return {"status": status, "resultado_r": resultado_r,
                        "datetime_saida": vela["datetime"], "preco_saida": stop}
            if vela["low"] <= alvo2:
                resultado_r = (entrada - alvo2) / risco_inicial
                return {"status": "WIN_ALVO2", "resultado_r": resultado_r,
                        "datetime_saida": vela["datetime"], "preco_saida": alvo2}
            if vela["low"] <= alvo1 and not alvo1_tocado:
                alvo1_tocado = True
                stop = entrada

    # percorreu todas as velas disponíveis e o trade ainda não bateu stop nem alvo2
    return {"status": "ABERTO", "resultado_r": None, "datetime_saida": None, "preco_saida": None}
