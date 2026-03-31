from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal

import numpy as np
import pandas as pd

from tariff_calendar import TariffCycle, TariffRegime, Periodo, gerar_mapa_periodos_ano, horas_ponta_por_mes


PeriodoNome = Literal["ponta", "cheias", "vazio", "super_vazio"]


@dataclass
class PeriodTariff:
    preco_comercializador: float  # €/kWh
    tar_acesso_redes: float  # €/kWh
    fin_tarifa_social: float  # €/kWh

    @property
    def total_eur_kwh(self) -> float:
        return self.preco_comercializador + self.tar_acesso_redes + self.fin_tarifa_social


@dataclass
class PowerTariffPonta:
    """Tarifa de potência em horas de ponta (€/kW dia)."""

    eur_kw_dia: float


def build_tariff_series(
    ano: int,
    ciclo: TariffCycle,
    period_tariffs: Dict[Periodo, PeriodTariff],
    regime: TariffRegime = TariffRegime.VIGENTE,
) -> pd.Series:
    """
    Devolve uma série com o preço total de energia (€/kWh) para cada intervalo de 15 min do ano.
    """
    mapa = gerar_mapa_periodos_ano(ano, ciclo, passo_minutos=15, regime=regime)
    idx = pd.DatetimeIndex(sorted(mapa.keys()))
    periodos = pd.Series([mapa[t] for t in idx], index=idx, dtype="object")

    preco = periodos.map(lambda p: period_tariffs[p].total_eur_kwh)
    preco.name = "eur_kwh"
    return preco


def compute_energy_cost(
    profile_kw: pd.Series,
    energy_price_eur_kwh: pd.Series,
) -> pd.DataFrame:
    """
    Calcula custos de energia por período e por mês.
    profile_kw: série com kW médios a cada 15 min.
    energy_price_eur_kwh: série alinhada com o mesmo índice, em €/kWh.
    """
    if not profile_kw.index.equals(energy_price_eur_kwh.index):
        energy_price_eur_kwh = energy_price_eur_kwh.reindex(profile_kw.index)

    # Energia em kWh por intervalo de 15min
    energia_kwh = profile_kw * (15.0 / 60.0)
    custo_eur = energia_kwh * energy_price_eur_kwh

    df = pd.DataFrame(
        {
            "kw": profile_kw,
            "kwh": energia_kwh,
            "preco_eur_kwh": energy_price_eur_kwh,
            "custo_eur": custo_eur,
        }
    )
    df["mes"] = df.index.month
    return df


def energy_cost_by_period_and_month(
    df: pd.DataFrame,
    ano: int,
    ciclo: TariffCycle,
    regime: TariffRegime = TariffRegime.VIGENTE,
) -> pd.DataFrame:
    """Agrega custos por mês e período horário."""
    mapa = gerar_mapa_periodos_ano(ano, ciclo, passo_minutos=15, regime=regime)
    periodos = pd.Series(mapa)
    periodos = periodos.reindex(df.index)

    df = df.copy()
    df["periodo"] = periodos.values
    df["mes"] = df.index.month

    resumo = (
        df.groupby(["mes", "periodo"])
        .agg(kwh=("kwh", "sum"), custo_eur=("custo_eur", "sum"))
        .reset_index()
    )
    return resumo


def power_charge_ponta_by_month(
    ano: int,
    ciclo: TariffCycle,
    power_tariff_ponta: PowerTariffPonta,
    contracted_power_kw: float,
    regime: TariffRegime = TariffRegime.VIGENTE,
) -> pd.DataFrame:
    """
    Calcula, para cada mês:
      - número de horas de ponta
      - fator (horas_ponta / dias_no_mes)
      - parcela mensal de potência: fator * tarifa(€/kW dia) * potência contratada (kW)
    """
    from calendar import monthrange

    horas_ponta = horas_ponta_por_mes(ano, ciclo, passo_minutos=60, regime=regime)

    resultados = []
    for mes in range(1, 13):
        dias_no_mes = monthrange(ano, mes)[1]
        hp = horas_ponta[mes]
        fator = hp / dias_no_mes if dias_no_mes > 0 else np.nan
        parcela_eur = fator * power_tariff_ponta.eur_kw_dia * contracted_power_kw
        resultados.append(
            {
                "mes": mes,
                "horas_ponta": hp,
                "dias_no_mes": dias_no_mes,
                "fator_hp": fator,
                "potencia_eur": parcela_eur,
            }
        )

    return pd.DataFrame(resultados)

