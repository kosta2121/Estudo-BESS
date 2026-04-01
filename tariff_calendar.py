from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from enum import Enum
from typing import List, Tuple, Dict
import json
from pathlib import Path
from functools import lru_cache

import holidays


class TariffCycle(str, Enum):
    SEMANAL = "semanal"
    SEMANAL_OPCIONAL = "semanal_opcional"
    DIARIO = "diario"


class TariffRegime(str, Enum):
    VIGENTE = "vigente"
    NOVO_2027 = "novo"


class Periodo(str, Enum):
    PONTA = "ponta"
    CHEIAS = "cheias"
    VAZIO = "vazio"
    SUPER_VAZIO = "super_vazio"


@dataclass(frozen=True)
class IntervaloHorario:
    inicio: time
    fim: time
    periodo: Periodo


def _parse_hora(texto: str) -> time:
    h, m = map(int, texto.split(":"))
    if h == 24 and m == 0:
        return time(hour=23, minute=59, second=59, microsecond=999999)
    return time(hour=h, minute=m)


def _intervalo(hora_ini: str, hora_fim: str, periodo: Periodo) -> IntervaloHorario:
    return IntervaloHorario(_parse_hora(hora_ini), _parse_hora(hora_fim), periodo)


@lru_cache(maxsize=16)
def _obter_feriados_pt(ano: int) -> holidays.HolidayBase:
    """Feriados nacionais principais em Portugal."""
    return holidays.country_holidays("PT", years=[ano])


def _eh_verao(d: date) -> bool:
    """Verão tarifário: abril a outubro incluídos."""
    return 4 <= d.month <= 10


def _tipo_dia(d: date, feriados: holidays.HolidayBase) -> str:
    if d in feriados:
        return "domingo"
    if d.weekday() >= 5:
        # 5 = sábado, 6 = domingo
        if d.weekday() == 5:
            return "sabado"
        return "domingo"
    return "dias_uteis"


def _construir_tabela_intervalos() -> Dict[Tuple[TariffCycle, str, str], List[IntervaloHorario]]:
    """
    Tabela estática dos ciclos atuais, diretamente baseada no ficheiro
    'ciclos horários.txt' fornecido.
    Chave: (ciclo, estacao, tipo_dia)
      - estacao: 'inverno' ou 'verao'
      - tipo_dia: 'dias_uteis' | 'sabado' | 'domingo'
    """
    I = Periodo

    tabela: Dict[Tuple[TariffCycle, str, str], List[IntervaloHorario]] = {}

    # --- CICLO SEMANAL, HORÁRIO DE INVERNO ---
    tabela[(TariffCycle.SEMANAL, "inverno", "dias_uteis")] = [
        _intervalo("00:00", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "07:00", I.VAZIO),
        _intervalo("07:00", "09:30", I.CHEIAS),
        _intervalo("09:30", "12:00", I.PONTA),
        _intervalo("12:00", "18:30", I.CHEIAS),
        _intervalo("18:30", "21:00", I.PONTA),
        _intervalo("21:00", "24:00", I.CHEIAS),
    ]
    tabela[(TariffCycle.SEMANAL, "inverno", "sabado")] = [
        _intervalo("00:00", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "09:30", I.VAZIO),
        _intervalo("09:30", "13:00", I.CHEIAS),
        _intervalo("13:00", "18:30", I.VAZIO),
        _intervalo("18:30", "22:00", I.CHEIAS),
        _intervalo("22:00", "24:00", I.VAZIO),
    ]
    tabela[(TariffCycle.SEMANAL, "inverno", "domingo")] = [
        _intervalo("00:00", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "24:00", I.VAZIO),
    ]

    # --- CICLO SEMANAL OPCIONAL, HORÁRIO DE INVERNO ---
    tabela[(TariffCycle.SEMANAL_OPCIONAL, "inverno", "dias_uteis")] = [
        _intervalo("00:00", "00:30", I.CHEIAS),
        _intervalo("00:30", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "07:30", I.VAZIO),
        _intervalo("07:30", "17:00", I.CHEIAS),
        _intervalo("17:00", "22:00", I.PONTA),
        _intervalo("22:00", "24:00", I.CHEIAS),
    ]
    tabela[(TariffCycle.SEMANAL_OPCIONAL, "inverno", "sabado")] = [
        _intervalo("00:00", "03:00", I.VAZIO),
        _intervalo("03:00", "07:00", I.SUPER_VAZIO),
        _intervalo("07:00", "10:30", I.VAZIO),
        _intervalo("10:30", "12:30", I.CHEIAS),
        _intervalo("12:30", "17:30", I.VAZIO),
        _intervalo("17:30", "22:30", I.CHEIAS),
        _intervalo("22:30", "24:00", I.VAZIO),
    ]
    tabela[(TariffCycle.SEMANAL_OPCIONAL, "inverno", "domingo")] = [
        _intervalo("00:00", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "24:00", I.VAZIO),
    ]

    # --- CICLO DIÁRIO, HORÁRIO DE INVERNO ---
    tabela[(TariffCycle.DIARIO, "inverno", "dias_uteis")] = [
        _intervalo("00:00", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "08:00", I.VAZIO),
        _intervalo("08:00", "09:00", I.CHEIAS),
        _intervalo("09:00", "10:30", I.PONTA),
        _intervalo("10:30", "18:00", I.CHEIAS),
        _intervalo("18:00", "20:30", I.PONTA),
        _intervalo("20:30", "22:00", I.CHEIAS),
        _intervalo("22:00", "24:00", I.VAZIO),
    ]
    # No documento, sábado e domingo de inverno/diário são iguais aos dias úteis
    tabela[(TariffCycle.DIARIO, "inverno", "sabado")] = tabela[
        (TariffCycle.DIARIO, "inverno", "dias_uteis")
    ]
    tabela[(TariffCycle.DIARIO, "inverno", "domingo")] = tabela[
        (TariffCycle.DIARIO, "inverno", "dias_uteis")
    ]

    # --- CICLO DIÁRIO, HORÁRIO DE VERÃO ---
    tabela[(TariffCycle.DIARIO, "verao", "dias_uteis")] = [
        _intervalo("00:00", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "08:00", I.VAZIO),
        _intervalo("08:00", "10:30", I.CHEIAS),
        _intervalo("10:30", "13:00", I.PONTA),
        _intervalo("13:00", "19:30", I.CHEIAS),
        _intervalo("19:30", "21:00", I.PONTA),
        _intervalo("21:00", "22:00", I.CHEIAS),
        _intervalo("22:00", "24:00", I.VAZIO),
    ]
    tabela[(TariffCycle.DIARIO, "verao", "sabado")] = tabela[
        (TariffCycle.DIARIO, "verao", "dias_uteis")
    ]
    tabela[(TariffCycle.DIARIO, "verao", "domingo")] = tabela[
        (TariffCycle.DIARIO, "verao", "dias_uteis")
    ]

    # --- CICLO SEMANAL, HORÁRIO DE VERÃO ---
    tabela[(TariffCycle.SEMANAL, "verao", "dias_uteis")] = [
        _intervalo("00:00", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "07:00", I.VAZIO),
        _intervalo("07:00", "09:15", I.CHEIAS),
        _intervalo("09:15", "12:15", I.PONTA),
        _intervalo("12:15", "24:00", I.CHEIAS),
    ]
    tabela[(TariffCycle.SEMANAL, "verao", "sabado")] = [
        _intervalo("00:00", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "09:00", I.VAZIO),
        _intervalo("09:00", "14:00", I.CHEIAS),
        _intervalo("14:00", "20:00", I.VAZIO),
        _intervalo("20:00", "22:00", I.CHEIAS),
        _intervalo("22:00", "24:00", I.VAZIO),
    ]
    tabela[(TariffCycle.SEMANAL, "verao", "domingo")] = [
        _intervalo("00:00", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "24:00", I.VAZIO),
    ]

    # --- CICLO SEMANAL OPCIONAL, HORÁRIO DE VERÃO ---
    tabela[(TariffCycle.SEMANAL_OPCIONAL, "verao", "dias_uteis")] = [
        _intervalo("00:00", "00:30", I.CHEIAS),
        _intervalo("00:30", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "07:30", I.VAZIO),
        _intervalo("07:30", "14:00", I.CHEIAS),
        _intervalo("14:00", "17:00", I.PONTA),
        _intervalo("17:00", "24:00", I.CHEIAS),
    ]
    tabela[(TariffCycle.SEMANAL_OPCIONAL, "verao", "sabado")] = [
        _intervalo("00:00", "03:30", I.VAZIO),
        _intervalo("03:30", "07:30", I.SUPER_VAZIO),
        _intervalo("07:30", "10:00", I.VAZIO),
        _intervalo("10:00", "13:30", I.CHEIAS),
        _intervalo("13:30", "19:30", I.VAZIO),
        _intervalo("19:30", "23:00", I.CHEIAS),
        _intervalo("23:00", "24:00", I.VAZIO),
    ]
    tabela[(TariffCycle.SEMANAL_OPCIONAL, "verao", "domingo")] = [
        _intervalo("00:00", "02:00", I.VAZIO),
        _intervalo("02:00", "06:00", I.SUPER_VAZIO),
        _intervalo("06:00", "24:00", I.VAZIO),
    ]

    return tabela


_TABELA_INTERVALOS = _construir_tabela_intervalos()

_CODE_TO_PERIODO: Dict[str, Periodo] = {
    "HP": Periodo.PONTA,
    "HC": Periodo.CHEIAS,
    "HVN": Periodo.VAZIO,
    "HSV": Periodo.SUPER_VAZIO,
}


def _carregar_mapa_15min() -> Dict[str, Dict]:
    try:
        from extract_mapas_xlsx import extract_maps, find_workbook

        workbook_path = find_workbook()
        return extract_maps(workbook_path)
    except Exception:
        pass

    candidate_names = [
        "mapas_tarifarios_15min_v2.json",
        "mapas_tarifarios_15min.json",
    ]
    for name in candidate_names:
        path = Path(__file__).with_name(name)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


_MAPA_15MIN = _carregar_mapa_15min()


def _obter_code_de_mapa(
    timestamp: datetime,
    ciclo: TariffCycle,
    regime: TariffRegime,
) -> str | None:
    """Obtém código HP/HC/HVN/HSV do mapa extraído do Excel, quando disponível."""
    if not _MAPA_15MIN:
        return None

    d = timestamp.date()
    estacao = "verao" if _eh_verao(d) else "inverno"
    feriados = _obter_feriados_pt(d.year)
    tipo = _tipo_dia(d, feriados)

    tipo_mapa = "dias_uteis"
    if tipo == "sabado":
        tipo_mapa = "sabado"
    elif tipo == "domingo":
        tipo_mapa = "domingo_feriado"

    ciclo_key = ciclo.value
    regime_key = regime.value
    hora_key = timestamp.strftime("%H:%M")

    bloco = _MAPA_15MIN.get(regime_key, {}).get(ciclo_key, {}).get(estacao, {})
    if not bloco:
        return None

    if tipo_mapa in bloco and hora_key in bloco[tipo_mapa]:
        return bloco[tipo_mapa][hora_key]
    if "fim_de_semana" in bloco and tipo_mapa in {"sabado", "domingo_feriado"} and hora_key in bloco["fim_de_semana"]:
        return bloco["fim_de_semana"][hora_key]
    if "fim-de-semana" in bloco and tipo_mapa in {"sabado", "domingo_feriado"} and hora_key in bloco["fim-de-semana"]:
        return bloco["fim-de-semana"][hora_key]
    if "todos_os_dias" in bloco and hora_key in bloco["todos_os_dias"]:
        return bloco["todos_os_dias"][hora_key]
    return None


def obter_periodo(timestamp: datetime, ciclo: TariffCycle, regime: TariffRegime = TariffRegime.VIGENTE) -> Periodo:
    """Devolve o período horário (ponta/cheias/vazio/super_vazio) para um dado instante."""
    code = _obter_code_de_mapa(timestamp, ciclo, regime)
    if code in _CODE_TO_PERIODO:
        return _CODE_TO_PERIODO[code]

    # Fallback para tabela estática (caso não exista o json extraído do Excel)
    d = timestamp.date()
    estacao = "verao" if _eh_verao(d) else "inverno"
    feriados = _obter_feriados_pt(d.year)
    tipo = _tipo_dia(d, feriados)

    chave = (ciclo, estacao, tipo)
    try:
        intervalos = _TABELA_INTERVALOS[chave]
    except KeyError:
        raise ValueError(f"Não há definição de intervalos para {chave}")

    t = timestamp.time()
    for intervalo in intervalos:
        if intervalo.inicio <= t < intervalo.fim:
            return intervalo.periodo

    # Segurança: se nada corresponder, algo está incoerente na tabela.
    raise RuntimeError(f"Nenhum período encontrado para {timestamp=}, {ciclo=}")


@lru_cache(maxsize=64)
def gerar_mapa_periodos_ano(
    ano: int,
    ciclo: TariffCycle,
    passo_minutos: int = 15,
    regime: TariffRegime = TariffRegime.VIGENTE,
) -> Dict[datetime, Periodo]:
    """
    Gera um dicionário {timestamp: Periodo} para o ano completo, com resolução de 15 min.
    Útil para cruzar diretamente com a série de consumo da E-Redes.
    """
    inicio = datetime(ano, 1, 1, 0, 0)
    fim = datetime(ano + 1, 1, 1, 0, 0)
    delta = timedelta(minutes=passo_minutos)

    atual = inicio
    mapa: Dict[datetime, Periodo] = {}
    while atual < fim:
        mapa[atual] = obter_periodo(atual, ciclo, regime=regime)
        atual += delta
    return mapa


@lru_cache(maxsize=64)
def horas_ponta_por_mes(
    ano: int,
    ciclo: TariffCycle,
    passo_minutos: int = 60,
    regime: TariffRegime = TariffRegime.VIGENTE,
) -> Dict[int, float]:
    """
    Calcula o número de horas de ponta em cada mês.
    Por defeito usa passo de 60 minutos (1h) para ficar alinhado com o conceito de
    'horas de ponta' referido na tarifa de potência.
    """
    inicio = datetime(ano, 1, 1, 0, 0)
    fim = datetime(ano + 1, 1, 1, 0, 0)
    delta = timedelta(minutes=passo_minutos)

    resultado: Dict[int, float] = {m: 0.0 for m in range(1, 13)}

    atual = inicio
    while atual < fim:
        periodo_atual = obter_periodo(atual, ciclo, regime=regime)

        if periodo_atual == Periodo.PONTA:
            resultado[atual.month] += passo_minutos / 60.0

        atual += delta

    return resultado
