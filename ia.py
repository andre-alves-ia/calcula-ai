"""Camada de IA do Calcula AI.

O LLM recebe apenas números já calculados (calc.py) e devolve recomendações
em linguagem simples. Se não houver chave de API, usa um gerador por regras,
para o app nunca ficar sem resposta.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import pandas as pd

from calc import CustosMoto

MODELO_PADRAO = "gemini-3.8-flash"
# tentados em ordem quando o modelo principal está sobrecarregado ou indisponível
MODELOS_RESERVA = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
TENTATIVAS_POR_MODELO = 2
ESPERA_SEGUNDOS = 3.0
ERROS_TEMPORARIOS = {429, 500, 503, 504}

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


def _codigo_http(erro: Exception) -> int | None:
    codigo = getattr(erro, "code", None)
    return codigo if isinstance(codigo, int) else None


def _chave_invalida(erro: Exception) -> bool:
    """Erro de autenticação: trocar de modelo ou repetir não resolve."""
    codigo = _codigo_http(erro)
    return codigo in (401, 403) or (codigo == 400 and "API key" in str(erro).replace("API_KEY", "API key"))


def _temporario(erro: Exception) -> bool:
    """Sobrecarga, cota por minuto ou prazo estourado: vale tentar de novo."""
    return _codigo_http(erro) in ERROS_TEMPORARIOS


def gerar_com_reserva(
    chamar: Callable[[str], str],
    modelos: list[str],
    tentativas: int = TENTATIVAS_POR_MODELO,
    espera: float = ESPERA_SEGUNDOS,
    dormir: Callable[[float], None] = time.sleep,
) -> tuple[str, str]:
    """Tenta cada modelo em ordem, repetindo os erros temporários. Devolve (texto, modelo usado).

    Modelo inexistente ou outro erro do modelo: pula para o próximo.
    Chave inválida: desiste na hora. Se nenhum modelo responder, relança o último erro.
    """
    ultimo: Exception | None = None
    for modelo in dict.fromkeys(modelos):  # sem repetidos, mantendo a ordem
        for tentativa in range(1, tentativas + 1):
            try:
                return chamar(modelo), modelo
            except Exception as erro:  # noqa: BLE001 - qualquer falha da API leva ao plano B
                if _chave_invalida(erro):
                    raise
                ultimo = erro
                if not _temporario(erro):
                    break
                if tentativa < tentativas:
                    dormir(espera * tentativa)
    assert ultimo is not None, "lista de modelos vazia"
    raise ultimo


def recomendacoes_llm(contexto: dict, api_key: str, modelo: str = MODELO_PADRAO) -> tuple[str, str]:
    """Pede o plano ao Gemini. Devolve (texto, modelo que respondeu)."""
    from google import genai
    from google.genai import types

    # limite por chamada (em ms) para o app nunca ficar travado esperando a IA
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=45_000))
    dados = "Dados do entregador (JSON):\n" + json.dumps(contexto, ensure_ascii=False, default=str)

    def chamar(nome: str) -> str:
        config = types.GenerateContentConfig(system_instruction=INSTRUCOES, temperature=0.3)
        if nome.startswith("gemini-3"):
            # texto curto a partir de números prontos: raciocínio longo só deixa a resposta lenta
            config.thinking_config = types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
        texto = client.models.generate_content(model=nome, contents=dados, config=config).text
        if not texto:
            raise RuntimeError(f"{nome} devolveu resposta vazia")
        return texto

    return gerar_com_reserva(chamar, [modelo, *MODELOS_RESERVA])


def _brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def recomendacoes_regras(contexto: dict, ia_configurada: bool = False) -> str:
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
        "_Recomendações geradas por regras. A IA não respondeu agora; tente de novo em alguns minutos._"
        if ia_configurada
        else "_Recomendações geradas por regras. Configure a chave do Gemini para análise com IA._",
    ]
    return "\n".join(linhas)
