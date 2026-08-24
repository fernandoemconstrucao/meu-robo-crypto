"""
Formatação inteligente de preços.
====================================
Problema que resolve: moedas como SHIB, PEPE têm preços tipo
0.00000523 — um formato fixo de "6 casas decimais" corta ou distorce
esses números (viraria "0.000005", perdendo os dígitos que realmente
importam). Aqui a quantidade de casas decimais é calculada dinamicamente
conforme a ordem de grandeza do preço, então nunca perde precisão.
"""

import math


def formatar_preco_dinamico(preco: float, casas_significativas: int = 4) -> str:
    """
    Formata um preço com casas decimais suficientes para não perder
    precisão, seja ele um valor "grande" (BTC ~61250.50) ou muito
    pequeno (SHIB ~0.00000523).

    casas_significativas: quantos dígitos não-zero você quer ver após
    os zeros iniciais, para números menores que 1.
    """
    if preco is None:
        return "N/A"
    preco = float(preco)
    if preco == 0:
        return "0"

    if abs(preco) >= 1:
        # Números "normais": 6 casas decimais já é mais que suficiente
        # e evita ruído visual desnecessário.
        casas = 6
    else:
        # Descobre quantos zeros existem logo após a vírgula, antes do
        # primeiro dígito significativo, e soma as casas que queremos ver.
        zeros_apos_virgula = -int(math.floor(math.log10(abs(preco)))) - 1
        casas = zeros_apos_virgula + casas_significativas

    return f"{preco:.{casas}f}"
