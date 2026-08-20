import os
import time
import requests
import ccxt
import pandas as pd

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOL = "SOL/USDT"
TIMEFRAME = "15m"
PERIODO = 20

exchange = ccxt.bingx()

enviar_mensagem_telegram("Robô de Sinais Ativo na Nuvem! Buscando oportunidades na Solana...")

def enviar_mensagem_telegram(mensagem):
    url = "https://telegram.org" + str(TOKEN) + "/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensagem}
    requests.post(url, json=payload)

def analisar_mercado():
    print("Analisando mercado na Binance")
    candles = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=100)
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    
    df["highest_high"] = df["high"].rolling(window=PERIODO).max()
    df["lowest_low"] = df["low"].rolling(window=PERIODO).min()
    df["avg_volume"] = df["volume"].rolling(window=PERIODO).mean()
    
    ultima_vela = df.iloc[-2]
    
    preco_fechamento = ultima_vela["close"]
    preco_maximo = ultima_vela["high"]
    preco_minimo = ultima_vela["low"]
    volume_atual = ultima_vela["volume"]
    
    suporte = ultima_vela["lowest_low"]
    resistencia = ultima_vela["highest_high"]
    volume_medio = ultima_vela["avg_volume"]
    
    volume_confirmado = volume_atual > (volume_medio * 1.5)
    
    if preco_minimo <= suporte and volume_confirmado:
        msg = "SINAL DE COMPRA SOLANA Preco " + str(preco_fechamento)
        enviar_mensagem_telegram(msg)
        print("Sinal de Compra disparado")
    elif preco_maximo >= resistencia and volume_confirmado:
        msg = "SINAL DE VENDA SOLANA Preco " + str(preco_fechamento)
        enviar_mensagem_telegram(msg)
        print("Sinal de Venda disparado")

while True:
    analisar_mercado()
    time.sleep(900)
