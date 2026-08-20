import time
import requests
import ccxt
import pandas as pd

# 1. CONFIGURAÇÕES INICIAIS
TELEGRAM_TOKEN = "COLE_SEU_TOKEN_DO_TELEGRAM_AQUI"
TELEGRAM_CHAT_ID = "COLE_SEU_CHAT_ID_AQUI"
SYMBOL = 'SOL/USDT'
TIMEFRAME = '15m'
PERIODO = 20

# Inicializa a conexão com a Binance
exchange = ccxt.binance()

def enviar_mensagem_telegram(mensagem):
    url = f"
    https://telegram.org
    {TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensagem}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Erro ao enviar Telegram: {e}")

def analisar_mercado():
    print(f"Analisando {SYMBOL} na Binance...")
    
    # Busca as últimas 100 velas
    candles = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=100)
    df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    # Cálculos de Suporte, Resistência e Média de Volume
    df['highest_high'] = df['high'].rolling(window=PERIODO).max()
    df['lowest_low'] = df['low'].rolling(window=PERIODO).min()
    df['avg_volume'] = df['volume'].rolling(window=PERIODO).mean()
    
    # Pega os dados da última vela fechada
    ultima_vela = df.iloc[-2]
    
    preco_fechamento = ultima_vela['close']
    preco_maximo = ultima_vela['high']
    preco_minimo = ultima_vela['low']
    volume_atual = ultima_vela['volume']
    
    suporte = ultima_vela['lowest_low']
    resistencia = ultima_vela['highest_high']
    volume_medio = ultima_vela['avg_volume']
    
    # Filtro de Volume (1.5x maior que a média)
    volume_confirmado = volume_atual > (volume_medio * 1.5)
    
    # Regras de Entrada
    if preco_minimo <= suporte and volume_confirmado:
        msg = f"🚨 SINAL DE COMPRA (SOLANA) 🚨\nAtivo: {SYMBOL}\nPreço Atual: {preco_fechamento}\nMotivo: Toque no Suporte com Volume Confirmado!"
        enviar_mensagem_telegram(msg)
        print("Sinal de Compra disparado!")
        
    elif preco_maximo >= resistencia and volume_confirmado:
        msg = f"🚨 SINAL DE VENDA (SOLANA) 🚨\nAtivo: {SYMBOL}\nPreço Atual: {preco_fechamento}\nMotivo: Toque na Resistência com Volume Confirmado!"
        enviar_mensagem_telegram(msg)
        print("Sinal de Venda disparado!")

# Loop para rodar continuamente na nuvem
while True:
    try:
        analisar_mercado()
        time.sleep(900)
    except Exception as e:
        print(f"Erro no sistema: {e}")
        time.sleep(60)
