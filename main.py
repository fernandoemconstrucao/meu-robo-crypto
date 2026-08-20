import os
import time
import requests
import ccxt
import pandas as pd

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TIMEFRAME = "15m"
PERIODO = 20

# Sua nova lista profissional de multi-ativos
LISTA_DE_MOEDAS = [
    "SOL/USDT",
    "BTC/USDT",
    "ETH/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "SHIB/USDT",
    "PEPE/USDT",
    "BNB/USDT"
]

exchange = ccxt.bingx()

def enviar_mensagem_telegram(mensagem):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensagem}
    requests.post(url, json=payload)

def analisar_mercado():
    print("Analisando mercado na BingX")
    candles = exchange.fetch_ohlcv(moeda, timeframe=TIMEFRAME, limit=100)
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
        msg = f"SINAL DE COMPRA SOLANA Preco {preco_fechamento}"
        enviar_mensagem_telegram(msg)
        print("Sinal de Compra disparado")
    elif preco_maximo >= resistencia and volume_confirmado:
        msg = f"SINAL DE VENDA SOLANA Preco {preco_fechamento}"
        enviar_mensagem_telegram(msg)
        print("Sinal de Venda disparado")

enviar_mensagem_telegram("Robo de Sinais Ativo na Nuvem! Buscando oportunidades na Solana...")

while True:
    analisar_mercado()
    time.sleep(900)
