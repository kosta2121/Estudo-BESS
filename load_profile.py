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

EXPECTED_INTERVALS_PER_YEAR = 35040


@dataclass
class LoadProfile:
    data: pd.DataFrame  # index datetime, coluna 'kw'
    expected_count: int = EXPECTED_INTERVALS_PER_YEAR
    original_valid_count: int = 0
    filled_missing_count: int = 0


def _normalize_text(s: object) -> str:
    return str(s).strip().lower()


def _coerce_datetime(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


def _find_header_row_in_excel(path: str, sheet_name: Optional[str]) -> Optional[int]:
    """
    Procura, nas primeiras linhas, um cabeçalho típico da E-Redes.
    """
    preview = pd.read_excel(path, sheet_name=sheet_name if sheet_name is not None else 0, header=None, nrows=40)
    for i in range(len(preview)):
        row_vals = [_normalize_text(v) for v in preview.iloc[i].tolist()]
        has_data = any(v == "data" or "data " in v for v in row_vals)
        has_hora = any(v == "hora" or "hora " in v for v in row_vals)
        has_consumo = any("consumo" in v and "kw" in v for v in row_vals)
        has_data_hora = has_data and has_hora
        if has_consumo and has_data_hora:
            return i
    return None


def _read_eredes_excel(path: str, sheet_name: Optional[str]) -> pd.DataFrame:
    header_row = _find_header_row_in_excel(path, sheet_name)
    if header_row is not None:
        return pd.read_excel(
            path,
            sheet_name=sheet_name if sheet_name is not None else 0,
            header=header_row,
        )
    # fallback: leitura simples da primeira folha
    return pd.read_excel(path, sheet_name=sheet_name if sheet_name is not None else 0)


def _infer_date_column(df: pd.DataFrame) -> str:
    for col in df.columns:
        c = _normalize_text(col)
        if "data" in c and "hora" not in c:
            return col
    return df.columns[0]


def _infer_hour_column(df: pd.DataFrame) -> str:
    for col in df.columns:
        c = _normalize_text(col)
        if "hora" in c:
            return col
    # fallback: escolhe a coluna com mais padrões HH:MM
    best_col = None
    best_score = -1.0
    for col in df.columns:
        s = df[col].astype(str).str.strip()
        score = s.str.match(r"^\d{1,2}:\d{2}(:\d{2})?$", na=False).mean()
        if score > best_score:
            best_score = score
            best_col = col
    return best_col if best_col is not None else df.columns[1]


def _infer_power_column(df: pd.DataFrame) -> str:
    for name in EREDES_DEFAULT_COLNAMES:
        if name in df.columns:
            return name
    for col in df.columns:
        c = _normalize_text(col)
        if "consumo" in c and "kw" in c:
            return col

    # fallback inteligente: coluna com mais valores numéricos
    best_col = None
    best_score = -1.0
    for col in df.columns:
        num = pd.to_numeric(df[col], errors="coerce")
        score = num.notna().mean()
        if score > best_score:
            best_score = score
            best_col = col
    if best_col is None:
        return df.columns[-1]
    return best_col


def _parse_combined_datetime(date_series: pd.Series, hour_series: pd.Series) -> pd.Series:
    date_text = date_series.astype(str).str.strip()
    hour_text = hour_series.astype(str).str.strip()
    # normaliza HH:MM(:SS)
    hour_text = hour_text.str.replace(r"^(\d{1}):", r"0\1:", regex=True)
    datehour = date_text + " " + hour_text
    return pd.to_datetime(datehour, errors="coerce", dayfirst=False)


def _infer_analysis_year(datetime_series: pd.Series) -> int:
    anos = datetime_series.dt.year.dropna()
    if anos.empty:
        raise ValueError("Não foi possível inferir o ano de análise a partir da coluna Data/Hora.")
    # Ano dominante (ex.: 2025), mesmo que exista 01/01/2026 00:00 como último ponto.
    return int(anos.value_counts().idxmax())


def _build_sequential_year_index(ano: int, periods: int) -> pd.DatetimeIndex:
    """
    Cria índice sequencial de 15 minutos para o ano de análise.
    Assim, se o último registo vier como 01/01 do ano seguinte às 00:00,
    ele continua a ser tratado como último intervalo do ano analisado.
    """
    return pd.date_range(start=f"{ano}-01-01 00:00:00", periods=periods, freq="15min")


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
        df = _read_eredes_excel(path, sheet_name=sheet_name)

    date_col = _infer_date_column(df)
    hour_col = _infer_hour_column(df)
    power_col = _infer_power_column(df)

    df = df[[date_col, hour_col, power_col]].copy()
    df["datetime"] = _parse_combined_datetime(df[date_col], df[hour_col])
    # vírgula decimal pt-PT
    kw_text = df[power_col].astype(str).str.strip()
    has_comma = kw_text.str.contains(",", na=False)
    # Quando vem em formato pt-PT (ex.: 1.234,56), removemos separador de milhar e trocamos decimal.
    kw_text = kw_text.where(~has_comma, kw_text.str.replace(".", "", regex=False))
    kw_text = kw_text.str.replace(",", ".", regex=False)
    df["kw"] = pd.to_numeric(kw_text, errors="coerce")
    df = df.dropna(subset=["datetime", "kw"]).copy()
    ano_analise = _infer_analysis_year(df["datetime"])

    # Ordena pela datetime do ficheiro, mas o índice final é sequencial do ano de análise.
    df = df.sort_values("datetime")
    df = df[["kw"]].reset_index(drop=True)

    original_valid_count = len(df)
    if original_valid_count > EXPECTED_INTERVALS_PER_YEAR:
        raise ValueError(
            f"Foram encontrados {original_valid_count} valores de consumo; esperado no máximo {EXPECTED_INTERVALS_PER_YEAR}."
        )

    filled_missing_count = 0
    if original_valid_count < EXPECTED_INTERVALS_PER_YEAR:
        filled_missing_count = EXPECTED_INTERVALS_PER_YEAR - original_valid_count
        # Completa o fim da série para garantir 35040 pontos e interpola gaps de NaN.
        df = df.reindex(range(EXPECTED_INTERVALS_PER_YEAR))
        df["kw"] = df["kw"].interpolate(method="linear", limit_direction="both").ffill().bfill()

    df.index = _build_sequential_year_index(ano_analise, EXPECTED_INTERVALS_PER_YEAR)

    return LoadProfile(
        data=df,
        original_valid_count=original_valid_count,
        filled_missing_count=filled_missing_count,
    )


def infer_year(profile: LoadProfile) -> int:
    idx: pd.DatetimeIndex = profile.data.index
    anos_unicos = idx.year.unique().tolist()
    if len(anos_unicos) != 1:
        raise ValueError(f"Não foi possível inferir o ano de análise. Anos encontrados: {anos_unicos}")
    return int(anos_unicos[0])

