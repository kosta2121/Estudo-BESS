from __future__ import annotations

from calendar import monthrange
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict

import altair as alt
import holidays
import pandas as pd
import streamlit as st

from load_profile import LoadProfile, infer_year, read_eredes_file
from pricing import (
    PeriodTariff,
    PowerTariffPonta,
    compute_energy_cost,
    energy_cost_by_period_and_month,
)
from tariff_calendar import Periodo, TariffCycle, TariffRegime, gerar_mapa_periodos_ano, horas_ponta_por_mes


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

MONTH_SHORT_PT = {
    1: "Jan",
    2: "Fev",
    3: "Mar",
    4: "Abr",
    5: "Mai",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Set",
    10: "Out",
    11: "Nov",
    12: "Dez",
}

PIE_PERIOD_ORDER = [
    "ponta",
    "cheias",
    "vazio",
    "super_vazio",
]

PIE_PERIOD_COLORS = [
    "#D78074",
    "#D9A441",
    "#72A694",
    "#7D93C4",
]

PIE_LABEL_COLOR = "#243240"
PIE_STROKE_COLOR = "#F5F1EA"
HEATMAP_COLORS = ["#67B96F", "#F3E58C", "#F4A36F", "#E1645B"]


def _format_pt_number(value: float, decimals: int = 2) -> str:
    formatted = f"{float(value):,.{decimals}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", " ")


def _format_pt_currency(value: float, decimals: int = 2) -> str:
    return f"{_format_pt_number(value, decimals)} €"


def _classify_day_type(dt_index: pd.DatetimeIndex) -> pd.Series:
    pt_holidays = holidays.country_holidays("PT", years=sorted(set(dt_index.year.tolist())))
    vals = []
    for ts in dt_index:
        day = ts.date()
        if day in pt_holidays or ts.weekday() == 6:
            vals.append("domingo_feriado")
        elif ts.weekday() == 5:
            vals.append("sabado")
        else:
            vals.append("dia_util")
    return pd.Series(vals, index=dt_index)


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


def _php_equivalent_by_month(
    ano: int,
    ciclo: TariffCycle,
    php_base_eur_kw_day: float,
    regime: TariffRegime,
) -> pd.DataFrame:
    horas_ponta = horas_ponta_por_mes(ano, ciclo, passo_minutos=15, regime=regime)
    rows = []

    for mes in range(1, 13):
        dias_no_mes = monthrange(ano, mes)[1]
        hp = float(horas_ponta[mes])
        fator = (dias_no_mes / hp) if hp > 0 else 0.0
        php_equiv = fator * php_base_eur_kw_day
        rows.append(
            {
                "mes": mes,
                "dias_no_mes": dias_no_mes,
                "horas_ponta": hp,
                "fator_php": fator,
                "php_equiv_eur_kwh": php_equiv,
            }
        )

    return pd.DataFrame(rows)


def _build_energy_price_series(
    ano: int,
    ciclo: TariffCycle,
    tariff_inputs: TariffInputs,
    regime: TariffRegime,
) -> pd.Series:
    mapa = gerar_mapa_periodos_ano(ano, ciclo, passo_minutos=15, regime=regime)
    idx = pd.DatetimeIndex(sorted(mapa.keys()))
    periodos = pd.Series([mapa[t] for t in idx], index=idx, dtype="object")

    php_by_month = _php_equivalent_by_month(
        ano=ano,
        ciclo=ciclo,
        php_base_eur_kw_day=tariff_inputs.power_tariff_ponta.eur_kw_dia,
        regime=regime,
    ).set_index("mes")["php_equiv_eur_kwh"]

    prices = []
    for ts, periodo in periodos.items():
        base_price = tariff_inputs.energy_tariffs[periodo].total_eur_kwh
        php_equiv = float(php_by_month.loc[ts.month]) if periodo == Periodo.PONTA else 0.0
        prices.append(base_price + php_equiv)

    price_series = pd.Series(prices, index=idx, name="eur_kwh")
    return price_series


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


def _prepare_pie_dataframe(df_pct: pd.DataFrame) -> pd.DataFrame:
    pie_df = df_pct.copy()
    pie_df["periodo"] = pd.Categorical(pie_df["periodo"], categories=PIE_PERIOD_ORDER, ordered=True)
    pie_df = pie_df.sort_values("periodo").reset_index(drop=True)
    pie_df["periodo_sort"] = range(len(pie_df))
    pie_df["pct_label"] = pie_df["percentagem"].map(lambda value: f"{value:.1f}%")
    return pie_df


def _build_share_dataframe(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    share_df = df.groupby("periodo", as_index=False)[value_col].sum()
    total = float(share_df[value_col].sum())
    share_df["percentagem"] = 0.0 if total == 0 else share_df[value_col] / total * 100.0
    share_df["periodo"] = share_df["periodo"].astype(str)
    return share_df


def _plot_pie_premium(df_pct: pd.DataFrame, value_col: str, value_title: str, value_format: str) -> alt.Chart:
    pie_df = _prepare_pie_dataframe(df_pct)
    base = alt.Chart(pie_df).encode(
        theta=alt.Theta("percentagem:Q", stack=True),
        order=alt.Order("periodo_sort:Q"),
        color=alt.Color(
            "periodo:N",
            title="Período",
            scale=alt.Scale(domain=PIE_PERIOD_ORDER, range=PIE_PERIOD_COLORS),
            sort=PIE_PERIOD_ORDER,
            legend=alt.Legend(
                orient="bottom",
                labelColor=PIE_LABEL_COLOR,
                symbolType="circle",
                symbolSize=180,
            ),
        ),
        tooltip=[
            alt.Tooltip("periodo:N", title="Período"),
            alt.Tooltip(f"{value_col}:Q", title=value_title, format=value_format),
            alt.Tooltip("percentagem:Q", title="Percentagem", format=".1f"),
        ],
    )

    arcs = base.mark_arc(
        innerRadius=58,
        outerRadius=120,
        cornerRadius=6,
        padAngle=0.012,
        stroke=PIE_STROKE_COLOR,
        strokeWidth=1.5,
    )
    labels = (
        alt.Chart(pie_df)
        .mark_text(radius=89, size=14, fontWeight="bold", color=PIE_LABEL_COLOR)
        .encode(
            theta=alt.Theta("percentagem:Q", stack=True),
            order=alt.Order("periodo_sort:Q"),
            text="pct_label:N",
            detail="periodo:N",
        )
    )

    return (arcs + labels).properties(height=320)


def _build_php_validation_table(
    ano: int,
    ciclo: TariffCycle,
    tariff_inputs: TariffInputs,
    regime: TariffRegime,
) -> pd.DataFrame:
    php_by_month = _php_equivalent_by_month(
        ano=ano,
        ciclo=ciclo,
        php_base_eur_kw_day=tariff_inputs.power_tariff_ponta.eur_kw_dia,
        regime=regime,
    )

    rows = []
    for month in range(1, 13):
        month_row = php_by_month.loc[php_by_month["mes"] == month].iloc[0]
        rows.append(
            {
                "Mes": MONTH_NAME_PT[month],
                "Dias no mes": int(month_row["dias_no_mes"]),
                "Horas de Ponta no mes": float(month_row["horas_ponta"]),
                "Factor (Dias/H.Ponta)": float(month_row["fator_php"]),
                "TAR PHP equiv. (EUR/kWh ponta)": float(month_row["php_equiv_eur_kwh"]),
            }
        )
    return pd.DataFrame(rows)


def _build_final_tariffs_table(
    ano: int,
    ciclo: TariffCycle,
    tariff_inputs: TariffInputs,
    regime: TariffRegime,
) -> pd.DataFrame:
    php_by_month = _php_equivalent_by_month(
        ano=ano,
        ciclo=ciclo,
        php_base_eur_kw_day=tariff_inputs.power_tariff_ponta.eur_kw_dia,
        regime=regime,
    ).set_index("mes")["php_equiv_eur_kwh"]

    rows = []
    for mes in range(1, 13):
        row = {"Mes": MONTH_NAME_PT[mes]}
        for periodo in Periodo:
            total = tariff_inputs.energy_tariffs[periodo].total_eur_kwh
            if periodo == Periodo.PONTA:
                total += float(php_by_month.loc[mes])
            row[f"{periodo.value} (EUR/kWh)"] = total
        rows.append(row)

    return pd.DataFrame(rows)


def _build_energy_cost_heatmap_dataframe(df_energy: pd.DataFrame) -> pd.DataFrame:
    heatmap = df_energy[["custo_eur"]].copy()
    heatmap["mes"] = heatmap.index.month
    heatmap["mes_label"] = heatmap["mes"].map(MONTH_SHORT_PT)
    heatmap["ord"] = heatmap.index.hour * 60 + heatmap.index.minute
    heatmap["hhmm"] = heatmap.index.strftime("%H:%M")

    heatmap = (
        heatmap.groupby(["mes", "mes_label", "ord", "hhmm"], as_index=False)["custo_eur"]
        .mean()
        .sort_values(["mes", "ord"])
    )
    heatmap["ord_label"] = heatmap["ord"].map(lambda value: f"{value // 60:02d}:{value % 60:02d}")
    return heatmap


def _plot_energy_cost_heatmap(df_energy: pd.DataFrame, color_domain: list[float]) -> alt.Chart:
    heatmap_df = _build_energy_cost_heatmap_dataframe(df_energy)
    hour_ticks = [f"{hour:02d}:00" for hour in range(24)]

    return (
        alt.Chart(heatmap_df)
        .mark_rect()
        .encode(
            x=alt.X(
                "hhmm:N",
                sort=alt.SortField(field="ord", order="ascending"),
                axis=alt.Axis(
                    title=None,
                    labelAngle=-90,
                    labelOverlap=False,
                    values=hour_ticks,
                    labelColor=PIE_LABEL_COLOR,
                    grid=True,
                    tickSize=0,
                    labelPadding=8,
                ),
            ),
            y=alt.Y(
                "mes_label:N",
                sort=list(MONTH_SHORT_PT.values()),
                axis=alt.Axis(title=None, labelColor=PIE_LABEL_COLOR),
            ),
            color=alt.Color(
                "custo_eur:Q",
                title="Custo médio",
                scale=alt.Scale(domain=color_domain, range=HEATMAP_COLORS),
                legend=alt.Legend(orient="bottom", labelColor=PIE_LABEL_COLOR),
            ),
            tooltip=[
                alt.Tooltip("mes_label:N", title="Mês"),
                alt.Tooltip("hhmm:N", title="Bloco"),
                alt.Tooltip("custo_eur:Q", title="Custo médio (€)", format=".4f"),
            ],
        )
        .properties(height=280)
        .configure_view(stroke=None)
    )


def _table_height(df: pd.DataFrame) -> int:
    return 38 + len(df) * 35


def _display_compact_table(df: pd.DataFrame, formats: dict[str, str] | None = None) -> None:
    display_df = df.copy().reset_index(drop=True)
    if formats:
        for col, formatter in formats.items():
            if col in display_df.columns:
                if formatter == "currency_2":
                    display_df[col] = display_df[col].map(lambda value: _format_pt_currency(value, 2))
                elif formatter == "currency_5":
                    display_df[col] = display_df[col].map(lambda value: _format_pt_currency(value, 5))
                elif formatter == "number_0":
                    display_df[col] = display_df[col].map(lambda value: _format_pt_number(value, 0))
                elif formatter == "number_1":
                    display_df[col] = display_df[col].map(lambda value: _format_pt_number(value, 1))
                elif formatter == "number_2":
                    display_df[col] = display_df[col].map(lambda value: _format_pt_number(value, 2))
                elif formatter == "number_3":
                    display_df[col] = display_df[col].map(lambda value: _format_pt_number(value, 3))
                elif formatter == "number_4":
                    display_df[col] = display_df[col].map(lambda value: _format_pt_number(value, 4))
                elif formatter == "number_5":
                    display_df[col] = display_df[col].map(lambda value: _format_pt_number(value, 5))

    st.dataframe(display_df, use_container_width=True, hide_index=True, height=_table_height(display_df))


def _compute_scenario_results(
    profile_kw: pd.Series,
    ano: int,
    ciclo: TariffCycle,
    tariff_inputs: TariffInputs,
    regime: TariffRegime,
) -> dict:
    prices = _build_energy_price_series(
        ano=ano,
        ciclo=ciclo,
        tariff_inputs=tariff_inputs,
        regime=regime,
    )
    df_energy = compute_energy_cost(profile_kw=profile_kw, energy_price_eur_kwh=prices)
    resumo_periodos = energy_cost_by_period_and_month(df_energy, ano=ano, ciclo=ciclo, regime=regime)
    php_by_month = _php_equivalent_by_month(
        ano=ano,
        ciclo=ciclo,
        php_base_eur_kw_day=tariff_inputs.power_tariff_ponta.eur_kw_dia,
        regime=regime,
    )
    df_pot = php_by_month.copy()
    df_pot["potencia_eur"] = 0.0
    df_pot = df_pot.copy()
    df_pot["potencia_fixa_eur"] = tariff_inputs.contracted_power_fixed_eur_kw_day * df_pot["dias_no_mes"]
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


def sidebar_inputs() -> tuple[TariffCycle, TariffRegime, TariffInputs, bool]:
    st.sidebar.header("Parâmetros principais")
    st.sidebar.caption("As alterações ficam pendentes e só são aplicadas quando carregar em Atualizar estudo.")

    with st.sidebar.form("study_parameters_form", clear_on_submit=False):
        ciclo_label = st.selectbox(
            "Ciclo horário",
            options=[
                ("Ciclo semanal", TariffCycle.SEMANAL),
                ("Ciclo semanal opcional", TariffCycle.SEMANAL_OPCIONAL),
                ("Ciclo diário", TariffCycle.DIARIO),
            ],
            format_func=lambda item: item[0],
        )
        ciclo = ciclo_label[1]

        regime_label = st.selectbox(
            "Regime de períodos horários",
            options=[
                ("Vigente (atual)", TariffRegime.VIGENTE),
                ("Novo (2027 / consulta 137)", TariffRegime.NOVO_2027),
            ],
            format_func=lambda item: item[0],
        )
        regime = regime_label[1]

        st.subheader("Preços de energia (€/kWh)")
        fin_social_global = st.number_input(
            "Fin. Tarifa Social (global, €/kWh)",
            min_value=0.0,
            step=0.0001,
            format="%.5f",
            value=0.0,
        )

        energy_tariffs: Dict[Periodo, PeriodTariff] = {}
        for periodo in Periodo:
            with st.expander(f"Período: {periodo.value}", expanded=(periodo == Periodo.PONTA)):
                preco_com = st.number_input(
                    "Preço comercializador",
                    min_value=0.0,
                    step=0.0001,
                    format="%.5f",
                    key=f"preco_com_{periodo.value}",
                    value=0.19 if periodo == Periodo.PONTA else 0.10,
                )
                tar = st.number_input(
                    "TAR - Acesso Redes",
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

        st.subheader("Potência em horas de ponta")
        potencia_kw = st.number_input(
            "Potência contratada (kW)",
            min_value=0.0,
            step=0.1,
            value=10.0,
        )
        tarifa_php = st.number_input(
            "Tarifa PHP (€/kW dia)",
            min_value=0.0,
            step=0.0001,
            format="%.5f",
            value=0.2,
        )
        tarifa_pot_fixa = st.number_input(
            "Potência contratada fixa (€/dia)",
            min_value=0.0,
            step=0.0001,
            format="%.5f",
            value=0.0,
        )

        submitted = st.form_submit_button("Atualizar estudo", type="primary", use_container_width=True)

    inputs = TariffInputs(
        energy_tariffs=energy_tariffs,
        power_tariff_ponta=PowerTariffPonta(eur_kw_dia=tarifa_php),
        contracted_power_kw=potencia_kw,
        contracted_power_fixed_eur_kw_day=tarifa_pot_fixa,
    )
    return ciclo, regime, inputs, submitted


def main() -> None:
    st.set_page_config(page_title="Simulador Tarifário BESS", layout="wide")
    st.title("Simulador base de tarifas (Portugal)")
    st.markdown(
        "Ferramenta base para análise de custos de energia e potência com ciclos "
        "horários portugueses, preparada para futura integração com sistemas fotovoltaicos e armazenamento."
    )

    ciclo, regime, tariff_inputs, submitted = sidebar_inputs()

    if submitted:
        st.session_state["last_update_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    elif "last_update_at" not in st.session_state:
        st.session_state["last_update_at"] = "valores iniciais"

    st.caption(
        f"Atualização aplicada: {st.session_state['last_update_at']}. "
        "Os parâmetros da sidebar só entram no estudo quando carregar em Atualizar estudo."
    )

    st.header("1. Carregamento do diagrama de carga (E-Redes)")
    uploaded = st.file_uploader(
        "Carregue o ficheiro anual (Excel/CSV) com 35 040 registos de 15 min",
        type=["xlsx", "xls", "csv"],
    )

    if uploaded is None:
        st.info("A aguardar o upload do ficheiro da E-Redes para avançar com os cálculos.")
        return

    upload_bytes = uploaded.getvalue()
    upload_suffix = Path(uploaded.name or "").suffix.lower()

    with st.spinner("A processar o ficheiro e a preparar o estudo..."):
        profile = _load_profile_from_upload(upload_bytes, upload_suffix)
        ano = infer_year(profile)
        profile.data = profile.data[profile.data.index.year == ano].copy()

        tariff_payload = _tariff_inputs_to_payload(tariff_inputs)
        res_vig = _compute_scenario_results_cached(
            profile_kw=profile.data["kw"],
            ano=ano,
            ciclo_value=ciclo.value,
            tariff_payload=tariff_payload,
            regime_value=TariffRegime.VIGENTE.value,
        )
        res_new = _compute_scenario_results_cached(
            profile_kw=profile.data["kw"],
            ano=ano,
            ciclo_value=ciclo.value,
            tariff_payload=tariff_payload,
            regime_value=TariffRegime.NOVO_2027.value,
        )

    results_by_regime = {
        TariffRegime.VIGENTE: res_vig,
        TariffRegime.NOVO_2027: res_new,
    }
    res_selected = results_by_regime[regime]

    if profile.filled_missing_count > 0:
        st.warning(
            f"Foram detetados {profile.filled_missing_count} intervalos de 15 min em falta "
            f"(de {profile.expected_count}). Esses valores foram preenchidos automaticamente por interpolação temporal."
        )

    st.success(f"Ficheiro carregado com sucesso. Ano detetado: **{ano}**.")

    consumo_mensal = profile.data.copy()
    consumo_mensal["mes"] = consumo_mensal.index.month
    consumo_mensal["kwh"] = consumo_mensal["kw"] * 0.25
    consumo_mensal = consumo_mensal.groupby("mes", as_index=False)["kwh"].sum()
    consumo_mensal = consumo_mensal.sort_values("mes")
    consumo_mensal["Mes"] = consumo_mensal["mes"].map(MONTH_NAME_PT)

    st.subheader("Consumo mensal (kWh)")
    chart_mensal = (
        alt.Chart(consumo_mensal)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color="#6E8FB3")
        .encode(
            x=alt.X("Mes:N", sort=alt.SortField(field="mes", order="ascending")),
            y=alt.Y("kwh:Q", title="kWh"),
            tooltip=[alt.Tooltip("Mes:N"), alt.Tooltip("kwh:Q", format=".2f")],
        )
    )
    st.altair_chart(chart_mensal, use_container_width=True)

    _php_validation_table = _build_php_validation_table(
        ano=ano,
        ciclo=ciclo,
        tariff_inputs=tariff_inputs,
        regime=regime,
    )

    _final_tariffs_table = _build_final_tariffs_table(
        ano=ano,
        ciclo=ciclo,
        tariff_inputs=tariff_inputs,
        regime=regime,
    )

    st.header("2. Folha de resumo")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ano de análise", f"{ano}")
    c2.metric("Registos válidos lidos", _format_pt_number(profile.original_valid_count, 0))
    c3.metric("Registos em falta preenchidos", _format_pt_number(profile.filled_missing_count, 0))
    c4.metric("Total final (15 min)", _format_pt_number(len(profile.data), 0))

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
    _resumo_anual = resumo_anual

    mensal_vig = (
        res_vig["resumo_periodos"]
        .groupby("mes", as_index=False)["custo_eur"]
        .sum()
        .rename(columns={"custo_eur": "energia_vigente_eur"})
    )
    mensal_new = (
        res_new["resumo_periodos"]
        .groupby("mes", as_index=False)["custo_eur"]
        .sum()
        .rename(columns={"custo_eur": "energia_novo_eur"})
    )
    pot_vig = res_vig["df_pot"][["mes", "potencia_total_eur"]].rename(columns={"potencia_total_eur": "potencia_vigente_eur"})
    pot_new = res_new["df_pot"][["mes", "potencia_total_eur"]].rename(columns={"potencia_total_eur": "potencia_novo_eur"})
    resumo_mensal = mensal_vig.merge(mensal_new, on="mes").merge(pot_vig, on="mes").merge(pot_new, on="mes")
    resumo_mensal["total_vigente_eur"] = resumo_mensal["energia_vigente_eur"] + resumo_mensal["potencia_vigente_eur"]
    resumo_mensal["total_novo_eur"] = resumo_mensal["energia_novo_eur"] + resumo_mensal["potencia_novo_eur"]
    resumo_mensal["delta_total_eur"] = resumo_mensal["total_novo_eur"] - resumo_mensal["total_vigente_eur"]
    resumo_mensal["Mes"] = resumo_mensal["mes"].map(MONTH_NAME_PT)
    resumo_mensal = resumo_mensal[["Mes", "total_vigente_eur", "total_novo_eur", "delta_total_eur"]]
    resumo_mensal = resumo_mensal.rename(
        columns={
            "total_vigente_eur": "Custo com TAR Atual",
            "total_novo_eur": "Custo com TAR 2027",
            "delta_total_eur": "Diferença",
        }
    )
    resumo_mensal_total = pd.DataFrame(
        [
            {
                "Mes": "Total",
                "Custo com TAR Atual": resumo_mensal["Custo com TAR Atual"].sum(),
                "Custo com TAR 2027": resumo_mensal["Custo com TAR 2027"].sum(),
                "Diferença": resumo_mensal["Diferença"].sum(),
            }
        ]
    )
    resumo_mensal = pd.concat([resumo_mensal, resumo_mensal_total], ignore_index=True)

    st.subheader("Resumo mensal do custo com TAR Atual vs TAR 2027")
    _display_compact_table(
        resumo_mensal,
        {
            "Custo com TAR Atual": "currency_2",
            "Custo com TAR 2027": "currency_2",
            "Diferença": "currency_2",
        },
    )

    resumo_periodos = res_selected["resumo_periodos"]
    pivot_energy = resumo_periodos.pivot(index="mes", columns="periodo", values="custo_eur").fillna(0.0)
    pivot_energy.index = pivot_energy.index.map(MONTH_NAME_PT)
    pivot_energy.columns = [col.value if hasattr(col, "value") else str(col) for col in pivot_energy.columns]
    total_row = pd.DataFrame([pivot_energy.sum(axis=0)], index=["Total"])
    pivot_energy = pd.concat([pivot_energy, total_row])
    pivot_energy = pivot_energy.reset_index()
    first_col = pivot_energy.columns[0]
    pivot_energy = pivot_energy.rename(columns={first_col: "Mes"})
    _pivot_energy = pivot_energy

    st.header("4. Resultados agregados (cenário selecionado)")
    total_energia = res_selected["total_energia"]
    total_potencia = res_selected["total_potencia"]
    total = res_selected["total"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Custo anual de energia", _format_pt_currency(total_energia, 2))
    col2.metric("Custo anual de potência fixa", _format_pt_currency(total_potencia, 2))
    col3.metric("Total anual", _format_pt_currency(total, 2))

    consumo_vig = _build_share_dataframe(res_vig["resumo_periodos"], "kwh")
    consumo_new = _build_share_dataframe(res_new["resumo_periodos"], "kwh")
    custo_vig = _build_share_dataframe(res_vig["resumo_periodos"], "custo_eur")
    custo_new = _build_share_dataframe(res_new["resumo_periodos"], "custo_eur")

    st.subheader("% de consumo por período horário")
    cpie1, cpie2 = st.columns(2)

    with cpie1:
        st.markdown("<div style='text-align:center; font-weight:600;'>TAR Atual</div>", unsafe_allow_html=True)
        chart_vig = _plot_pie_premium(consumo_vig, "kwh", "Consumo (kWh)", ".2f")
        st.altair_chart(chart_vig, use_container_width=True)

    with cpie2:
        st.markdown("<div style='text-align:center; font-weight:600;'>TAR 2027</div>", unsafe_allow_html=True)
        chart_new = _plot_pie_premium(consumo_new, "kwh", "Consumo (kWh)", ".2f")
        st.altair_chart(chart_new, use_container_width=True)

    st.subheader("% de custo de energia por período horário")
    ccost1, ccost2 = st.columns(2)

    with ccost1:
        st.markdown("<div style='text-align:center; font-weight:600;'>TAR Atual</div>", unsafe_allow_html=True)
        chart_cost_vig = _plot_pie_premium(custo_vig, "custo_eur", "Custo de energia (€)", ".2f")
        st.altair_chart(chart_cost_vig, use_container_width=True)

    with ccost2:
        st.markdown("<div style='text-align:center; font-weight:600;'>TAR 2027</div>", unsafe_allow_html=True)
        chart_cost_new = _plot_pie_premium(custo_new, "custo_eur", "Custo de energia (€)", ".2f")
        st.altair_chart(chart_cost_new, use_container_width=True)

    heatmap_vig_data = _build_energy_cost_heatmap_dataframe(res_vig["df_energy"])
    heatmap_new_data = _build_energy_cost_heatmap_dataframe(res_new["df_energy"])
    heatmap_min = min(float(heatmap_vig_data["custo_eur"].min()), float(heatmap_new_data["custo_eur"].min()))
    heatmap_max = max(float(heatmap_vig_data["custo_eur"].max()), float(heatmap_new_data["custo_eur"].max()))
    if heatmap_min == heatmap_max:
        heatmap_max = heatmap_min + 1e-9
    heatmap_domain = [heatmap_min, heatmap_max]

    st.subheader("Heatmap custo de energia por bloco de 15 minutos")
    chea1, chea2 = st.columns(2)

    with chea1:
        st.markdown("<div style='text-align:center; font-weight:600;'>Custo com TAR Atual</div>", unsafe_allow_html=True)
        heatmap_vig = _plot_energy_cost_heatmap(res_vig["df_energy"], heatmap_domain)
        st.altair_chart(heatmap_vig, use_container_width=True)

    with chea2:
        st.markdown("<div style='text-align:center; font-weight:600;'>Custo com TAR 2027</div>", unsafe_allow_html=True)
        heatmap_new = _plot_energy_cost_heatmap(res_new["df_energy"], heatmap_domain)
        st.altair_chart(heatmap_new, use_container_width=True)

    perfil = profile.data.copy()
    perfil["month"] = perfil.index.month
    perfil["hhmm"] = perfil.index.strftime("%H:%M")
    perfil["day_type"] = _classify_day_type(perfil.index)
    perfil["ord"] = perfil.index.hour * 60 + perfil.index.minute
    perfil["tarifa_eur_kwh"] = res_selected["prices"].reindex(perfil.index).values

    def season_profile(months: list[int], title: str) -> None:
        df_s = perfil[perfil["month"].isin(months)].copy()
        avg = (
            df_s.groupby(["day_type", "hhmm", "ord"], as_index=False)
            .agg(kw_medio=("kw", "mean"), tarifa_media=("tarifa_eur_kwh", "mean"))
        )
        order = ["dia_util", "sabado", "domingo_feriado"]
        labels = {"dia_util": "Dias úteis", "sabado": "Sábados", "domingo_feriado": "Domingos/Feriados"}
        avg["Tipo"] = avg["day_type"].map(labels)
        tarifa_uteis = (
            avg.loc[avg["day_type"] == "dia_util", ["hhmm", "ord", "tarifa_media"]]
            .sort_values("ord")
            .reset_index(drop=True)
        )

        base = alt.Chart(avg).encode(
            x=alt.X("hhmm:N", sort=alt.SortField(field="ord", order="ascending"), title="Hora"),
            color=alt.Color(
                "Tipo:N",
                sort=[labels[item] for item in order],
                title="Tipo de dia",
                legend=alt.Legend(
                    orient="bottom",
                    direction="horizontal",
                    columns=3,
                    labelColor=PIE_LABEL_COLOR,
                    titleColor=PIE_LABEL_COLOR,
                    symbolStrokeWidth=3,
                ),
            ),
        )

        consumo = base.mark_line().encode(
            y=alt.Y("kw_medio:Q", title="kW médio"),
            tooltip=[
                alt.Tooltip("Tipo:N", title="Tipo"),
                alt.Tooltip("hhmm:N", title="Hora"),
                alt.Tooltip("kw_medio:Q", title="Consumo médio (kW)", format=".3f"),
            ],
        )

        tarifa_ref = (
            alt.Chart(tarifa_uteis)
            .mark_line(strokeDash=[10, 5], strokeWidth=3, color="#364152", opacity=1.0)
            .encode(
                x=alt.X("hhmm:N", sort=alt.SortField(field="ord", order="ascending"), title="Hora"),
                y=alt.Y(
                    "tarifa_media:Q",
                    title="Tarifa média dias úteis (€/kWh)",
                    axis=alt.Axis(
                        orient="right",
                        grid=False,
                        format=".2f",
                        labelColor="#364152",
                        titleColor="#364152",
                        titleAngle=90,
                        titlePadding=18,
                    ),
                ),
                tooltip=[
                    alt.Tooltip("hhmm:N", title="Hora"),
                    alt.Tooltip("tarifa_media:Q", title="Tarifa média dias úteis (€/kWh)", format=".5f"),
                ],
            )
        )
        chart = alt.layer(consumo, tarifa_ref).resolve_scale(y="independent").properties(title=title)
        st.altair_chart(chart, use_container_width=True)

    st.subheader("Perfis diários médios de consumo e tarifa")
    st.caption(
        "Linhas coloridas contínuas: consumo médio por tipo de dia. Linha tracejada escura: tarifa média dos dias úteis, no eixo da direita."
    )
    season_profile([4, 5, 6, 7], "Verão (Abril a Julho): dias úteis, sábados, domingos/feriados")
    season_profile([11, 12, 1, 2, 3], "Inverno (Novembro a Março): dias úteis, sábados, domingos/feriados")


if __name__ == "__main__":
    main()
