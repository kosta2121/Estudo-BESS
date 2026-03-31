from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pandas as pd
import streamlit as st

from load_profile import read_eredes_file, infer_year
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
            fin = st.number_input(
                "Fin. Tarifa Social",
                min_value=0.0,
                step=0.0001,
                format="%.5f",
                key=f"fin_{periodo.value}",
                value=0.0,
            )
            energy_tariffs[periodo] = PeriodTariff(
                preco_comercializador=preco_com,
                tar_acesso_redes=tar,
                fin_tarifa_social=fin,
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

    inputs = TariffInputs(
        energy_tariffs=energy_tariffs,
        power_tariff_ponta=PowerTariffPonta(eur_kw_dia=tarifa_php),
        contracted_power_kw=potencia_kw,
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

    st.success(f"Ficheiro carregado com sucesso. Ano detetado: **{ano}**.")
    st.write("Primeiras linhas do perfil de carga:")
    st.dataframe(profile.data.head(20))

    st.header("2. Cálculo de custos de energia")
    energy_price_series = build_tariff_series(
        ano=ano,
        ciclo=ciclo,
        period_tariffs=tariff_inputs.energy_tariffs,
        regime=regime,
    )

    df_energy = compute_energy_cost(
        profile_kw=profile.data["kw"],
        energy_price_eur_kwh=energy_price_series,
    )
    resumo_periodos = energy_cost_by_period_and_month(
        df_energy,
        ano=ano,
        ciclo=ciclo,
        regime=regime,
    )

    st.subheader("Energia — resumo mensal por período")
    pivot_energy = resumo_periodos.pivot(
        index="mes",
        columns="periodo",
        values="custo_eur",
    ).fillna(0.0)
    st.dataframe(pivot_energy.style.format("{:.2f}"))

    st.header("3. Potência em horas de ponta")
    df_pot = power_charge_ponta_by_month(
        ano=ano,
        ciclo=ciclo,
        power_tariff_ponta=tariff_inputs.power_tariff_ponta,
        contracted_power_kw=tariff_inputs.contracted_power_kw,
        regime=regime,
    )
    st.dataframe(df_pot.style.format({"horas_ponta": "{:.1f}", "fator_hp": "{:.3f}", "potencia_eur": "{:.2f}"}))

    st.header("4. Resultados agregados")
    total_energia = resumo_periodos["custo_eur"].sum()
    total_potencia = df_pot["potencia_eur"].sum()
    total = total_energia + total_potencia

    col1, col2, col3 = st.columns(3)
    col1.metric("Custo anual de energia", f"{total_energia:,.2f} €")
    col2.metric("Custo anual de potência (ponta)", f"{total_potencia:,.2f} €")
    col3.metric("Total anual", f"{total:,.2f} €")

    st.header("5. Comparação Vigente vs Novo 2027")
    def compute_total_for(reg: TariffRegime) -> tuple[float, float, float]:
        prices = build_tariff_series(
            ano=ano,
            ciclo=ciclo,
            period_tariffs=tariff_inputs.energy_tariffs,
            regime=reg,
        )
        df_e = compute_energy_cost(profile_kw=profile.data["kw"], energy_price_eur_kwh=prices)
        resumo = energy_cost_by_period_and_month(df_e, ano=ano, ciclo=ciclo, regime=reg)
        df_p = power_charge_ponta_by_month(
            ano=ano,
            ciclo=ciclo,
            power_tariff_ponta=tariff_inputs.power_tariff_ponta,
            contracted_power_kw=tariff_inputs.contracted_power_kw,
            regime=reg,
        )
        e = float(resumo["custo_eur"].sum())
        p = float(df_p["potencia_eur"].sum())
        return e, p, e + p

    e_vig, p_vig, t_vig = compute_total_for(TariffRegime.VIGENTE)
    e_new, p_new, t_new = compute_total_for(TariffRegime.NOVO_2027)

    comp = pd.DataFrame(
        [
            {"cenario": "Vigente", "energia_eur": e_vig, "potencia_eur": p_vig, "total_eur": t_vig},
            {"cenario": "Novo 2027", "energia_eur": e_new, "potencia_eur": p_new, "total_eur": t_new},
            {"cenario": "Delta (Novo - Vigente)", "energia_eur": e_new - e_vig, "potencia_eur": p_new - p_vig, "total_eur": t_new - t_vig},
        ]
    )
    st.dataframe(comp.style.format({"energia_eur": "{:.2f}", "potencia_eur": "{:.2f}", "total_eur": "{:.2f}"}))

    st.markdown(
        "Esta é a base de cálculo. Na próxima fase podemos ligar aqui:\n"
        "- Perfis de produção fotovoltaica e algoritmos de otimização de carga/descarga."
    )


if __name__ == "__main__":
    main()

