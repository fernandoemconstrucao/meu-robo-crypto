"""
Gerador de Relatório Visual
==============================
Lê o CSV de sinais (registro.py) e gera um dashboard HTML autocontido
(gráficos via Chart.js) com taxa de acerto, profit factor, curva de
resultado acumulado, desempenho por moeda e a tabela de cada trade.

COMO RODAR:
    python gerar_relatorio.py

Gera o arquivo relatorio.html — é só abrir no navegador.
"""

import json
import webbrowser
from pathlib import Path

import pandas as pd

import registro
from formatacao import formatar_preco_dinamico


def calcular_metricas(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    ganhos = df[df["resultado_r"] > 0]
    perdas = df[df["resultado_r"] < 0]
    soma_ganhos = ganhos["resultado_r"].sum()
    soma_perdas = abs(perdas["resultado_r"].sum())
    return {
        "total_trades": len(df),
        "wins": len(df[df["status"] == "WIN_ALVO2"]),
        "breakevens": len(df[df["status"] == "BREAKEVEN"]),
        "losses": len(df[df["status"] == "STOP"]),
        "taxa_acerto_pct": round(len(ganhos) / len(df) * 100, 1),
        "profit_factor": round(soma_ganhos / soma_perdas, 2) if soma_perdas > 0 else None,
        "expectativa_r": round(df["resultado_r"].mean(), 2),
        "resultado_total_r": round(df["resultado_r"].sum(), 2),
    }


def gerar_html(df_fechados: pd.DataFrame, metricas: dict) -> str:
    df_fechados = df_fechados.sort_values("datetime_sinal").reset_index(drop=True)
    df_fechados["r_acumulado"] = df_fechados["resultado_r"].cumsum()

    labels_equity = [d.strftime("%d/%m %H:%M") for d in df_fechados["datetime_sinal"]]
    valores_equity = df_fechados["r_acumulado"].round(2).tolist()

    por_moeda = df_fechados.groupby("moeda").agg(
        trades=("resultado_r", "count"),
        wins=("resultado_r", lambda s: (s > 0).sum()),
        resultado_r=("resultado_r", "sum"),
    ).reset_index()
    por_moeda["taxa_acerto"] = (por_moeda["wins"] / por_moeda["trades"] * 100).round(1)

    tabela_trades = df_fechados[[
        "datetime_sinal", "moeda", "direcao", "entrada", "status", "resultado_r"
    ]].copy()
    tabela_trades["datetime_sinal"] = tabela_trades["datetime_sinal"].dt.strftime("%d/%m/%Y %H:%M")
    linhas_tabela = tabela_trades.to_dict("records")

    html = f"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<title>Relatório de Sinais - Desempenho</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #0f1117; color: #e6e6e6; margin: 0; padding: 24px; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .subtitulo {{ color: #9aa0a6; margin-bottom: 24px; font-size: 13px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 28px; }}
  .card {{ background: #1a1d29; border-radius: 10px; padding: 16px; border: 1px solid #2a2e3d; }}
  .card .valor {{ font-size: 24px; font-weight: 700; }}
  .card .rotulo {{ font-size: 12px; color: #9aa0a6; margin-top: 4px; }}
  .positivo {{ color: #4ade80; }}
  .negativo {{ color: #f87171; }}
  .grafico-container {{ background: #1a1d29; border-radius: 10px; padding: 16px; margin-bottom: 20px; border: 1px solid #2a2e3d; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #2a2e3d; }}
  th {{ color: #9aa0a6; font-weight: 600; }}
  .badge {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .badge-win {{ background: #14532d; color: #4ade80; }}
  .badge-loss {{ background: #450a0a; color: #f87171; }}
  .badge-be {{ background: #422006; color: #fbbf24; }}
  @media (max-width: 800px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
  <h1>📊 Relatório de Desempenho dos Sinais</h1>
  <div class="subtitulo">{metricas.get('total_trades', 0)} trades fechados analisados</div>

  <div class="cards">
    <div class="card"><div class="valor">{metricas.get('taxa_acerto_pct', 0)}%</div><div class="rotulo">Taxa de Acerto</div></div>
    <div class="card"><div class="valor {'positivo' if (metricas.get('profit_factor') or 0) >= 1 else 'negativo'}">{metricas.get('profit_factor', 'N/A')}</div><div class="rotulo">Profit Factor</div></div>
    <div class="card"><div class="valor {'positivo' if (metricas.get('expectativa_r') or 0) >= 0 else 'negativo'}">{metricas.get('expectativa_r', 0)}R</div><div class="rotulo">Expectância / Trade</div></div>
    <div class="card"><div class="valor {'positivo' if (metricas.get('resultado_total_r') or 0) >= 0 else 'negativo'}">{metricas.get('resultado_total_r', 0)}R</div><div class="rotulo">Resultado Total</div></div>
    <div class="card"><div class="valor positivo">{metricas.get('wins', 0)}</div><div class="rotulo">Wins (Alvo 2)</div></div>
    <div class="card"><div class="valor negativo">{metricas.get('losses', 0)}</div><div class="rotulo">Losses (Stop)</div></div>
  </div>

  <div class="grafico-container">
    <h3>Curva de Resultado Acumulado (em R)</h3>
    <canvas id="equityChart" height="80"></canvas>
  </div>

  <div class="grid-2">
    <div class="grafico-container">
      <h3>Resultado por Moeda</h3>
      <canvas id="moedaChart"></canvas>
    </div>
    <div class="grafico-container">
      <h3>Distribuição de Resultados</h3>
      <canvas id="statusChart"></canvas>
    </div>
  </div>

  <div class="grafico-container">
    <h3>Histórico de Trades</h3>
    <table>
      <thead><tr><th>Data</th><th>Ativo</th><th>Direção</th><th>Entrada</th><th>Resultado</th><th>R</th></tr></thead>
      <tbody>
        {"".join(f'''<tr>
          <td>{l["datetime_sinal"]}</td>
          <td>{l["moeda"]}</td>
          <td>{l["direcao"]}</td>
          <td>{formatar_preco_dinamico(l["entrada"])}</td>
          <td><span class="badge {"badge-win" if l["status"]=="WIN_ALVO2" else "badge-loss" if l["status"]=="STOP" else "badge-be"}">{l["status"]}</span></td>
          <td>{l["resultado_r"]:.2f}R</td>
        </tr>''' for l in linhas_tabela)}
      </tbody>
    </table>
  </div>

<script>
new Chart(document.getElementById('equityChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(labels_equity)},
    datasets: [{{
      label: 'R Acumulado', data: {json.dumps(valores_equity)},
      borderColor: '#60a5fa', backgroundColor: 'rgba(96,165,250,0.1)',
      fill: true, tension: 0.2, pointRadius: 2,
    }}]
  }},
  options: {{ scales: {{ y: {{ grid: {{ color: '#2a2e3d' }} }}, x: {{ grid: {{ display: false }} }} }},
    plugins: {{ legend: {{ display: false }} }} }}
}});

new Chart(document.getElementById('moedaChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(por_moeda["moeda"].tolist())},
    datasets: [{{
      label: 'Resultado (R)', data: {json.dumps(por_moeda["resultado_r"].round(2).tolist())},
      backgroundColor: {json.dumps(["#4ade80" if v >= 0 else "#f87171" for v in por_moeda["resultado_r"]])},
    }}]
  }},
  options: {{ scales: {{ y: {{ grid: {{ color: '#2a2e3d' }} }}, x: {{ grid: {{ display: false }} }} }},
    plugins: {{ legend: {{ display: false }} }} }}
}});

new Chart(document.getElementById('statusChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['Win (Alvo 2)', 'Breakeven', 'Loss (Stop)'],
    datasets: [{{
      data: [{metricas.get('wins', 0)}, {metricas.get('breakevens', 0)}, {metricas.get('losses', 0)}],
      backgroundColor: ['#4ade80', '#fbbf24', '#f87171'],
    }}]
  }},
  options: {{ plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#e6e6e6' }} }} }} }}
}});
</script>
</body>
</html>"""
    return html


def main():
    df = registro.carregar_sinais()
    df_fechados = df[df["resultado_r"].notna()].copy()
    df_fechados["resultado_r"] = df_fechados["resultado_r"].astype(float)

    if df_fechados.empty:
        print("Nenhum trade fechado ainda para gerar relatório. Rode o monitor ou a "
              "recuperação do histórico do Telegram primeiro.")
        return

    metricas = calcular_metricas(df_fechados)
    html = gerar_html(df_fechados, metricas)

    caminho = Path("relatorio.html")
    caminho.write_text(html, encoding="utf-8")
    print(f"Relatório gerado em {caminho.resolve()}")

    try:
        webbrowser.open(f"file://{caminho.resolve()}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
