"""
Robô de Sinais Cripto - Suporte/Resistência com Confluência
=============================================================
Estratégia base: Rompimento de Suporte/Resistência (período configurável)
confirmado por Volume + Filtro de Tendência (EMA multi-timeframe) + RSI.

Correções aplicadas na versão original:
- S/R calculado SEM a vela atual (elimina viés de olhar pra trás / tautologia)
- Deduplicação de sinais por vela (não repete o mesmo sinal)
- Loop alinhado ao fechamento real da vela (sem drift de horário)
- Stop Loss e Take Profit calculados via ATR (proporcional à volatilidade real)
- Filtro de tendência no timeframe superior (evita operar contra a maré)
- RSI como filtro de exaustão (evita comprar topo / vender fundo)
- Logging estruturado, retries e tratamento de erros por símbolo/API
"""

import os
import time
import tempfile
import logging
from pathlib import Path
from datetime import datetime, timezone

import requests
import ccxt
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import mplfinance as mpf

import registro
from formatacao import formatar_preco_dinamico

# =========================================================
# CONFIGURAÇÃO
# =========================================================

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME = "15m"              # timeframe de operação
TIMEFRAME_TENDENCIA = "1h"     # timeframe superior, usado só para filtro de viés
PERIODO_SR = 20                # período do canal de suporte/resistência
PERIODO_VOLUME = 20            # período da média de volume
MULTIPLICADOR_VOLUME = 1.5     # volume precisa ser 1.5x a média
PERIODO_RSI = 14
RSI_SOBRECOMPRA = 75           # acima disso, não confia em rompimento de alta
RSI_SOBREVENDA = 25            # abaixo disso, não confia em rompimento de baixa
PERIODO_ATR = 14
ATR_STOP_MULT = 1.5            # stop loss = 1.5x ATR
ATR_ALVO1_MULT = 1.5           # alvo 1 = 1.5x ATR (risco:retorno 1:1)
ATR_ALVO2_MULT = 3.0           # alvo 2 = 3x ATR (risco:retorno 1:2)
EMA_TENDENCIA = 50              # EMA usada no timeframe superior p/ definir viés

PERIODO_ADX = 14
ADX_MINIMO = 20                 # abaixo disso, mercado é considerado "sem tendência"
                                 # (rompimentos de S/R tendem a ser falsos nesse cenário)

MAX_TRADES_SIMULTANEOS = 3      # limite de sinais em aberto ao mesmo tempo (todos os
                                 # ativos somados) — protege contra quedas correlacionadas
                                 # do mercado cripto derrubando vários sinais de uma vez

LISTA_DE_MOEDAS = [
    "SOL/USDT", "BTC/USDT", "ETH/USDT", "XRP/USDT",
    "DOGE/USDT", "PEPE/USDT", "BNB/USDT",
    # SHIB/USDT removido em 25/08/2026: backtest de 300 dias mostrou profit
    # factor de apenas 1,02 (praticamente empatado), e a tentativa de exigir
    # mais volume pra esse ativo especificamente piorou o resultado (foi pra
    # 0,83, -6R). Os dados não sustentam manter esse ativo na lista por ora.
]

INTERVALO_ENTRE_MOEDAS_SEG = 2   # respiro entre chamadas à API por símbolo
MAX_TENTATIVAS = 3               # tentativas em caso de erro de rede/API

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("robo_sinais")

# =========================================================
# VALIDAÇÃO DE AMBIENTE
# =========================================================

def validar_ambiente():
    """Só é chamado ao rodar o robô de verdade (main), não ao importar
    este arquivo como módulo (ex: no backtest.py)."""
    if not TOKEN or not CHAT_ID:
        log.error("TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID não configurados nas variáveis de ambiente do Railway.")
        raise SystemExit(1)


exchange = ccxt.bingx({
    "enableRateLimit": True,   # evita ban por excesso de requisições
})

_mercados_carregados = False


def formatar_preco(moeda: str, preco: float) -> str:
    """
    Formata o preço usando a precisão REAL definida pela própria BingX
    para aquele par (o mesmo número de casas decimais que ela usa para
    negociar) — isso resolve o problema de moedas tipo SHIB/PEPE, que
    têm muitas casas decimais e ficavam cortadas com um formato fixo.
    Se por algum motivo a exchange não puder ser consultada (ex: rodando
    o backtest sem essa etapa carregada), cai no cálculo dinâmico local.
    """
    global _mercados_carregados
    try:
        if not _mercados_carregados:
            exchange.load_markets()
            _mercados_carregados = True
        return exchange.price_to_precision(moeda, preco)
    except Exception as e:
        log.warning(f"[{moeda}] Não foi possível usar a precisão da exchange ({e}), usando formatação local.")
        return formatar_preco_dinamico(preco)

# Guarda o timestamp da última vela que já gerou sinal, por moeda+direção,
# para não repetir o mesmo sinal em ciclos seguidos.
ultimo_sinal = {}  # {"BTC/USDT_COMPRA": timestamp_da_vela}


# =========================================================
# TELEGRAM
# =========================================================

def enviar_mensagem_telegram(mensagem: str) -> bool:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensagem, "parse_mode": "Markdown"}

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True
            log.warning(f"Telegram respondeu {resp.status_code}: {resp.text}")
        except requests.RequestException as e:
            log.warning(f"Tentativa {tentativa}/{MAX_TENTATIVAS} falhou ao enviar Telegram: {e}")
        time.sleep(2)

    log.error("Falha definitiva ao enviar mensagem para o Telegram.")
    return False


def enviar_foto_telegram(caminho_imagem: str, legenda: str = "") -> bool:
    """Envia uma imagem (ex: gráfico de resultado) ao Telegram, com legenda opcional."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            with open(caminho_imagem, "rb") as foto:
                resp = requests.post(
                    url,
                    data={"chat_id": CHAT_ID, "caption": legenda[:1024], "parse_mode": "Markdown"},
                    files={"photo": foto},
                    timeout=20,
                )
            if resp.status_code == 200:
                return True
            log.warning(f"Telegram (foto) respondeu {resp.status_code}: {resp.text}")
        except (requests.RequestException, OSError) as e:
            log.warning(f"Tentativa {tentativa}/{MAX_TENTATIVAS} falhou ao enviar foto: {e}")
        time.sleep(2)

    log.error("Falha definitiva ao enviar imagem para o Telegram.")
    return False


# =========================================================
# COLETA E INDICADORES
# =========================================================

def buscar_candles(moeda: str, timeframe: str, limit: int = 150) -> pd.DataFrame | None:
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            candles = exchange.fetch_ohlcv(moeda, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
            return df
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            log.warning(f"[{moeda}] Tentativa {tentativa}/{MAX_TENTATIVAS} falhou ao buscar candles: {e}")
            time.sleep(2)
    log.error(f"[{moeda}] Não foi possível obter candles após {MAX_TENTATIVAS} tentativas.")
    return None


def calcular_rsi(close: pd.Series, periodo: int) -> pd.Series:
    delta = close.diff()
    ganho = delta.clip(lower=0)
    perda = -delta.clip(upper=0)
    media_ganho = ganho.rolling(window=periodo).mean()
    media_perda = perda.rolling(window=periodo).mean()
    rs = media_ganho / media_perda.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calcular_atr(df: pd.DataFrame, periodo: int) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(window=periodo).mean()


def calcular_adx(df: pd.DataFrame, periodo: int) -> pd.Series:
    """
    ADX (Average Directional Index): mede a FORÇA de uma tendência,
    independente da direção. Valores abaixo de ~20 indicam mercado sem
    tendência definida (lateral) — nesse cenário, rompimentos de S/R têm
    muito mais chance de serem falsos (o preço rompe e volta logo depois).
    """
    high, low, close = df["high"], df["low"], df["close"]

    variacao_alta = high.diff()
    variacao_baixa = -low.diff()
    dm_mais = np.where((variacao_alta > variacao_baixa) & (variacao_alta > 0), variacao_alta, 0.0)
    dm_menos = np.where((variacao_baixa > variacao_alta) & (variacao_baixa > 0), variacao_baixa, 0.0)

    tr = pd.concat([
        high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(window=periodo).mean()
    di_mais = 100 * pd.Series(dm_mais, index=df.index).rolling(window=periodo).mean() / atr
    di_menos = 100 * pd.Series(dm_menos, index=df.index).rolling(window=periodo).mean() / atr

    dx = 100 * (di_mais - di_menos).abs() / (di_mais + di_menos).replace(0, 1e-10)
    return dx.rolling(window=periodo).mean()


def obter_multiplicador_volume(moeda: str) -> float:
    # Dicionário reservado para ajustes futuros de volume por ativo específico,
    # caso algum outro par mostre o mesmo padrão de fraqueza que o SHIB mostrou.
    # Vazio por enquanto: a tentativa de usar isso no SHIB piorou o resultado
    # (profit factor caiu de 1,02 para 0,83) — o ativo foi removido da lista
    # em vez de ajustado. Ver LISTA_DE_MOEDAS para o histórico dessa decisão.
    overrides = {}
    return overrides.get(moeda, MULTIPLICADOR_VOLUME)


def calcular_indicadores(df: pd.DataFrame) -> pd.DataFrame:
    # IMPORTANTE: shift(1) exclui a vela atual do cálculo de S/R e volume médio,
    # senão a vela é comparada consigo mesma (bug da versão anterior).
    df["resistencia"] = df["high"].shift(1).rolling(window=PERIODO_SR).max()
    df["suporte"] = df["low"].shift(1).rolling(window=PERIODO_SR).min()
    df["volume_medio"] = df["volume"].shift(1).rolling(window=PERIODO_VOLUME).mean()
    df["rsi"] = calcular_rsi(df["close"], PERIODO_RSI)
    df["atr"] = calcular_atr(df, PERIODO_ATR)
    df["adx"] = calcular_adx(df, PERIODO_ADX)
    return df


def obter_vies_tendencia(moeda: str) -> str:
    """Retorna 'ALTA', 'BAIXA' ou 'NEUTRO' com base na EMA do timeframe superior."""
    df = buscar_candles(moeda, TIMEFRAME_TENDENCIA, limit=EMA_TENDENCIA + 10)
    if df is None or len(df) < EMA_TENDENCIA:
        return "NEUTRO"
    ema = df["close"].ewm(span=EMA_TENDENCIA, adjust=False).mean()
    preco_atual = df["close"].iloc[-1]
    if preco_atual > ema.iloc[-1]:
        return "ALTA"
    elif preco_atual < ema.iloc[-1]:
        return "BAIXA"
    return "NEUTRO"


# =========================================================
# FORMATAÇÃO DO SINAL
# =========================================================

def calcular_niveis(preco, atr, direcao):
    if direcao == "COMPRA":
        stop = preco - (atr * ATR_STOP_MULT)
        alvo1 = preco + (atr * ATR_ALVO1_MULT)
        alvo2 = preco + (atr * ATR_ALVO2_MULT)
    else:
        stop = preco + (atr * ATR_STOP_MULT)
        alvo1 = preco - (atr * ATR_ALVO1_MULT)
        alvo2 = preco - (atr * ATR_ALVO2_MULT)
    return stop, alvo1, alvo2


def gerar_grafico_sinal(df: pd.DataFrame, indice_sinal: int, moeda: str, direcao: str,
                         entrada: float, stop: float, alvo1: float, alvo2: float,
                         suporte: float, resistencia: float) -> str | None:
    """
    Gera um gráfico de candles mostrando as últimas ~50 velas até o momento
    do sinal, com linhas horizontais marcando entrada, stop, alvos e o
    nível de S/R que foi rompido — dá pra "ver" o contexto de mercado que
    gerou aquele sinal, não só ler os números.
    """
    try:
        inicio = max(0, indice_sinal - 49)
        janela = df.iloc[inicio: indice_sinal + 1].copy()
        if len(janela) < 10:
            return None

        janela["datetime"] = pd.to_datetime(janela["timestamp"], unit="ms", utc=True)
        janela = janela.set_index("datetime")
        janela = janela.rename(columns={
            "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume",
        })

        cores_mercado = mpf.make_marketcolors(
            up="#4ade80", down="#f87171", edge="inherit", wick="inherit",
            volume={"up": "#4ade80", "down": "#f87171"},
        )
        estilo = mpf.make_mpf_style(
            base_mpf_style="nightclouds", marketcolors=cores_mercado,
            gridcolor="#2a2e3d", gridstyle="--", facecolor="#161925",
            figcolor="#0f1117", edgecolor="#2a2e3d", rc={"font.size": 9},
        )

        linhas = dict(
            hlines=[entrada, stop, alvo1, alvo2, suporte, resistencia],
            colors=["#60a5fa", "#f87171", "#4ade80", "#4ade80", "#9aa0a6", "#9aa0a6"],
            linestyle=["-", "--", "--", "--", ":", ":"],
            linewidths=[1.4, 1.2, 1.2, 1.2, 1.0, 1.0],
        )

        titulo = f"{moeda} · {direcao} · {TIMEFRAME}"
        fig, axlist = mpf.plot(
            janela, type="candle", style=estilo, title=titulo, volume=True,
            hlines=linhas, returnfig=True, figsize=(9, 5.5),
        )

        legenda = [
            Line2D([0], [0], color="#60a5fa", linewidth=1.4, label=f"Entrada ({formatar_preco(moeda, entrada)})"),
            Line2D([0], [0], color="#4ade80", linewidth=1.2, linestyle="--", label="Alvo 1 / Alvo 2"),
            Line2D([0], [0], color="#f87171", linewidth=1.2, linestyle="--", label=f"Stop ({formatar_preco(moeda, stop)})"),
            Line2D([0], [0], color="#9aa0a6", linewidth=1.0, linestyle=":", label="Suporte/Resistência"),
        ]
        axlist[0].legend(handles=legenda, loc="upper left", facecolor="#1a1d29",
                         edgecolor="#2a2e3d", labelcolor="#e6e6e6", fontsize=8, framealpha=0.9)

        caminho = Path(tempfile.gettempdir()) / f"sinal_{moeda.replace('/', '')}_{indice_sinal}.png"
        fig.savefig(caminho, facecolor=fig.get_facecolor(), bbox_inches="tight", dpi=150)
        plt.close(fig)
        return str(caminho)
    except Exception as e:
        log.warning(f"[{moeda}] Não foi possível gerar o gráfico de candles do sinal: {e}")
        return None


def montar_mensagem(moeda, direcao, preco, stop, alvo1, alvo2, motivo) -> str:
    emoji = "🟢" if direcao == "COMPRA" else "🔴"
    agora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    return (
        f"{emoji} *SINAL DE {direcao}* {emoji}\n\n"
        f"*Ativo:* {moeda}\n"
        f"*Timeframe:* {TIMEFRAME}\n"
        f"*Preço de Entrada:* {formatar_preco(moeda, preco)}\n\n"
        f"🎯 *Alvo 1:* {formatar_preco(moeda, alvo1)}\n"
        f"🎯 *Alvo 2:* {formatar_preco(moeda, alvo2)}\n"
        f"🛑 *Stop Loss:* {formatar_preco(moeda, stop)}\n\n"
        f"*Motivo:* {motivo}\n"
        f"*Horário:* {agora}\n\n"
        f"⚠️ _Sinal gerado por análise técnica automatizada. Não constitui "
        f"recomendação de investimento. Gerencie seu risco e nunca aloque "
        f"mais do que está disposto a perder._"
    )


# =========================================================
# ANÁLISE PRINCIPAL
# =========================================================

def analisar_mercado(moeda: str):
    log.info(f"Analisando {moeda}...")

    df = buscar_candles(moeda, TIMEFRAME)
    if df is None or len(df) < max(PERIODO_SR, PERIODO_VOLUME, PERIODO_ATR) + 5:
        return

    df = calcular_indicadores(df)

    # Usa a última vela FECHADA (penúltima do array) para evitar sinais em
    # velas ainda em formação, que podem mudar até o fechamento.
    vela = df.iloc[-2]
    vela_timestamp = vela["timestamp"]

    preco_fechamento = vela["close"]
    preco_maximo = vela["high"]
    preco_minimo = vela["low"]
    volume_atual = vela["volume"]

    if (pd.isna(vela["suporte"]) or pd.isna(vela["volume_medio"]) or pd.isna(vela["atr"])
            or pd.isna(vela["rsi"]) or pd.isna(vela["adx"])):
        return  # dados insuficientes ainda (início do histórico)

    volume_confirmado = volume_atual > (vela["volume_medio"] * obter_multiplicador_volume(moeda))
    tendencia_forte = vela["adx"] > ADX_MINIMO

    sinal_compra = preco_minimo <= vela["suporte"] and volume_confirmado and vela["rsi"] < RSI_SOBRECOMPRA and tendencia_forte
    sinal_venda = preco_maximo >= vela["resistencia"] and volume_confirmado and vela["rsi"] > RSI_SOBREVENDA and tendencia_forte

    if not (sinal_compra or sinal_venda):
        return

    direcao = "COMPRA" if sinal_compra else "VENDA"
    chave_dedup = f"{moeda}_{direcao}_{vela_timestamp}"

    if ultimo_sinal.get(f"{moeda}_{direcao}") == vela_timestamp:
        log.info(f"[{moeda}] Sinal de {direcao} já enviado para esta vela, ignorando repetição.")
        return

    sinais_abertos = registro.carregar_sinais()

    # Trava por ativo: nunca emitir um novo sinal enquanto já existir um sinal
    # em aberto NESSE MESMO ativo (em qualquer direção). Sem isso, o robô pode
    # emitir vários sinais seguidos no mesmo ativo em poucos minutos enquanto
    # o mercado ainda está "testando" o rompimento — empilhando exposição no
    # pior tipo de risco (mesmo ativo = correlação de 100%). Essa também é a
    # mesma regra que o backtest.py sempre seguiu (nunca simula dois trades
    # sobrepostos no mesmo ativo) — sem essa trava, o robô ao vivo podia se
    # comportar de um jeito que o backtest nunca validou.
    if not sinais_abertos.empty:
        aberto_neste_ativo = ((sinais_abertos["moeda"] == moeda) & (sinais_abertos["status"] == "ABERTO")).any()
        if aberto_neste_ativo:
            log.info(f"[{moeda}] Sinal ignorado: já existe um sinal em aberto para este ativo.")
            return

    # Limite de risco de portfólio: nunca deixar mais que MAX_TRADES_SIMULTANEOS
    # sinais em aberto ao mesmo tempo (somando todos os ativos), pra evitar que
    # uma queda/alta correlacionada do mercado cripto acerte vários de uma vez.
    qtd_abertos = (sinais_abertos["status"] == "ABERTO").sum() if not sinais_abertos.empty else 0
    if qtd_abertos >= MAX_TRADES_SIMULTANEOS:
        log.info(f"[{moeda}] Sinal ignorado: limite de {MAX_TRADES_SIMULTANEOS} trades "
                  f"simultâneos já atingido ({qtd_abertos} em aberto).")
        return

    # Filtro de confluência: só confirma o sinal se a tendência do timeframe
    # superior concordar com a direção do rompimento.
    vies = obter_vies_tendencia(moeda)
    if direcao == "COMPRA" and vies == "BAIXA":
        log.info(f"[{moeda}] Rompimento de suporte ignorado: tendência de {TIMEFRAME_TENDENCIA} está em BAIXA.")
        return
    if direcao == "VENDA" and vies == "ALTA":
        log.info(f"[{moeda}] Rompimento de resistência ignorado: tendência de {TIMEFRAME_TENDENCIA} está em ALTA.")
        return

    motivo = (
        f"Rompimento de {'suporte' if direcao == 'COMPRA' else 'resistência'} "
        f"({PERIODO_SR} períodos) + Volume {obter_multiplicador_volume(moeda)}x acima da média "
        f"+ ADX {vela['adx']:.1f} (tendência confirmada) "
        f"+ Tendência {TIMEFRAME_TENDENCIA} alinhada ({vies}) + RSI em {vela['rsi']:.1f}"
    )

    stop, alvo1, alvo2 = calcular_niveis(preco_fechamento, vela["atr"], direcao)
    mensagem = montar_mensagem(moeda, direcao, preco_fechamento, stop, alvo1, alvo2, motivo)

    if enviar_mensagem_telegram(mensagem):
        ultimo_sinal[f"{moeda}_{direcao}"] = vela_timestamp
        registro.registrar_sinal(
            moeda=moeda, direcao=direcao, entrada=preco_fechamento,
            stop=stop, alvo1=alvo1, alvo2=alvo2, atr=vela["atr"], motivo=motivo,
        )
        log.info(f"[{moeda}] Sinal de {direcao} enviado e registrado com sucesso.")

        caminho_grafico = gerar_grafico_sinal(
            df, indice_sinal=len(df) - 2, moeda=moeda, direcao=direcao,
            entrada=preco_fechamento, stop=stop, alvo1=alvo1, alvo2=alvo2,
            suporte=vela["suporte"], resistencia=vela["resistencia"],
        )
        if caminho_grafico:
            enviar_foto_telegram(caminho_grafico, f"📈 Contexto do sinal: {moeda} ({TIMEFRAME})")
            Path(caminho_grafico).unlink(missing_ok=True)


# =========================================================
# CONTROLE DE TEMPO (alinhamento ao fechamento real da vela)
# =========================================================

def segundos_ate_proxima_vela(timeframe: str) -> float:
    unidades = {"m": 60, "h": 3600, "d": 86400}
    valor = int(timeframe[:-1])
    unidade = timeframe[-1]
    duracao_seg = valor * unidades[unidade]

    agora = time.time()
    resto = agora % duracao_seg
    espera = duracao_seg - resto
    return espera + 5  # pequena margem para garantir que a vela já fechou na exchange


# =========================================================
# LOOP PRINCIPAL
# =========================================================

def main():
    validar_ambiente()
    log.info("Robô de sinais iniciado.")
    enviar_mensagem_telegram("🤖 Robô de sinais *online* e monitorando o mercado.")

    while True:
        log.info("--- Iniciando ciclo de varredura no mercado ---")
        for moeda in LISTA_DE_MOEDAS:
            try:
                analisar_mercado(moeda)
            except Exception as e:
                # Nunca deixa uma falha em um ativo derrubar o robô inteiro
                log.exception(f"Erro inesperado ao analisar {moeda}: {e}")
            time.sleep(INTERVALO_ENTRE_MOEDAS_SEG)

        espera = segundos_ate_proxima_vela(TIMEFRAME)
        log.info(f"Varredura concluída. Aguardando {espera/60:.1f} min até o fechamento da próxima vela...")
        time.sleep(espera)


if __name__ == "__main__":
    main()
