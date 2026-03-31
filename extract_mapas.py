import json
from pathlib import Path

import pandas as pd


def normalize_text(s: str) -> str:
    return (
        str(s)
        .strip()
        .lower()
        .replace("á", "a")
        .replace("à", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )


def parse_tipo_dia(tipo: str) -> str:
    t = normalize_text(tipo)
    if "todos os dias" in t:
        return "todos_os_dias"
    if "dia util" in t:
        return "dias_uteis"
    if "sabado" in t:
        return "sabado"
    if "domingo" in t or "feriado" in t:
        return "domingo_feriado"
    return t


def parse_ciclo(c: str) -> str:
    c = normalize_text(c)
    if "semanal opcional" in c:
        return "semanal_opcional"
    if "semanal" in c:
        return "semanal"
    if "diario" in c:
        return "diario"
    return c


def parse_epoca(e: str) -> str:
    e = normalize_text(e)
    if "verao" in e:
        return "verao"
    if "inverno" in e:
        return "inverno"
    return "inverno"


def main() -> None:
    path = Path(
        r"g:\O meu disco\Profissional\Legislação Autoconsumo\Alteração períodos horários\calculadora_fatura_bte_mt_at_mat.xlsx"
    )
    df = pd.read_excel(path, sheet_name="mapas")

    # Usar colunas do bloco dt=15min
    cols = {
        "ciclo": "Nome do ciclo.1",
        "hora": "Hora (dt=15min)",
        "epoca": "Época.1",
        "tipo": "Tipo de dia.1",
        "vigente": "Mapa Vigente.1",
        "novo": "Novo Mapa.1",
    }

    sub = df[[v for v in cols.values() if v in df.columns]].copy()
    sub = sub.rename(columns={v: k for k, v in cols.items() if v in sub.columns})
    sub = sub.dropna(subset=["ciclo", "hora", "vigente", "novo"])

    # manter apenas códigos conhecidos
    valid_codes = {"HP", "HC", "HVN", "HSV"}
    sub = sub[sub["vigente"].isin(valid_codes) & sub["novo"].isin(valid_codes)]

    def to_hhmm(v):
        if isinstance(v, str):
            s = v.strip()
            if len(s) >= 5 and s[2] == ":":
                return s[:5]
            return s
        if hasattr(v, "strftime"):
            return v.strftime("%H:%M")
        # excel float for time
        return pd.to_datetime(v).strftime("%H:%M")

    sub["hora"] = sub["hora"].apply(to_hhmm)
    sub["ciclo"] = sub["ciclo"].apply(parse_ciclo)
    sub["epoca"] = sub["epoca"].apply(parse_epoca)
    sub["tipo"] = sub["tipo"].apply(parse_tipo_dia)

    out = {}
    for version in ("vigente", "novo"):
        data = {}
        for _, r in sub.iterrows():
            c = r["ciclo"]
            e = r["epoca"]
            t = r["tipo"]
            h = r["hora"]
            code = r[version]
            data.setdefault(c, {}).setdefault(e, {}).setdefault(t, {})[h] = code
        out[version] = data

    output_path = Path("mapas_tarifarios_15min.json")
    output_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Gerado: {output_path.resolve()}")
    print("Registos:", len(sub))
    print("Ciclos:", sorted(sub["ciclo"].unique()))
    print("Tipos dia:", sorted(sub["tipo"].unique()))


if __name__ == "__main__":
    main()

