from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd


EREDES_DEFAULT_COLNAMES = [
    "Consumo registado, Ativa (kW)",
    "Consumo registado, ativa (kW)",
    "Consumo registado, Ativa (kW) ",
]


@dataclass
class LoadProfile:
    data: pd.DataFrame  # index datetime, coluna 'kw'


def _infer_datetime_column(df: pd.DataFrame) -> str:
    for col in df.columns:
        if "data" in col.lower() or "hora" in col.lower():
            return col
    # fallback: first column
    return df.columns[0]


def _infer_power_column(df: pd.DataFrame) -> str:
    for name in EREDES_DEFAULT_COLNAMES:
        if name in df.columns:
            return name
    for col in df.columns:
        if "consumo" in col.lower() and "kW".lower().replace("w", "w") in col:
            return col
    # fallback: last column
    return df.columns[-1]


def read_eredes_file(path: str, sheet_name: Optional[str] = None) -> LoadProfile:
    """
    Lê um ficheiro exportado da E-Redes (Excel ou CSV) e devolve uma série com:
      - índice: datetime
      - coluna: 'kw' (potência média por intervalo, kW)
    Assume um ano completo com 35 040 registos de 15 em 15 minutos.
    """
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path, sep=";", decimal=",")
    else:
        df = pd.read_excel(path, sheet_name=sheet_name)

    datetime_col = _infer_datetime_column(df)
    power_col = _infer_power_column(df)

    df = df[[datetime_col, power_col]].copy()
    df[datetime_col] = pd.to_datetime(df[datetime_col])
    df = df.set_index(datetime_col)
    df = df.sort_index()

    df.rename(columns={power_col: "kw"}, inplace=True)

    # Garantir frequência de 15 min, se possível
    df = df.asfreq("15min")

    return LoadProfile(data=df)


def infer_year(profile: LoadProfile) -> int:
    idx: pd.DatetimeIndex = profile.data.index
    anos = idx.year.unique()
    if len(anos) != 1:
        raise ValueError(f"Esperava um único ano no ficheiro, obtido {list(anos)}")
    return int(anos[0])

