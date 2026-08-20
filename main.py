import os
import time
import requests
import ccxt
import pandas as pd

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TIMEFRAME = "15m"
PERIODO = 20

# Lista correta com todos os seus ativos
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
    url = f"url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    {TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensagem}
    requests.post(url, json=payload)

def analisar_mercado(moeda):
    print(f"Analisando {moeda} na BingX...")
    try:
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
            msg = f"🚨 SINAL DE COMPRA 🚨\nAtivo: {moeda}\nPreço Atual: {preco_fechamento}\nMotivo: Suporte + Volume Confirmado!"
            enviar_mensagem_telegram(msg)
            print(f"Sinal de Compra disparado para {moeda}")
        elif preco_maximo >= resistencia and volume_confirmado:
            msg = f"🚨 SINAL DE VENDA 🚨\nAtivo: {moeda}\nPreço Atual: {preco_fechamento}\nMotivo: Resistência + Volume Confirmado!"
            enviar_mensagem_telegram(msg)
            print(f"Sinal de Venda disparado para {moeda}")
            
    except Exception as e:
        print(f"Erro ao analisar a moeda {moeda}: {e}")

# O loop final correto que corrige o erro da linha 65
while True:
    print("--- Iniciando ciclo de varredura no mercado ---")
    for m in LISTA_DE_MOEDAS:
        analisar_mercado(m)
        time.sleep(2)
        
    print("Varredura concluida. Aguardando 15 minutos...")
    time.sleep(900)
