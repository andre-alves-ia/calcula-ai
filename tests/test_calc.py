import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calc  # noqa: E402

CUSTOS = calc.CustosMoto(consumo_km_l=40, preco_litro=6.0, manutencao_por_km=0.10)


def corridas(**override):
    base = {
        "data": ["2026-11-10", "10/11/2026"],
        "hora": ["12:30", "19:05"],
        "app": ["iFood", "99"],
        "regiao": ["Guará", "Plano Piloto"],
        "valor": ["15,00", "R$ 20,00"],
        "km": [5, 10],
        "minutos": [30, 60],
    }
    base.update(override)
    return pd.DataFrame(base)


def test_custo_por_km():
    # 6,00 / 40 = 0,15 de combustível + 0,10 de manutenção
    assert CUSTOS.custo_por_km == pytest.approx(0.25)


def test_validar_aceita_formato_brasileiro():
    df, erros = calc.validar(corridas())
    assert erros == []
    assert df["valor"].tolist() == [15.0, 20.0]
    assert df["data"].dt.day.tolist() == [10, 10]


def test_validar_descarta_linhas_invalidas():
    df, erros = calc.validar(corridas(km=[0, 10]))
    assert len(df) == 1
    assert "1 corrida" in erros[0]


def test_validar_aponta_colunas_faltando():
    df, erros = calc.validar(pd.DataFrame({"valor": [10]}))
    assert df.empty and "Colunas faltando" in erros[0]


def test_liquido_por_hora_e_ponderado():
    df, _ = calc.validar(corridas())
    df = calc.enriquecer(df, CUSTOS)
    r = calc.resumo_geral(df)
    # líquido = (15 - 1,25) + (20 - 2,50) = 31,25 em 1,5 h
    assert r["liquido"] == pytest.approx(31.25)
    assert r["liquido_hora"] == pytest.approx(31.25 / 1.5, abs=0.01)
    # e NÃO a média simples das taxas por corrida (27,5 e 17,5 -> 22,5)
    assert r["liquido_hora"] != pytest.approx(22.5)


def test_faixas_de_horario():
    assert calc.faixa_do_horario(0) == "Madrugada (0h–6h)"
    assert calc.faixa_do_horario(12) == "Almoço (11h–14h)"
    assert calc.faixa_do_horario(23) == "Noite (23h–24h)"
    with pytest.raises(ValueError):
        calc.faixa_do_horario(24)


def test_agrupar_ordena_pelo_melhor():
    df, _ = calc.validar(corridas())
    g = calc.agrupar(calc.enriquecer(df, CUSTOS), "regiao")
    assert g.index[0] == "Guará"


def test_avaliar_oferta():
    # 5 km, 30 min, meta 20/h -> mínimo = 5*0,25 + 10 = 11,25
    ruim = calc.avaliar_oferta(10, 5, 30, CUSTOS, meta_hora=20)
    boa = calc.avaliar_oferta(12, 5, 30, CUSTOS, meta_hora=20)
    assert ruim["valor_minimo"] == pytest.approx(11.25)
    assert not ruim["vale_a_pena"] and boa["vale_a_pena"]


def test_dados_de_exemplo_sao_validos():
    bruto = pd.read_csv(Path(__file__).resolve().parents[1] / "dados" / "corridas_exemplo.csv", dtype=str)
    df, erros = calc.validar(bruto)
    assert erros == [] and len(df) == len(bruto)


def test_hora_logado_inclui_tempo_parado():
    # 100 líquidos em 2 h rodando, mas 4 h logado -> 25/h reais
    assert calc.liquido_por_hora_logado(100, 4) == 25.0
    assert calc.liquido_por_hora_logado(100, 0) == 0.0
