"""
Módulo de registro de sinais.
==============================
Usado por bot.py (grava cada sinal enviado), monitorar_resultados.py
(atualiza o resultado de sinais em aberto) e gerar_relatorio.py (lê tudo
para montar o dashboard).

ATENÇÃO SOBRE PERSISTÊNCIA NO RAILWAY:
O sistema de arquivos do Railway é efêmero por padrão — se você fizer um
novo deploy (git push) ou o serviço reiniciar, o arquivo CSV local é
apagado. Para persistir de verdade entre deploys, crie um "Volume" no
Railway (Settings > Volumes) e defina a variável de ambiente
REGISTRO_CSV_PATH apontando para dentro dele, por exemplo:
    REGISTRO_CSV_PATH=/data/sinais_enviados.csv
Sem isso, o registro funciona normalmente enquanto o serviço estiver no
ar, só não sobrevive a um redeploy.
"""

import os
import csv
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

CAMINHO_CSV = os.getenv("REGISTRO_CSV_PATH", "sinais_enviados.csv")

COLUNAS = [
    "id", "moeda", "direcao", "datetime_sinal", "entrada", "stop",
    "alvo1", "alvo2", "atr", "motivo", "status", "resultado_r",
    "datetime_saida", "preco_saida",
]


def garantir_csv():
    caminho = Path(CAMINHO_CSV)
    if not caminho.exists():
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(COLUNAS)


def registrar_sinal(moeda, direcao, entrada, stop, alvo1, alvo2, atr, motivo, datetime_sinal=None):
    """Grava um novo sinal com status ABERTO. Retorna o id gerado."""
    garantir_csv()
    if datetime_sinal is None:
        datetime_sinal = datetime.now(timezone.utc)

    sinal_id = f"{moeda.replace('/', '')}_{int(datetime_sinal.timestamp())}"

    with open(CAMINHO_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            sinal_id, moeda, direcao, datetime_sinal.isoformat(),
            entrada, stop, alvo1, alvo2, atr, motivo,
            "ABERTO", "", "", "",
        ])
    return sinal_id


def carregar_sinais() -> pd.DataFrame:
    garantir_csv()
    df = pd.read_csv(CAMINHO_CSV)
    if not df.empty:
        # unidade "ns" fixada explicitamente: sem isso, uma coluna que só tem
        # NaT (ex: nenhum trade fechado ainda) vira dtype de baixa precisão e
        # o pandas recusa depois receber um Timestamp mais preciso ao atualizar.
        df["datetime_sinal"] = pd.to_datetime(df["datetime_sinal"], utc=True, format="ISO8601").astype("datetime64[ns, UTC]")
        df["datetime_saida"] = pd.to_datetime(df["datetime_saida"], utc=True, format="ISO8601", errors="coerce").astype("datetime64[ns, UTC]")
    return df


def salvar_sinais(df: pd.DataFrame):
    df.to_csv(CAMINHO_CSV, index=False)
