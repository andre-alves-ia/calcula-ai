import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ia  # noqa: E402


class ErroAPI(Exception):
    """Imita os erros do google-genai, que trazem o status HTTP em .code."""

    def __init__(self, code, mensagem=""):
        super().__init__(f"{code} {mensagem}")
        self.code = code


def roteiro(respostas):
    """Chamada falsa: para cada modelo, devolve (ou lança) as respostas na ordem."""
    chamadas = []

    def chamar(modelo):
        chamadas.append(modelo)
        r = respostas[modelo].pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    return chamar, chamadas


def gerar(chamar, modelos, esperas=None):
    dormir = (lambda s: esperas.append(s)) if esperas is not None else (lambda s: None)
    return ia.gerar_com_reserva(chamar, modelos, tentativas=2, espera=3, dormir=dormir)


def test_responde_de_primeira():
    chamar, chamadas = roteiro({"a": ["ok"]})
    assert gerar(chamar, ["a", "b"]) == ("ok", "a")
    assert chamadas == ["a"]


def test_repete_erro_temporario_no_mesmo_modelo():
    esperas = []
    chamar, chamadas = roteiro({"a": [ErroAPI(503), "ok"]})
    assert gerar(chamar, ["a", "b"], esperas) == ("ok", "a")
    assert chamadas == ["a", "a"] and esperas == [3]


def test_usa_modelo_reserva_quando_principal_continua_sobrecarregado():
    chamar, chamadas = roteiro({"a": [ErroAPI(503), ErroAPI(503)], "b": ["ok do b"]})
    assert gerar(chamar, ["a", "b"]) == ("ok do b", "b")
    assert chamadas == ["a", "a", "b"]


def test_modelo_inexistente_pula_sem_repetir():
    chamar, chamadas = roteiro({"a": [ErroAPI(404, "NOT_FOUND")], "b": ["ok"]})
    assert gerar(chamar, ["a", "b"]) == ("ok", "b")
    assert chamadas == ["a", "b"]


def test_chave_invalida_desiste_na_hora():
    chamar, chamadas = roteiro({"a": [ErroAPI(400, "API key not valid")], "b": ["ok"]})
    with pytest.raises(ErroAPI):
        gerar(chamar, ["a", "b"])
    assert chamadas == ["a"]


def test_tudo_falhando_relanca_o_ultimo_erro():
    chamar, _ = roteiro({"a": [ErroAPI(503), ErroAPI(503)], "b": [ErroAPI(404, "do b")]})
    with pytest.raises(ErroAPI, match="do b"):
        gerar(chamar, ["a", "b"])


def test_nao_repete_modelo_duplicado():
    chamar, chamadas = roteiro({"a": [ErroAPI(404)], "b": ["ok"]})
    gerar(chamar, ["a", "a", "b"])
    assert chamadas == ["a", "b"]


def test_rodape_das_regras_depende_da_chave():
    contexto = {
        "resumo_periodo": {"corridas": 1, "bruto": 10.0, "custo": 1.0, "percentual_custo": 10.0,
                           "liquido_hora": 20.0, "liquido_km": 2.0},
        "meta_liquido_por_hora": 25.0,
        "valor_minimo_por_km_para_bater_meta": 2.5,
    }
    assert "Configure a chave" in ia.recomendacoes_regras(contexto)
    assert "não respondeu agora" in ia.recomendacoes_regras(contexto, ia_configurada=True)
