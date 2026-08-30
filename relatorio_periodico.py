"""
Relatório Periódico de Convergência (Ao Vivo vs. Backtest)
==============================================================
Compara o desempenho REAL dos sinais enviados (dados de registro.py) com
a referência validada no backtest de 300 dias, em 3 janelas de tempo:
3 dias, 7 dias (semanal) e mensal (ao fechar o mês).

Por que isso importa: se o robô ao vivo continuar performando parecido
com o que o backtest previu, é uma evidência forte de que a estratégia
é genuína (não sorte). Se começar a divergir de forma consistente pra
baixo, é um alerta precoce de que o mercado mudou de comportamento —
muito antes disso ficar óbvio "no olho".

COMO RODAR:
    python relatorio_periodico.py

Pensado para rodar 1x por dia (ex: um Cron Job no Railway, às 00:05 UTC,
logo depois do monitorar_resultados.py). O script decide sozinho, a cada
execução, se já passou tempo suficiente para mandar cada tipo de
relatório (usando o arquivo estado_relatorios.json como memória) — então
não tem problema rodar todo dia, ele só envia quando for a hora certa.
"""

import json
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd
import matplotlib
matplotlib.use("Agg")  # sem interface gráfica, só gera o arquivo de imagem
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

import registro
from bot import enviar_mensagem_telegram, enviar_foto_telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("relatorio_periodico")

CAMINHO_ESTADO = Path("estado_relatorios.json")

# Referência validada no backtest de 300 dias (7 moedas, sem SHIB, com ADX) em 25/08/2026.
# Atualize estes valores manualmente sempre que rodar um novo backtest que
# mude materialmente esses números — assim a régua de comparação continua
# refletindo a versão mais atual e validada da estratégia.
REFERENCIA = {
    "taxa_acerto_pct": 32.2,
    "profit_factor": 1.58,
    "expectativa_r": 0.24,
    "atualizado_em": "2026-08-25",
}

INTERVALO_3D_DIAS = 3
INTERVALO_7D_DIAS = 7

# Margem de tolerância antes de considerar "divergente" (não é uma ciência
# exata — é um alerta pra você prestar atenção, não uma sentença definitiva).
TOLERANCIA_TAXA_ACERTO_PP = 8    # pontos percentuais abaixo da referência
TOLERANCIA_PROFIT_FACTOR = 0.35  # abaixo da referência


def carregar_estado() -> dict:
    if CAMINHO_ESTADO.exists():
        return json.loads(CAMINHO_ESTADO.read_text(encoding="utf-8"))
    return {"ultimo_3d": None, "ultimo_7d": None, "ultimo_mensal": None}


def salvar_estado(estado: dict):
    CAMINHO_ESTADO.write_text(json.dumps(estado, indent=2), encoding="utf-8")


def dias_desde(data_iso: str | None) -> float:
    if data_iso is None:
        return float("inf")
    data = datetime.fromisoformat(data_iso)
    return (datetime.now(timezone.utc) - data).total_seconds() / 86400


def obter_janela_periodo(df: pd.DataFrame, dias: int) -> pd.DataFrame:
    limite = datetime.now(timezone.utc) - timedelta(days=dias)
    return df[(df["resultado_r"].notna()) & (df["datetime_sinal"] >= limite)]


def obter_janela_mes(df: pd.DataFrame, ano: int, mes: int) -> pd.DataFrame:
    inicio = datetime(ano, mes, 1, tzinfo=timezone.utc)
    fim = datetime(ano + (mes == 12), (mes % 12) + 1, 1, tzinfo=timezone.utc)
    return df[(df["resultado_r"].notna()) & (df["datetime_sinal"] >= inicio) & (df["datetime_sinal"] < fim)]


def calcular_metricas_periodo(df: pd.DataFrame, dias: int) -> dict | None:
    return _resumo(obter_janela_periodo(df, dias))


def calcular_metricas_mes_fechado(df: pd.DataFrame, ano: int, mes: int) -> dict | None:
    return _resumo(obter_janela_mes(df, ano, mes))


def _resumo(janela: pd.DataFrame) -> dict | None:
    if janela.empty:
        return None
    ganhos = janela[janela["resultado_r"] > 0]
    perdas = janela[janela["resultado_r"] < 0]
    soma_ganhos = ganhos["resultado_r"].sum()
    soma_perdas = abs(perdas["resultado_r"].sum())
    return {
        "trades": len(janela),
        "wins": int((janela["status"] == "WIN_ALVO2").sum()),
        "breakevens": int((janela["status"] == "BREAKEVEN").sum()),
        "losses": int((janela["status"] == "STOP").sum()),
        "taxa_acerto_pct": round(len(ganhos) / len(janela) * 100, 1),
        "profit_factor": round(soma_ganhos / soma_perdas, 2) if soma_perdas > 0 else None,
        "expectativa_r": round(janela["resultado_r"].mean(), 2),
        "resultado_total_r": round(janela["resultado_r"].sum(), 2),
        "melhor_trade": janela.loc[janela["resultado_r"].idxmax()] if len(janela) > 0 else None,
    }


def gerar_grafico_periodo(janela: pd.DataFrame, titulo: str) -> str:
    """
    Gera uma imagem PNG com a curva de resultado acumulado (em R) do
    período, verde se terminou positivo, vermelha se terminou negativo,
    com cada trade individual marcado (verde=win, vermelho=loss,
    amarelo=breakeven). Retorna o caminho do arquivo gerado.
    """
    janela = janela.sort_values("datetime_sinal").reset_index(drop=True)
    janela["r_acumulado"] = janela["resultado_r"].cumsum()

    cores_pontos = janela["resultado_r"].apply(
        lambda r: "#4ade80" if r > 0 else ("#f87171" if r < 0 else "#fbbf24")
    )
    resultado_final = janela["r_acumulado"].iloc[-1]
    cor_linha = "#4ade80" if resultado_final >= 0 else "#f87171"

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    fig.patch.set_facecolor("#0f1117")
    ax.set_facecolor("#161925")

    x = range(len(janela))
    ax.plot(x, janela["r_acumulado"], color=cor_linha, linewidth=2.5, zorder=2)
    ax.fill_between(x, janela["r_acumulado"], 0, color=cor_linha, alpha=0.15, zorder=1)
    ax.scatter(x, janela["r_acumulado"], c=cores_pontos, s=32, zorder=3,
               edgecolors="#0f1117", linewidths=0.6)

    ax.axhline(0, color="#3a3f4f", linewidth=1, linestyle="--", zorder=1)
    ax.set_title(titulo, color="#e6e6e6", fontsize=15, fontweight="bold", pad=14)
    ax.set_xlabel("Sinais no período", color="#9aa0a6", fontsize=10)
    ax.set_ylabel("Resultado Acumulado (R)", color="#9aa0a6", fontsize=10)
    ax.tick_params(colors="#9aa0a6", labelsize=9)
    for spine in ax.spines.values():
        spine.set_color("#2a2e3d")
    ax.grid(color="#2a2e3d", linewidth=0.5, alpha=0.6)

    ax.text(0.98, 0.06, f"{resultado_final:+.1f}R", transform=ax.transAxes,
            ha="right", va="bottom", fontsize=22, fontweight="bold", color=cor_linha)

    # Legenda explicando cada cor — fica dentro da própria imagem, então
    # qualquer pessoa que entrar no grupo depois entende sem precisar perguntar.
    legenda = [
        Line2D([0], [0], marker='o', color='none', markerfacecolor='#4ade80',
               markersize=9, label='Win (bateu Alvo 2)'),
        Line2D([0], [0], marker='o', color='none', markerfacecolor='#f87171',
               markersize=9, label='Loss (bateu Stop)'),
        Line2D([0], [0], marker='o', color='none', markerfacecolor='#fbbf24',
               markersize=9, label='Breakeven (empatou)'),
    ]
    ax.legend(handles=legenda, loc='upper left', facecolor='#1a1d29', edgecolor='#2a2e3d',
              labelcolor='#e6e6e6', fontsize=9, framealpha=0.9)

    caminho = Path(tempfile.gettempdir()) / f"grafico_{titulo.lower().replace(' ', '_')}.png"
    fig.tight_layout()
    fig.savefig(caminho, facecolor=fig.get_facecolor())
    plt.close(fig)
    return str(caminho)


def avaliar_convergencia(metricas: dict) -> str:
    if metricas["profit_factor"] is None:
        return "⚠️"
    taxa_ok = metricas["taxa_acerto_pct"] >= REFERENCIA["taxa_acerto_pct"] - TOLERANCIA_TAXA_ACERTO_PP
    pf_ok = metricas["profit_factor"] >= REFERENCIA["profit_factor"] - TOLERANCIA_PROFIT_FACTOR
    return "✅" if (taxa_ok and pf_ok) else "⚠️"


def montar_mensagem_periodo(titulo: str, dias: int, m: dict) -> str:
    selo = avaliar_convergencia(m)
    pf_texto = f"{m['profit_factor']}" if m["profit_factor"] is not None else "N/A (sem perdas ainda)"

    return (
        f"📊 *{titulo}* {selo}\n\n"
        f"Período: últimos {dias} dias\n"
        f"Sinais fechados: {m['trades']} ({m['wins']} win · {m['breakevens']} be · {m['losses']} stop)\n\n"
        f"Taxa de Acerto: *{m['taxa_acerto_pct']}%* (referência: {REFERENCIA['taxa_acerto_pct']}%)\n"
        f"Profit Factor: *{pf_texto}* (referência: {REFERENCIA['profit_factor']})\n"
        f"Expectância: *{m['expectativa_r']}R*/trade\n"
        f"Resultado do período: *{m['resultado_total_r']}R*\n\n"
        f"{'Dentro do esperado pelo backtest.' if selo == '✅' else 'Abaixo da referência do backtest — vale acompanhar de perto.'}"
    )


def montar_mensagem_mensal(ano: int, mes: int, m: dict) -> str:
    nomes_mes = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                 "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
    selo = avaliar_convergencia(m)

    melhor = m["melhor_trade"]
    texto_melhor = ""
    if melhor is not None:
        texto_melhor = (
            f"\n🏆 *Melhor sinal do mês:* {melhor['moeda']} {melhor['direcao']} "
            f"(+{melhor['resultado_r']:.2f}R)\n"
        )

    return (
        f"📅 *RELATÓRIO MENSAL — {nomes_mes[mes]}/{ano}* {selo}\n\n"
        f"Sinais fechados no mês: {m['trades']} ({m['wins']} win · {m['breakevens']} be · {m['losses']} stop)\n\n"
        f"Taxa de Acerto: *{m['taxa_acerto_pct']}%*\n"
        f"Profit Factor: *{m['profit_factor']}*\n"
        f"Expectância: *{m['expectativa_r']}R*/trade\n"
        f"Resultado Total do Mês: *{m['resultado_total_r']}R*\n"
        f"{texto_melhor}\n"
        f"Comparado à referência validada em backtest (Acerto {REFERENCIA['taxa_acerto_pct']}% · "
        f"PF {REFERENCIA['profit_factor']}), este mês ficou "
        f"{'dentro do esperado ✅' if selo == '✅' else 'abaixo do esperado ⚠️'}.\n\n"
        f"⚠️ _Resultados passados não garantem resultados futuros. Gerencie seu risco._"
    )


def enviar_relatorio_com_grafico(texto: str, janela: pd.DataFrame, titulo_grafico: str, legenda_curta: str):
    """Envia o texto do relatório e, logo em seguida, o gráfico da curva de
    resultado do período — o texto traz os números exatos, o gráfico dá a
    leitura visual imediata de "ganhou ou perdeu"."""
    enviar_mensagem_telegram(texto)
    try:
        caminho_grafico = gerar_grafico_periodo(janela, titulo_grafico)
        enviar_foto_telegram(caminho_grafico, legenda_curta)
        Path(caminho_grafico).unlink(missing_ok=True)  # limpa o arquivo temporário
    except Exception as e:
        log.warning(f"Não foi possível gerar/enviar o gráfico ({titulo_grafico}): {e}")


def main():
    estado = carregar_estado()
    df = registro.carregar_sinais()

    if df.empty:
        log.info("Nenhum sinal registrado ainda, nada para reportar.")
        return

    agora = datetime.now(timezone.utc)

    # --- Relatório de 3 dias ---
    if dias_desde(estado["ultimo_3d"]) >= INTERVALO_3D_DIAS:
        janela = obter_janela_periodo(df, INTERVALO_3D_DIAS)
        m = _resumo(janela)
        if m:
            texto = montar_mensagem_periodo("Relatório de 3 dias", INTERVALO_3D_DIAS, m)
            enviar_relatorio_com_grafico(texto, janela, "Últimos 3 dias", f"📊 Resultado: {m['resultado_total_r']:+.1f}R")
            log.info("Relatório de 3 dias enviado.")
        estado["ultimo_3d"] = agora.isoformat()

    # --- Relatório semanal (7 dias) ---
    if dias_desde(estado["ultimo_7d"]) >= INTERVALO_7D_DIAS:
        janela = obter_janela_periodo(df, INTERVALO_7D_DIAS)
        m = _resumo(janela)
        if m:
            texto = montar_mensagem_periodo("Relatório Semanal", INTERVALO_7D_DIAS, m)
            enviar_relatorio_com_grafico(texto, janela, "Últimos 7 dias", f"📊 Resultado: {m['resultado_total_r']:+.1f}R")
            log.info("Relatório semanal enviado.")
        estado["ultimo_7d"] = agora.isoformat()

    # --- Relatório mensal (só no dia 1, resumindo o mês ANTERIOR que fechou) ---
    mes_anterior = agora.month - 1 or 12
    ano_mes_anterior = agora.year - 1 if agora.month == 1 else agora.year
    chave_mes = f"{ano_mes_anterior}-{mes_anterior:02d}"

    if agora.day == 1 and estado["ultimo_mensal"] != chave_mes:
        janela = obter_janela_mes(df, ano_mes_anterior, mes_anterior)
        m = _resumo(janela)
        if m:
            texto = montar_mensagem_mensal(ano_mes_anterior, mes_anterior, m)
            nomes_mes = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
            titulo_grafico = f"{nomes_mes[mes_anterior]}/{ano_mes_anterior}"
            enviar_relatorio_com_grafico(texto, janela, titulo_grafico, f"📅 Resultado do mês: {m['resultado_total_r']:+.1f}R")
            log.info(f"Relatório mensal de {chave_mes} enviado.")
        estado["ultimo_mensal"] = chave_mes

    salvar_estado(estado)


if __name__ == "__main__":
    main()
