"""Camada de IA do Calcula AI.

O LLM recebe apenas números já calculados (calc.py) e devolve recomendações
em linguagem simples. Se não houver chave de API, usa um gerador por regras,
para o app nunca ficar sem resposta.
"""

from __future__ import annotations

import json

import pandas as pd

from calc import CustosMoto

MODELO_PADRAO = "gemini-3.8-flash"

INSTRUCOES = """Você é um consultor financeiro para entregadores de moto de aplicativo (iFood, 99 etc.) no Brasil.
Você recebe métricas JÁ CALCULADAS das corridas reais de um entregador. Não refaça contas e não invente números:
cite apenas valores que estão nos dados.

Escreva em português do Brasil, linguagem direta de quem anda de moto, sem jargão. Estrutura:
1. **Diagnóstico** (2–3 frases): quanto ele ganha de verdade por hora e quanto o custo da moto come do bruto.
2. **Melhores horários e regiões**: onde e quando o R$ líquido/hora é maior. Compare com o pior caso.
3. **O que evitar**: horários, regiões ou tipo de corrida que puxam a média para baixo.
4. **Regra prática para aceitar corridas**: use o valor mínimo por km informado.
5. **Plano para a próxima semana**: 3 ações concretas.

Atenção: "liquido_hora" considera só o tempo em corrida. Se existir "liquido_por_hora_logado", esse é o ganho
real (inclui o tempo parado esperando pedido) e deve ser o número principal do diagnóstico.
Se algum grupo tiver poucas corridas (menos de 5), avise que a conclusão ainda é fraca.
Máximo de 300 palavras."""


def _tabela(df: pd.DataFrame) -> list[dict]:
    cols = ["corridas", "liquido_hora", "liquido_km", "liquido"]
    return (
        df[cols]
        .reset_index()
        .rename(columns={"liquido_hora": "liquido_por_hora", "liquido_km": "liquido_por_km", "liquido": "liquido_total"})
        .astype({"corridas": int})
        .to_dict(orient="records")
    )


def montar_contexto(
    resumo: dict,
    grupos: dict[str, pd.DataFrame],
    custos: CustosMoto,
    meta_hora: float,
    minimo_por_km_tipico: float,
    horas_logado: float = 0.0,
) -> dict:
    resumo = dict(resumo)
    if horas_logado > 0:
        resumo["horas_logado_no_app"] = horas_logado
        resumo["liquido_por_hora_logado"] = round(resumo["liquido"] / horas_logado, 2)
        resumo["percentual_tempo_parado"] = round(100 * (1 - resumo["horas"] / horas_logado), 1)
    return {
        "resumo_periodo": resumo,
        "custos_moto": {
            "combustivel_por_km": round(custos.combustivel_por_km, 3),
            "manutencao_por_km": custos.manutencao_por_km,
            "custo_total_por_km": round(custos.custo_por_km, 3),
        },
        "meta_liquido_por_hora": meta_hora,
        "valor_minimo_por_km_para_bater_meta": minimo_por_km_tipico,
        **{f"por_{nome}": _tabela(df) for nome, df in grupos.items()},
    }


def recomendacoes_llm(contexto: dict, api_key: str, modelo: str = MODELO_PADRAO) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resposta = client.models.generate_content(
        model=modelo,
        contents="Dados do entregador (JSON):\n" + json.dumps(contexto, ensure_ascii=False, default=str),
        config=types.GenerateContentConfig(system_instruction=INSTRUCOES, temperature=0.3),
    )
    return resposta.text


def _brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def recomendacoes_regras(contexto: dict) -> str:
    """Plano B sem IA: mesmas conclusões básicas, montadas por regras."""
    r = contexto["resumo_periodo"]
    meta = contexto["meta_liquido_por_hora"]
    linhas = [
        "**Diagnóstico**",
        f"Em {r['corridas']} corridas você faturou {_brl(r['bruto'])} brutos, mas a moto custou "
        f"{_brl(r['custo'])} ({str(r['percentual_custo']).replace('.', ',')}% do bruto). Sobrou "
        f"{_brl(r['liquido_hora'])} por hora em corrida e {_brl(r['liquido_km'])} por km.",
    ]
    if "liquido_por_hora_logado" in r:
        linhas.append(
            f"Contando o tempo parado esperando pedido ({str(r['percentual_tempo_parado']).replace('.', ',')}% do tempo logado), "
            f"o ganho real cai para **{_brl(r['liquido_por_hora_logado'])} por hora**."
        )
    linhas.append("")
    for chave, titulo in [("por_faixa", "horário"), ("por_regiao", "região")]:
        tabela = [t for t in contexto.get(chave, []) if t["corridas"] >= 3]
        if len(tabela) >= 2:
            melhor, pior = tabela[0], tabela[-1]
            nome = next(k for k in melhor if k not in {"corridas", "liquido_por_hora", "liquido_por_km", "liquido_total"})
            linhas += [
                f"**Melhor {titulo}:** {melhor[nome]} — {_brl(melhor['liquido_por_hora'])}/h líquidos. "
                f"**Pior:** {pior[nome]} — {_brl(pior['liquido_por_hora'])}/h.",
                "",
            ]
    linhas += [
        "**Regra para aceitar corrida**",
        f"Para bater sua meta de {_brl(meta)}/h, recuse ofertas abaixo de "
        f"~{_brl(contexto['valor_minimo_por_km_para_bater_meta'])} por km rodado.",
        "",
        "_Recomendações geradas por regras. Configure a chave do Gemini para análise com IA._",
    ]
    return "\n".join(linhas)
