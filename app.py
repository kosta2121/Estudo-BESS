from __future__ import annotations

import os
import tempfile
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import altair as alt
import holidays
import pandas as pd
import streamlit as st

from load_profile import LoadProfile, read_eredes_file, infer_year
from pricing import (
    PeriodTariff,
    PowerTariffPonta,
    build_tariff_series,
    compute_energy_cost,
    energy_cost_by_period_and_month,
    power_charge_ponta_by_month,
)
from tariff_calendar import TariffCycle, TariffRegime, Periodo


@dataclass
class TariffInputs:
    energy_tariffs: Dict[Periodo, PeriodTariff]
    power_tariff_ponta: PowerTariffPonta
    contracted_power_kw: float
    contracted_power_fixed_eur_kw_day: float


MONTH_NAME_PT = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Março",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}

PIE_PERIOD_ORDER = [
    "ponta",
    "cheias",
    "vazio",
    "super_vazio",
]

PIE_PERIOD_COLORS = [
    "#D9827E",
    "#D7A64A",
    "#76A997",
    "#7C95C7",
]

PIE_LABEL_COLOR = "#23313F"
PIE_STROKE_COLOR = "#F7F4EF"


def _classify_day_type(dt_index: pd.DatetimeIndex) -> pd.Series:
    pt_holidays = holidays.country_holidays("PT", years=sorted(set(dt_index.year.tolist())))
    vals = []
    for ts in dt_index:
        d = ts.date()
        if d in pt_holidays or ts.weekday() == 6:
            vals.append("domingo_feriado")
        elif ts.weekday() == 5:
            vals.append("sabado")
        else:
            vals.append("dia_util")
    return pd.Series(vals, index=dt_index)


def _plot_pie_with_labels(df_pct: pd.DataFrame, title: str) -> alt.Chart:
    base = alt.Chart(df_pct).encode(
        theta=alt.Theta("percentagem:Q"),
        color=alt.Color("periodo:N", title="Período"),
        tooltip=[
            alt.Tooltip("periodo:N", title="Período"),
            alt.Tooltip("kwh:Q", title="Consumo (kWh)", format=".2f"),
            alt.Tooltip("percentagem:Q", title="Percentagem", format=".2f"),
        ],
    )
    arcs = base.mark_arc()
    labels = base.mark_text(radius=135, size=12).encode(text="pct_label:N")
    return (arcs + labels).properties(title=title)


def _plot_pie_with_labels_soft(df_pct: pd.DataFrame, title: str) -> alt.Chart:
    base = alt.Chart(df_pct).encode(
        theta=alt.Theta("percentagem:Q"),
        color=alt.Color(
            "periodo:N",
            title="PerÃ­odo",
            scale=alt.Scale(domain=PIE_PERIOD_ORDER, range=PIE_PERIOD_COLORS),
            sort=PIE_PERIOD_ORDER,
            legend=alt.Legend(orient="bottom"),
        ),
        tooltip=[
            alt.Tooltip("periodo:N", title="PerÃ­odo"),
            alt.Tooltip("kwh:Q", title="Consumo (kWh)", format=".2f"),
            alt.Tooltip("percentagem:Q", title="Percentagem", format=".1f"),
        ],
    )
    arcs = base.mark_arc(innerRadius=52, outerRadius=120, stroke="#FFFFFF", strokeWidth=2)
    label_halo = base.mark_text(
        radius=142,
        size=18,
        fontWeight="bold",
        color="#FFFFFF",
        stroke="#FFFFFF",
        strokeWidth=5,
    ).encode(text="pct_label:N")
    labels = base.mark_text(
        radius=142,
        size=18,
        fontWeight="bold",
        color="#405261",
    ).encode(text="pct_label:N")
    return (arcs + label_halo + labels).properties(title=title, height=320)


def _plot_pie_with_labels_clear(df_pct: pd.DataFrame, title: str) -> alt.Chart:
    base = alt.Chart(df_pct).encode(
        theta=alt.Theta("percentagem:Q"),
        color=alt.Color(
            "periodo:N",
            title="Periodo",
            scale=alt.Scale(domain=PIE_PERIOD_ORDER, range=PIE_PERIOD_COLORS),
            sort=PIE_PERIOD_ORDER,
            legend=alt.Legend(orient="bottom"),
        ),
        tooltip=[
            alt.Tooltip("periodo:N", title="Periodo"),
            alt.Tooltip("kwh:Q", title="Consumo (kWh)", format=".2f"),
            alt.Tooltip("percentagem:Q", title="Percentagem", format=".1f"),
        ],
    )
    arcs = base.mark_arc(innerRadius=52, outerRadius=120, stroke="#FFFFFF", strokeWidth=2)
    label_halo = base.mark_text(
        radius=142,
        size=18,
        fontWeight="bold",
        color="#FFFFFF",
        stroke="#FFFFFF",
        strokeWidth=5,
    ).encode(text="pct_label:N")
    labels = base.mark_text(
        radius=142,
        size=18,
        fontWeight="bold",
        color="#405261",
    ).encode(text="pct_label:N")
    return (arcs + label_halo + labels).properties(title=title, height=320)


def _tariff_inputs_to_payload(tariff_inputs: TariffInputs) -> dict:
    return {
        "energy_tariffs": {
            periodo.value: {
                "preco_comercializador": tariff.preco_comercializador,
                "tar_acesso_redes": tariff.tar_acesso_redes,
                "fin_tarifa_social": tariff.fin_tarifa_social,
            }
            for periodo, tariff in tariff_inputs.energy_tariffs.items()
        },
        "power_tariff_ponta_eur_kw_dia": tariff_inputs.power_tariff_ponta.eur_kw_dia,
        "contracted_power_kw": tariff_inputs.contracted_power_kw,
        "contracted_power_fixed_eur_kw_day": tariff_inputs.contracted_power_fixed_eur_kw_day,
    }


def _tariff_inputs_from_payload(payload: dict) -> TariffInputs:
    return TariffInputs(
        energy_tariffs={
            Periodo(periodo): PeriodTariff(
                preco_comercializador=values["preco_comercializador"],
                tar_acesso_redes=values["tar_acesso_redes"],
                fin_tarifa_social=values["fin_tarifa_social"],
            )
            for periodo, values in payload["energy_tariffs"].items()
        },
        power_tariff_ponta=PowerTariffPonta(eur_kw_dia=payload["power_tariff_ponta_eur_kw_dia"]),
        contracted_power_kw=payload["contracted_power_kw"],
        contracted_power_fixed_eur_kw_day=payload["contracted_power_fixed_eur_kw_day"],
    )


@st.cache_data(show_spinner=False)
def _load_profile_from_upload(upload_bytes: bytes, file_suffix: str) -> LoadProfile:
    suffix = file_suffix if file_suffix in {".csv", ".xls", ".xlsx"} else ".tmp"
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(upload_bytes)
            tmp_path = tmp_file.name

        profile = read_eredes_file(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    return profile


@st.cache_data(show_spinner=False)
def _compute_scenario_results_cached(
    profile_kw: pd.Series,
    ano: int,
    ciclo_value: str,
    tariff_payload: dict,
    regime_value: str,
) -> dict:
    tariff_inputs = _tariff_inputs_from_payload(tariff_payload)
    return _compute_scenario_results(
        profile_kw=profile_kw,
        ano=ano,
        ciclo=TariffCycle(ciclo_value),
        tariff_inputs=tariff_inputs,
        regime=TariffRegime(regime_value),
    )


def _prepare_pie_dataframe(df_pct: pd.DataFrame) -> pd.DataFrame:
    pie_df = df_pct.copy()
    pie_df["periodo"] = pd.Categorical(pie_df["periodo"], categories=PIE_PERIOD_ORDER, ordered=True)
    pie_df = pie_df.sort_values("periodo").reset_index(drop=True)
    pie_df["periodo_sort"] = range(len(pie_df))
    pie_df["pct_label"] = pie_df["percentagem"].map(lambda value: f"{value:.1f}%")
    return pie_df


def _plot_pie_premium(df_pct: pd.DataFrame, title: str) -> alt.Chart:
    pie_df = _prepare_pie_dataframe(df_pct)
    base = alt.Chart(pie_df).encode(
        theta=alt.Theta("percentagem:Q", stack=True),
        order=alt.Order("periodo_sort:Q"),
        color=alt.Color(
            "periodo:N",
            title="Periodo",
            scale=alt.Scale(domain=PIE_PERIOD_ORDER, range=PIE_PERIOD_COLORS),
            sort=PIE_PERIOD_ORDER,
            legend=alt.Legend(orient="bottom", labelColor=PIE_LABEL_COLOR, symbolType="circle", symbolSize=180),
        ),
        tooltip=[
            alt.Tooltip("periodo:N", title="Periodo"),
            alt.Tooltip("kwh:Q", title="Consumo (kWh)", format=".2f"),
            alt.Tooltip("percentagem:Q", title="Percentagem", format=".1f"),
        ],
    )

    arcs = base.mark_arc(innerRadius=58, outerRadius=120, cornerRadius=6, stroke=PIE_STROKE_COLOR, strokeWidth=1.5)
    labels = (
        alt.Chart(pie_df)
        .mark_text(radius=88, size=15, fontWeight="bold", color=PIE_LABEL_COLOR)
        .encode(
            theta=alt.Theta("percentagem:Q", stack=True),
            order=alt.Order("periodo_sort:Q"),
            text="pct_label:N",
            detail="periodo:N",
        )
    )

    return (arcs + labels).properties(title=title, height=320)


def _compute_scenario_results(
    profile_kw: pd.Series,
    ano: int,
    ciclo: TariffCycle,
    tariff_inputs: TariffInputs,
    regime: TariffRegime,
) -> dict:
    prices = build_tariff_series(
        ano=ano,
        ciclo=ciclo,
        period_tariffs=tariff_inputs.energy_tariffs,
        regime=regime,
    )
    df_energy = compute_energy_cost(profile_kw=profile_kw, energy_price_eur_kwh=prices)
    resumo_periodos = energy_cost_by_period_and_month(df_energy, ano=ano, ciclo=ciclo, regime=regime)
    df_pot = power_charge_ponta_by_month(
        ano=ano,
        ciclo=ciclo,
        power_tariff_ponta=tariff_inputs.power_tariff_ponta,
        contracted_power_kw=tariff_inputs.contracted_power_kw,
        regime=regime,
    )
    df_pot = df_pot.copy()
    df_pot["potencia_fixa_eur"] = (
        tariff_inputs.contracted_power_fixed_eur_kw_day
        * tariff_inputs.contracted_power_kw
        * df_pot["dias_no_mes"]
    )
    df_pot["potencia_total_eur"] = df_pot["potencia_eur"] + df_pot["potencia_fixa_eur"]

    total_energia = float(resumo_periodos["custo_eur"].sum())
    total_potencia = float(df_pot["potencia_total_eur"].sum())
    total = total_energia + total_potencia

    return {
        "prices": prices,
        "df_energy": df_energy,
        "resumo_periodos": resumo_periodos,
        "df_pot": df_pot,
        "total_energia": total_energia,
        "total_potencia": total_potencia,
        "total": total,
    }


def sidebar_inputs() -> tuple[TariffCycle, TariffRegime, TariffInputs]:
    st.sidebar.header("Parâmetros principais")

    ciclo_label = st.sidebar.selectbox(
        "Ciclo horário",
        options=[
            ("Ciclo semanal", TariffCycle.SEMANAL),
            ("Ciclo semanal opcional", TariffCycle.SEMANAL_OPCIONAL),
            ("Ciclo diário", TariffCycle.DIARIO),
        ],
        format_func=lambda x: x[0],
    )
    ciclo = ciclo_label[1]

    regime_label = st.sidebar.selectbox(
        "Regime de períodos horários",
        options=[
            ("Vigente (atual)", TariffRegime.VIGENTE),
            ("Novo (2027 / consulta 137)", TariffRegime.NOVO_2027),
        ],
        format_func=lambda x: x[0],
    )
    regime = regime_label[1]

    st.sidebar.subheader("Preços de energia (€/kWh)")
    fin_social_global = st.sidebar.number_input(
        "Fin. Tarifa Social (global, €/kWh)",
        min_value=0.0,
        step=0.0001,
        format="%.5f",
        value=0.0,
    )
    energy_tariffs: Dict[Periodo, PeriodTariff] = {}
    for periodo in Periodo:
        with st.sidebar.expander(f"Período: {periodo.value}", expanded=(periodo == Periodo.PONTA)):
            preco_com = st.number_input(
                "Preço comercializador",
                min_value=0.0,
                step=0.0001,
                format="%.5f",
                key=f"preco_com_{periodo.value}",
                value=0.19 if periodo == Periodo.PONTA else 0.10,
            )
            tar = st.number_input(
                "TAR — Acesso Redes",
                min_value=0.0,
                step=0.0001,
                format="%.5f",
                key=f"tar_{periodo.value}",
                value=0.0,
            )
            energy_tariffs[periodo] = PeriodTariff(
                preco_comercializador=preco_com,
                tar_acesso_redes=tar,
                fin_tarifa_social=fin_social_global,
            )

    st.sidebar.subheader("Potência em horas de ponta")
    potencia_kw = st.sidebar.number_input(
        "Potência contratada (kW)",
        min_value=0.0,
        step=0.1,
        value=10.0,
    )
    tarifa_php = st.sidebar.number_input(
        "Tarifa PHP (€/kW dia)",
        min_value=0.0,
        step=0.0001,
        format="%.5f",
        value=0.2,
    )
    tarifa_pot_fixa = st.sidebar.number_input(
        "Potência contratada fixa (€/kW dia)",
        min_value=0.0,
        step=0.0001,
        format="%.5f",
        value=0.0,
    )

    inputs = TariffInputs(
        energy_tariffs=energy_tariffs,
        power_tariff_ponta=PowerTariffPonta(eur_kw_dia=tarifa_php),
        contracted_power_kw=potencia_kw,
        contracted_power_fixed_eur_kw_day=tarifa_pot_fixa,
    )
    return ciclo, regime, inputs


def main() -> None:
    st.set_page_config(page_title="Simulador Tarifário BESS", layout="wide")
    st.title("Simulador base de tarifas (Portugal)")
    st.markdown(
        "Ferramenta base para análise de custos de energia e potência com ciclos "
        "horários portugueses, preparada para futura integração com sistemas fotovoltaicos e armazenamento."
    )

    ciclo, regime, tariff_inputs = sidebar_inputs()

    st.header("1. Carregamento do diagrama de carga (E-Redes)")
    uploaded = st.file_uploader(
        "Carregue o ficheiro anual (Excel/CSV) com 35 040 registos de 15 min",
        type=["xlsx", "xls", "csv"],
    )

    if uploaded is None:
        st.info("Aguardo o upload do ficheiro da E-Redes para avançar com os cálculos.")
        return

    # Guardar temporariamente em disco para reutilizar a função de leitura
    tmp_path = "eredes_temp_upload"
    with open(tmp_path, "wb") as f:
        f.write(uploaded.read())

    profile = read_eredes_file(tmp_path)
    ano = infer_year(profile)
    profile.data = profile.data[profile.data.index.year == ano].copy()

    if profile.filled_missing_count > 0:
        st.warning(
            f"Foram detetados {profile.filled_missing_count} intervalos de 15 min em falta "
            f"(de {profile.expected_count}). Esses valores foram preenchidos automaticamente por interpolação temporal."
        )

    st.success(f"Ficheiro carregado com sucesso. Ano detetado: **{ano}**.")
    st.write("Primeiras linhas do perfil de carga:")
    st.dataframe(profile.data.head(20))

    # Gráfico 1: consumo mensal ao longo do ano
    consumo_mensal = profile.data.copy()
    consumo_mensal["mes"] = consumo_mensal.index.month
    consumo_mensal["kwh"] = consumo_mensal["kw"] * 0.25
    consumo_mensal = consumo_mensal.groupby("mes", as_index=False)["kwh"].sum()
    consumo_mensal = consumo_mensal.sort_values("mes")
    consumo_mensal["Mês"] = consumo_mensal["mes"].map(MONTH_NAME_PT)
    st.subheader("Consumo mensal (kWh)")
    chart_mensal = (
        alt.Chart(consumo_mensal)
        .mark_bar()
        .encode(
            x=alt.X("Mês:N", sort=alt.SortField(field="mes", order="ascending")),
            y=alt.Y("kwh:Q", title="kWh"),
            tooltip=[alt.Tooltip("Mês:N"), alt.Tooltip("kwh:Q", format=".2f")],
        )
    )
    st.altair_chart(chart_mensal, use_container_width=True)

    res_selected = _compute_scenario_results(
        profile_kw=profile.data["kw"],
        ano=ano,
        ciclo=ciclo,
        tariff_inputs=tariff_inputs,
        regime=regime,
    )
    res_vig = _compute_scenario_results(
        profile_kw=profile.data["kw"],
        ano=ano,
        ciclo=ciclo,
        tariff_inputs=tariff_inputs,
        regime=TariffRegime.VIGENTE,
    )
    res_new = _compute_scenario_results(
        profile_kw=profile.data["kw"],
        ano=ano,
        ciclo=ciclo,
        tariff_inputs=tariff_inputs,
        regime=TariffRegime.NOVO_2027,
    )

    st.header("2. Folha de resumo")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ano de análise", f"{ano}")
    c2.metric("Registos válidos lidos", f"{profile.original_valid_count:,}".replace(",", "."))
    c3.metric("Registos em falta preenchidos", f"{profile.filled_missing_count:,}".replace(",", "."))
    c4.metric("Total final (15min)", f"{len(profile.data):,}".replace(",", "."))

    resumo_anual = pd.DataFrame(
        [
            {
                "cenario": "Vigente",
                "energia_eur": res_vig["total_energia"],
                "potencia_eur": res_vig["total_potencia"],
                "total_eur": res_vig["total"],
            },
            {
                "cenario": "Novo 2027",
                "energia_eur": res_new["total_energia"],
                "potencia_eur": res_new["total_potencia"],
                "total_eur": res_new["total"],
            },
            {
                "cenario": "Delta (Novo - Vigente)",
                "energia_eur": res_new["total_energia"] - res_vig["total_energia"],
                "potencia_eur": res_new["total_potencia"] - res_vig["total_potencia"],
                "total_eur": res_new["total"] - res_vig["total"],
            },
        ]
    )
    st.subheader("Resumo anual de comparação")
    st.dataframe(resumo_anual.style.format({"energia_eur": "{:.2f}", "potencia_eur": "{:.2f}", "total_eur": "{:.2f}"}))

    mensal_vig = (
        res_vig["resumo_periodos"].groupby("mes", as_index=False)["custo_eur"].sum().rename(columns={"custo_eur": "energia_vigente_eur"})
    )
    mensal_new = (
        res_new["resumo_periodos"].groupby("mes", as_index=False)["custo_eur"].sum().rename(columns={"custo_eur": "energia_novo_eur"})
    )
    pot_vig = res_vig["df_pot"][["mes", "potencia_eur"]].rename(columns={"potencia_eur": "potencia_vigente_eur"})
    pot_new = res_new["df_pot"][["mes", "potencia_eur"]].rename(columns={"potencia_eur": "potencia_novo_eur"})
    resumo_mensal = mensal_vig.merge(mensal_new, on="mes").merge(pot_vig, on="mes").merge(pot_new, on="mes")
    resumo_mensal["total_vigente_eur"] = resumo_mensal["energia_vigente_eur"] + resumo_mensal["potencia_vigente_eur"]
    resumo_mensal["total_novo_eur"] = resumo_mensal["energia_novo_eur"] + resumo_mensal["potencia_novo_eur"]
    resumo_mensal["delta_total_eur"] = resumo_mensal["total_novo_eur"] - resumo_mensal["total_vigente_eur"]
    resumo_mensal["Mês"] = resumo_mensal["mes"].map(MONTH_NAME_PT)
    resumo_mensal = resumo_mensal[
        [
            "Mês",
            "energia_vigente_eur",
            "energia_novo_eur",
            "potencia_vigente_eur",
            "potencia_novo_eur",
            "total_vigente_eur",
            "total_novo_eur",
            "delta_total_eur",
        ]
    ]
    st.subheader("Resumo mensal (Vigente vs Novo 2027)")
    st.dataframe(
        resumo_mensal.style.format(
            {
                "energia_vigente_eur": "{:.2f}",
                "energia_novo_eur": "{:.2f}",
                "potencia_vigente_eur": "{:.2f}",
                "potencia_novo_eur": "{:.2f}",
                "total_vigente_eur": "{:.2f}",
                "total_novo_eur": "{:.2f}",
                "delta_total_eur": "{:.2f}",
            }
        )
    )

    st.header("3. Cálculo de custos de energia")
    resumo_periodos = res_selected["resumo_periodos"]

    st.subheader("Energia — resumo mensal por período")
    pivot_energy = resumo_periodos.pivot(
        index="mes",
        columns="periodo",
        values="custo_eur",
    ).fillna(0.0)
    pivot_energy.index = pivot_energy.index.map(MONTH_NAME_PT)
    st.dataframe(pivot_energy.style.format("{:.2f}"))

    st.header("4. Potência em horas de ponta")
    df_pot = res_selected["df_pot"]
    df_pot_show = df_pot.copy()
    df_pot_show["Mês"] = df_pot_show["mes"].map(MONTH_NAME_PT)
    df_pot_show = df_pot_show[
        [
            "Mês",
            "horas_ponta",
            "dias_no_mes",
            "fator_hp",
            "potencia_eur",
            "potencia_fixa_eur",
            "potencia_total_eur",
        ]
    ]
    st.dataframe(
        df_pot_show.style.format(
            {
                "horas_ponta": "{:.1f}",
                "fator_hp": "{:.3f}",
                "potencia_eur": "{:.2f}",
                "potencia_fixa_eur": "{:.2f}",
                "potencia_total_eur": "{:.2f}",
            }
        )
    )

    st.header("5. Resultados agregados (cenário selecionado)")
    total_energia = res_selected["total_energia"]
    total_potencia = res_selected["total_potencia"]
    total = res_selected["total"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Custo anual de energia", f"{total_energia:,.2f} €")
    col2.metric("Custo anual de potência (total)", f"{total_potencia:,.2f} €")
    col3.metric("Total anual", f"{total:,.2f} €")

    # Gráficos circulares: percentagem de consumo por período (Vigente vs Novo 2027)
    period_vig = res_vig["resumo_periodos"].groupby("periodo", as_index=False)["kwh"].sum()
    period_new = res_new["resumo_periodos"].groupby("periodo", as_index=False)["kwh"].sum()
    period_vig["percentagem"] = (period_vig["kwh"] / period_vig["kwh"].sum() * 100.0).fillna(0.0)
    period_new["percentagem"] = (period_new["kwh"] / period_new["kwh"].sum() * 100.0).fillna(0.0)
    period_vig["periodo"] = period_vig["periodo"].astype(str)
    period_new["periodo"] = period_new["periodo"].astype(str)
    period_vig["pct_label"] = period_vig["percentagem"].map(lambda v: f"{v:.1f}%")
    period_new["pct_label"] = period_new["percentagem"].map(lambda v: f"{v:.1f}%")

    st.subheader("% de consumo por período horário")
    cpie1, cpie2 = st.columns(2)

    with cpie1:
        chart_vig = _plot_pie_with_labels_clear(period_vig, "Regime atual (Vigente)")
        st.altair_chart(chart_vig, use_container_width=True)

    with cpie2:
        chart_new = _plot_pie_with_labels_clear(period_new, "Regime futuro (Novo 2027)")
        st.altair_chart(chart_new, use_container_width=True)

    # Perfis diários médios por estação e tipo de dia
    perfil = profile.data.copy()
    perfil["month"] = perfil.index.month
    perfil["hhmm"] = perfil.index.strftime("%H:%M")
    perfil["day_type"] = _classify_day_type(perfil.index)
    perfil["ord"] = perfil.index.hour * 60 + perfil.index.minute

    def season_profile(months: list[int], title: str) -> None:
        df_s = perfil[perfil["month"].isin(months)].copy()
        avg = (
            df_s.groupby(["day_type", "hhmm", "ord"], as_index=False)["kw"]
            .mean()
            .rename(columns={"kw": "kw_medio"})
        )
        order = ["dia_util", "sabado", "domingo_feriado"]
        labels = {"dia_util": "Dias úteis", "sabado": "Sábados", "domingo_feriado": "Domingos/Feriados"}
        avg["Tipo"] = avg["day_type"].map(labels)
        avg["ord_tipo"] = avg["day_type"].map({k: i for i, k in enumerate(order)})

        chart = (
            alt.Chart(avg)
            .mark_line()
            .encode(
                x=alt.X("hhmm:N", sort=alt.SortField(field="ord", order="ascending"), title="Hora"),
                y=alt.Y("kw_medio:Q", title="kW médio"),
                color=alt.Color("Tipo:N", sort=[labels[o] for o in order], title="Tipo de dia"),
                tooltip=[
                    alt.Tooltip("Tipo:N", title="Tipo"),
                    alt.Tooltip("hhmm:N", title="Hora"),
                    alt.Tooltip("kw_medio:Q", title="kW médio", format=".3f"),
                ],
            )
            .properties(title=title)
        )
        st.altair_chart(chart, use_container_width=True)

    st.subheader("Perfis diários médios de consumo")
    season_profile([4, 5, 6, 7], "Verão (Abril a Julho): dias úteis, sábados, domingos/feriados")
    season_profile([11, 12, 1, 2, 3], "Inverno (Novembro a Março): dias úteis, sábados, domingos/feriados")

    st.markdown(
        "Esta é a base de cálculo. Na próxima fase podemos ligar aqui:\n"
        "- Perfis de produção fotovoltaica e algoritmos de otimização de carga/descarga."
    )


if __name__ == "__main__":
    from app_new import main as app_main

    app_main()
