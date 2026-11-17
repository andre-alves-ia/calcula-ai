"""Calcula AI — assistente de ganhos para entregadores de moto.

Rodar localmente:  streamlit run app.py
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import calc
import ia

st.set_page_config(page_title="Calcula AI", page_icon="🛵", layout="wide")

EXEMPLO = Path(__file__).parent / "dados" / "corridas_exemplo.csv"


def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def grafico_barras(tabela: pd.DataFrame, rotulo: str) -> alt.Chart:
    """Barras horizontais do melhor para o pior R$ líquido/hora."""
    dados = tabela.reset_index().rename(columns={tabela.index.name: "grupo"})
    dados["grupo"] = dados["grupo"].astype(str)
    dados["texto"] = dados["liquido_hora"].map(brl)
    base = alt.Chart(dados).encode(
        y=alt.Y("grupo:N", sort="-x", title=None, axis=alt.Axis(labelOverlap=False, labelLimit=220)),
        x=alt.X("liquido_hora:Q", title=rotulo),
    )
    barras = base.mark_bar(cornerRadiusEnd=3).encode(
        tooltip=[alt.Tooltip("grupo:N", title="Grupo"), alt.Tooltip("texto:N", title="Líquido/h"),
                 alt.Tooltip("corridas:Q", title="Corridas")]
    )
    rotulos = base.mark_text(align="left", dx=4).encode(text="texto:N")
    return (barras + rotulos).properties(height=max(140, 40 * len(dados)))


def tabela_reais(df: pd.DataFrame) -> pd.DataFrame:
    """Formata colunas em reais no padrão brasileiro para exibição."""
    out = df.copy()
    for col in ["liquido_hora", "liquido_km", "liquido", "valor"]:
        if col in out:
            out[col] = out[col].map(brl)
    return out


def segredo(nome: str, padrao: str = "") -> str:
    try:
        return st.secrets.get(nome, padrao)
    except Exception:  # sem secrets.toml
        return padrao


# ---------- estado ----------
def tipar(df: pd.DataFrame) -> pd.DataFrame:
    """Padroniza tipos para a tabela editável (texto nas colunas de texto, número nas numéricas)."""
    df = df.reindex(columns=calc.COLUNAS).copy()
    for col in ["data", "hora", "app", "regiao"]:
        df[col] = df[col].astype("string")
    for col in ["valor", "km", "minutos"]:
        df[col] = calc.para_numero(df[col].astype("string")).astype("float64")
    return df.reset_index(drop=True)


if "corridas" not in st.session_state:  # base que alimenta a tabela
    st.session_state.corridas = tipar(pd.DataFrame(columns=calc.COLUNAS))
if "editadas" not in st.session_state:  # o que o usuário vê/editou
    st.session_state.editadas = st.session_state.corridas

# ---------- barra lateral: custos e meta ----------
with st.sidebar:
    st.header("🛵 Sua moto")
    st.caption("Ajuste para a sua realidade. Tudo é calculado a partir daqui.")
    consumo = st.number_input("Consumo (km por litro)", 10.0, 80.0, 35.0, 1.0)
    preco = st.number_input("Gasolina (R$ por litro)", 3.0, 12.0, 6.40, 0.05, format="%.2f")
    manut = st.number_input(
        "Manutenção (R$ por km)",
        0.0, 2.0, 0.12, 0.01, format="%.2f",
        help="Óleo, pneus, relação, freios, revisão… somados e divididos pelos km que duram.",
    )
    custos = calc.CustosMoto(consumo, preco, manut)
    st.metric("Custo real por km rodado", brl(custos.custo_por_km))

    st.header("🎯 Sua meta")
    meta_hora = st.number_input("Quero ganhar líquido por hora (R$)", 5.0, 200.0, 25.0, 1.0)

    horas_logado = st.number_input(
        "Horas logado no app no período (opcional)", 0.0, 2000.0, 0.0, 0.5,
        help="Soma das horas em que você ficou online, inclusive parado esperando pedido. "
             "Sem isso, o R$/hora conta só o tempo dentro das corridas.",
    )

    st.header("🤖 IA")
    chave = segredo("GEMINI_API_KEY") or st.text_input(
        "Chave da API do Gemini (opcional)", type="password",
        help="Sem chave, as recomendações são geradas por regras.",
    )
    modelo = segredo("GEMINI_MODEL", ia.MODELO_PADRAO)
    st.caption("✅ IA ativada" if chave else "Modo sem IA (regras)")

st.title("Calcula AI")
st.write("Descubra quanto você **ganha de verdade** por hora e por km — e em quais horários e regiões vale a pena rodar.")

aba_reg, aba_painel, aba_ia, aba_oferta = st.tabs(
    ["📝 Corridas", "📊 Painel", "💡 Recomendações", "✅ Vale a pena aceitar?"]
)

# ---------- aba: registrar ----------
with aba_reg:
    col_form, col_arquivo = st.columns([2, 1])
    with col_form:
        st.subheader("Registrar corrida")
        with st.form("nova", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            d = c1.date_input("Data", date.today(), format="DD/MM/YYYY")
            h = c2.time_input("Hora em que aceitou", datetime.now().time().replace(second=0, microsecond=0))
            app_nome = c3.selectbox("App", ["iFood", "99", "Rappi", "Outro"])
            c4, c5, c6, c7 = st.columns(4)
            regiao = c4.text_input("Região", placeholder="Ex.: Águas Claras")
            valor = c5.number_input("Valor recebido (R$)", 0.0, 500.0, step=0.5, format="%.2f")
            km = c6.number_input("Km rodados", 0.0, 200.0, step=0.1, format="%.1f",
                                 help="Total: até o restaurante + até o cliente.")
            minutos = c7.number_input("Minutos gastos", 0, 600, step=1,
                                      help="Do aceite até a entrega, contando a espera no restaurante.")
            if st.form_submit_button("Adicionar", type="primary"):
                if valor <= 0 or km <= 0 or minutos <= 0:
                    st.error("Preencha valor, km e minutos.")
                else:
                    nova = pd.DataFrame([{
                        "data": d.isoformat(), "hora": h.strftime("%H:%M"), "app": app_nome,
                        "regiao": regiao.strip() or "Não informada",
                        "valor": valor, "km": km, "minutos": minutos,
                    }])
                    st.session_state.corridas = tipar(pd.concat([st.session_state.editadas, nova], ignore_index=True))
                    st.success("Corrida adicionada.")

    with col_arquivo:
        st.subheader("Importar / exportar")
        enviado = st.file_uploader("Enviar CSV de corridas", type="csv")
        if enviado is not None and st.button("Carregar CSV"):
            st.session_state.corridas = tipar(pd.read_csv(enviado, dtype=str))
            st.rerun()
        if st.button("Usar dados de exemplo"):
            st.session_state.corridas = tipar(pd.read_csv(EXEMPLO, dtype=str))
            st.rerun()
        local_download = st.empty()
        st.caption("Os dados ficam só nesta sessão do navegador. Baixe o CSV para não perder.")

    st.subheader("Suas corridas")
    st.caption("Dá para editar ou apagar linhas direto na tabela.")
    st.session_state.editadas = st.data_editor(
        st.session_state.corridas, num_rows="dynamic", width="stretch",
        column_config={
            "valor": st.column_config.NumberColumn("valor (R$)", format="%.2f", min_value=0),
            "km": st.column_config.NumberColumn(format="%.1f", min_value=0),
            "minutos": st.column_config.NumberColumn(format="%d", min_value=0),
        },
    )
    local_download.download_button(
        "Baixar minhas corridas (CSV)",
        st.session_state.editadas.to_csv(index=False).encode("utf-8"),
        "minhas_corridas.csv", "text/csv",
        disabled=st.session_state.editadas.empty,
    )

# ---------- processamento ----------
dados, avisos = calc.validar(st.session_state.editadas)
for aviso in avisos:
    st.warning(aviso)
tem_dados = not dados.empty
if tem_dados:
    dados = calc.enriquecer(dados, custos)
    resumo = calc.resumo_geral(dados)
    grupos = {
        "faixa": calc.agrupar(dados, "faixa"),
        "regiao": calc.agrupar(dados, "regiao"),
        "app": calc.agrupar(dados, "app"),
        "dia_semana": calc.agrupar(dados, "dia_semana"),
    }
    # ritmo real: usa as horas logado (com tempo parado) quando informadas
    km_por_hora = resumo["km"] / (horas_logado if horas_logado >= resumo["horas"] else resumo["horas"])
    minimo_km = calc.valor_minimo_por_km(custos, meta_hora, km_por_hora)

# ---------- aba: painel ----------
with aba_painel:
    if not tem_dados:
        st.info("Registre corridas ou use os dados de exemplo para ver o painel.")
    else:
        if horas_logado and horas_logado < resumo["horas"]:
            st.warning("As horas logado são menores que o tempo somado das corridas. Confira o valor na barra lateral.")
        m = st.columns(5)
        m[0].metric("Bruto", brl(resumo["bruto"]))
        m[1].metric("Custo da moto", brl(resumo["custo"]),
                    f"-{str(resumo['percentual_custo']).replace('.', ',')}% do bruto", delta_color="inverse")
        m[2].metric("Líquido", brl(resumo["liquido"]))
        if horas_logado > 0:
            real = calc.liquido_por_hora_logado(resumo["liquido"], horas_logado)
            m[3].metric("Líquido/h real", brl(real),
                        f"{real - meta_hora:+.2f} vs meta".replace(".", ","),
                        help=f"Em corrida: {brl(resumo['liquido_hora'])}/h. A diferença é o tempo parado.")
        else:
            m[3].metric("Líquido/h em corrida", brl(resumo["liquido_hora"]),
                        f"{resumo['liquido_hora'] - meta_hora:+.2f} vs meta".replace(".", ","),
                        help="Informe as horas logado na barra lateral para ver o ganho real com o tempo parado.")
        m[4].metric("Líquido por km", brl(resumo["liquido_km"]))
        st.caption(f"{resumo['corridas']} corridas · {resumo['dias_trabalhados']} dias · "
                   f"{resumo['horas']:.1f} h · {resumo['km']:.0f} km · "
                   f"bruto por hora {brl(resumo['bruto_hora'])}")

        st.info(f"**Regra de bolso:** para bater {brl(meta_hora)}/h no seu ritmo médio "
                f"({km_por_hora:.0f} km/h), só aceite corridas que paguem **{brl(minimo_km)} por km** ou mais.")

        titulos = {"faixa": "Por horário", "regiao": "Por região", "app": "Por app", "dia_semana": "Por dia da semana"}
        colunas = st.columns(2)
        for i, (nome, tabela) in enumerate(grupos.items()):
            with colunas[i % 2]:
                st.subheader(titulos[nome])
                st.altair_chart(grafico_barras(tabela, "R$ líquido por hora em corrida"), width="stretch")
                st.dataframe(
                    tabela_reais(tabela[["corridas", "liquido_hora", "liquido_km", "liquido"]]).rename(columns={
                        "corridas": "Corridas", "liquido_hora": "Líquido/h", "liquido_km": "Líquido/km", "liquido": "Líquido total"})
                    .rename_axis({"faixa": "Horário", "regiao": "Região", "app": "App", "dia_semana": "Dia"}[nome]),
                    width="stretch",
                )

        ruins = calc.corridas_abaixo_da_meta(dados, meta_hora)
        with st.expander(f"⚠️ {len(ruins)} corridas pagaram menos que a sua meta por hora"):
            st.dataframe(
                ruins[["data", "hora", "app", "regiao", "valor", "km", "minutos", "liquido", "liquido_hora"]]
                .sort_values("liquido_hora")
                .assign(data=lambda x: x["data"].dt.strftime("%d/%m/%Y"))
                .pipe(tabela_reais),
                width="stretch", hide_index=True,
            )

# ---------- aba: recomendações ----------
with aba_ia:
    if not tem_dados:
        st.info("Registre corridas primeiro.")
    else:
        st.write("A IA recebe só os números já calculados no painel e transforma em um plano prático.")
        if resumo["corridas"] < 20:
            st.caption("Dica: com menos de 20 corridas as conclusões ainda são fracas.")
        if st.button("Gerar recomendações", type="primary"):
            contexto = ia.montar_contexto(resumo, grupos, custos, meta_hora, minimo_km, horas_logado)
            with st.spinner("Analisando suas corridas…"):
                if chave:
                    try:
                        texto = ia.recomendacoes_llm(contexto, chave, modelo)
                    except Exception as erro:  # API fora, chave inválida, cota etc.
                        st.warning(f"Não consegui falar com a IA ({type(erro).__name__}). Usando regras.")
                        texto = ia.recomendacoes_regras(contexto)
                else:
                    texto = ia.recomendacoes_regras(contexto)
            st.markdown(texto)

# ---------- aba: avaliar oferta ----------
with aba_oferta:
    st.write("Chegou uma oferta? Coloque os números e veja se compensa **antes** de aceitar.")
    o1, o2, o3 = st.columns(3)
    ov = o1.number_input("Valor oferecido (R$)", 0.0, 500.0, 9.0, 0.5, format="%.2f")
    ok = o2.number_input("Distância total (km)", 0.1, 200.0, 5.0, 0.1, format="%.1f")
    om = o3.number_input("Tempo estimado (min)", 1, 600, 25, 1)
    av = calc.avaliar_oferta(ov, ok, om, custos, meta_hora)
    r1, r2, r3 = st.columns(3)
    r1.metric("Custo da moto", brl(av["custo"]))
    r2.metric("Sobra pra você", brl(av["liquido"]))
    r3.metric("Equivale a", f"{brl(av['liquido_hora'])}/h")
    if av["vale_a_pena"]:
        st.success(f"✅ Vale a pena. O mínimo para a sua meta seria {brl(av['valor_minimo'])}.")
    else:
        st.error(f"❌ Abaixo da sua meta. Para valer {brl(meta_hora)}/h, essa corrida teria que pagar {brl(av['valor_minimo'])}.")
