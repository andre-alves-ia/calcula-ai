"""Regras de cálculo do Calcula AI.

Toda a matemática fica aqui, de forma determinística e testável.
O LLM nunca faz conta: ele só recebe estes números prontos e os interpreta.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

COLUNAS = ["data", "hora", "app", "regiao", "valor", "km", "minutos"]

FAIXAS_HORARIO = [
    (0, 6, "Madrugada (0h–6h)"),
    (6, 11, "Manhã (6h–11h)"),
    (11, 14, "Almoço (11h–14h)"),
    (14, 18, "Tarde (14h–18h)"),
    (18, 23, "Jantar (18h–23h)"),
    (23, 24, "Noite (23h–24h)"),
]
ORDEM_FAIXAS = [nome for _, _, nome in FAIXAS_HORARIO]
DIAS_SEMANA = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


@dataclass(frozen=True)
class CustosMoto:
    """Custos variáveis da moto, por km rodado."""

    consumo_km_l: float  # quantos km a moto faz com 1 litro
    preco_litro: float  # R$ por litro de gasolina
    manutencao_por_km: float  # óleo, pneu, relação, freio... diluídos por km

    @property
    def combustivel_por_km(self) -> float:
        return self.preco_litro / self.consumo_km_l

    @property
    def custo_por_km(self) -> float:
        return self.combustivel_por_km + self.manutencao_por_km


def para_numero(serie: pd.Series) -> pd.Series:
    """Aceita '18,50', 'R$ 18,50' ou 18.5."""
    if serie.dtype == object or pd.api.types.is_string_dtype(serie):
        s = serie.astype(str).str.replace("R$", "", regex=False).str.strip()
        formato_br = s.str.contains(",", regex=False)  # 1.234,56 -> 1234.56
        s = s.where(~formato_br, s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
        serie = s
    return pd.to_numeric(serie, errors="coerce")


def _para_data(serie: pd.Series) -> pd.Series:
    """Aceita 2026-11-10 (ISO) e 10/11/2026 (padrão brasileiro)."""
    texto = serie.astype(str).str.strip()
    iso = pd.to_datetime(texto, errors="coerce", format="%Y-%m-%d")
    br = pd.to_datetime(texto, errors="coerce", format="%d/%m/%Y")
    return iso.fillna(br)


def _hora_inteira(serie: pd.Series) -> pd.Series:
    """'19:40' -> 19. Também aceita 19 ou '19'."""
    texto = serie.astype(str).str.strip().str.split(":").str[0]
    return pd.to_numeric(texto, errors="coerce")


def validar(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Normaliza tipos e descarta linhas inválidas, explicando o motivo."""
    erros: list[str] = []
    faltando = [c for c in COLUNAS if c not in df.columns]
    if faltando:
        return pd.DataFrame(columns=COLUNAS), [f"Colunas faltando: {', '.join(faltando)}"]

    df = df[COLUNAS].copy()
    df["data"] = _para_data(df["data"])
    df["hora_int"] = _hora_inteira(df["hora"])
    for col in ["valor", "km", "minutos"]:
        df[col] = para_numero(df[col])
    df["app"] = df["app"].fillna("Outro").astype(str).str.strip()
    df["regiao"] = df["regiao"].fillna("Não informada").astype(str).str.strip()

    invalida = (
        df["data"].isna()
        | df["hora_int"].isna()
        | ~df["hora_int"].between(0, 23)
        | df["valor"].isna()
        | (df["valor"] <= 0)
        | df["km"].isna()
        | (df["km"] <= 0)
        | df["minutos"].isna()
        | (df["minutos"] <= 0)
    )
    if invalida.any():
        erros.append(
            f"{int(invalida.sum())} corrida(s) ignorada(s) por data, hora, valor, km ou minutos inválidos."
        )
    return df[~invalida].reset_index(drop=True), erros


def faixa_do_horario(hora: int) -> str:
    for inicio, fim, nome in FAIXAS_HORARIO:
        if inicio <= hora < fim:
            return nome
    raise ValueError(f"Hora fora do intervalo 0–23: {hora}")


def enriquecer(df: pd.DataFrame, custos: CustosMoto) -> pd.DataFrame:
    """Adiciona custo, ganho líquido e as dimensões de análise a cada corrida."""
    df = df.copy()
    df["custo"] = df["km"] * custos.custo_por_km
    df["liquido"] = df["valor"] - df["custo"]
    df["horas"] = df["minutos"] / 60
    df["liquido_hora"] = df["liquido"] / df["horas"]
    df["liquido_km"] = df["liquido"] / df["km"]
    df["faixa"] = pd.Categorical(
        df["hora_int"].astype(int).map(faixa_do_horario), categories=ORDEM_FAIXAS, ordered=True
    )
    df["dia_semana"] = pd.Categorical(
        df["data"].dt.day_of_week.map(lambda i: DIAS_SEMANA[i]), categories=DIAS_SEMANA, ordered=True
    )
    return df


def resumo_geral(df: pd.DataFrame) -> dict:
    """Totais do período. Médias por hora/km são ponderadas (soma ÷ soma)."""
    horas = df["horas"].sum()
    km = df["km"].sum()
    bruto = df["valor"].sum()
    liquido = df["liquido"].sum()
    resumo = {
        "corridas": int(len(df)),
        "dias_trabalhados": int(df["data"].dt.date.nunique()),
        "bruto": round(bruto, 2),
        "custo": round(df["custo"].sum(), 2),
        "liquido": round(liquido, 2),
        "horas": round(horas, 2),
        "km": round(km, 1),
        "bruto_hora": round(bruto / horas, 2) if horas else 0.0,
        "liquido_hora": round(liquido / horas, 2) if horas else 0.0,
        "liquido_km": round(liquido / km, 2) if km else 0.0,
        "percentual_custo": round(100 * df["custo"].sum() / bruto, 1) if bruto else 0.0,
    }
    return {k: (v if isinstance(v, int) else float(v)) for k, v in resumo.items()}


def liquido_por_hora_logado(liquido: float, horas_logado: float) -> float:
    """Ganho real por hora contando o tempo parado esperando pedido."""
    return round(liquido / horas_logado, 2) if horas_logado > 0 else 0.0


def agrupar(df: pd.DataFrame, coluna: str, minimo_corridas: int = 1) -> pd.DataFrame:
    """Desempenho por horário, região, app ou dia, ordenado por R$ líquido/hora."""
    g = df.groupby(coluna, observed=True).agg(
        corridas=("valor", "size"),
        bruto=("valor", "sum"),
        liquido=("liquido", "sum"),
        horas=("horas", "sum"),
        km=("km", "sum"),
    )
    g["liquido_hora"] = g["liquido"] / g["horas"]
    g["liquido_km"] = g["liquido"] / g["km"]
    g = g[g["corridas"] >= minimo_corridas]
    return g.sort_values("liquido_hora", ascending=False).round(2)


def valor_minimo_corrida(km: float, minutos: float, custos: CustosMoto, meta_hora: float) -> float:
    """Menor valor que cobre o custo da moto E paga a meta de R$/hora pelo tempo gasto."""
    return round(km * custos.custo_por_km + meta_hora * minutos / 60, 2)


def valor_minimo_por_km(custos: CustosMoto, meta_hora: float, km_por_hora: float) -> float:
    """Regra de bolso: R$/km mínimo, usando o ritmo médio real do entregador (km rodados por hora)."""
    return round(custos.custo_por_km + meta_hora / km_por_hora, 2)


def avaliar_oferta(valor: float, km: float, minutos: float, custos: CustosMoto, meta_hora: float) -> dict:
    custo = km * custos.custo_por_km
    liquido = valor - custo
    liquido_hora = liquido / (minutos / 60)
    minimo = valor_minimo_corrida(km, minutos, custos, meta_hora)
    return {
        "custo": round(custo, 2),
        "liquido": round(liquido, 2),
        "liquido_hora": round(liquido_hora, 2),
        "valor_minimo": minimo,
        "vale_a_pena": valor >= minimo,
    }


def corridas_abaixo_da_meta(df: pd.DataFrame, meta_hora: float) -> pd.DataFrame:
    return df[df["liquido_hora"] < meta_hora]
