import json
import os
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
VALID_CODES = {"HP", "HC", "HVN", "HSV"}


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return text.strip().lower()


def parse_tipo_dia(value: str) -> str:
    text = normalize_text(value)
    if "todos os dias" in text:
        return "todos_os_dias"
    if "fim-de-semana" in text:
        return "fim_de_semana"
    if "dia util" in text:
        return "dias_uteis"
    if "sabado" in text:
        return "sabado"
    if "domingo" in text or "feriado" in text:
        return "domingo_feriado"
    return text


def parse_ciclo(value: str) -> str:
    text = normalize_text(value)
    if "semanal opcional" in text:
        return "semanal_opcional"
    if "ciclo diario" in text:
        return "diario"
    if "ciclo semanal epocas" in text and "norte" in text:
        return "semanal_epocas_norte"
    if "ciclo semanal epocas" in text and "centro" in text:
        return "semanal_epocas_centro"
    if "ciclo semanal epocas" in text and "sul" in text:
        return "semanal_epocas_sul"
    if "ciclo semanal" in text:
        return "semanal"
    return text


def parse_estacao(hora_legal: str, epoca: str) -> str:
    legal = normalize_text(hora_legal)
    if legal in {"inverno", "verao"}:
        return legal

    season = normalize_text(epoca)
    if season in {"inverno", "verao"}:
        return season
    if season in {"alta", "media", "baixa"}:
        return season
    return "inverno"


def excel_time_to_hhmm(raw: str) -> str:
    text = str(raw).strip()
    if ":" in text:
        return text[:5]

    value = float(text)
    total_minutes = round(value * 24 * 60)
    hours = (total_minutes // 60) % 24
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def find_workbook() -> Path:
    preferred = Path(
        r"g:\O meu disco\Profissional\Legislação Autoconsumo\Alteração períodos horários\todos os ciclos.xlsx"
    )
    if preferred.exists():
        return preferred

    base = Path(r"g:\O meu disco\Profissional")
    for root, _, files in os.walk(base):
        for name in files:
            if name.lower() == "todos os ciclos.xlsx":
                return Path(root) / name

    raise FileNotFoundError("Nao foi encontrado o ficheiro 'todos os ciclos.xlsx'.")


def load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []

    shared = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings = []
    for si in shared.findall("a:si", NS):
        strings.append("".join((node.text or "") for node in si.iterfind(".//a:t", NS)))
    return strings


def iter_sheet_rows(path: Path):
    with zipfile.ZipFile(path) as zf:
        shared_strings = load_shared_strings(zf)
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        sheet_data = sheet.find("a:sheetData", NS)
        if sheet_data is None:
            return

        for row in sheet_data:
            record = {}
            for cell in row.findall("a:c", NS):
                ref = "".join(ch for ch in cell.attrib["r"] if ch.isalpha())
                cell_type = cell.attrib.get("t")
                value_node = cell.find("a:v", NS)
                if value_node is None:
                    continue
                raw = value_node.text or ""
                if cell_type == "s":
                    record[ref] = shared_strings[int(raw)]
                else:
                    record[ref] = raw
            if record:
                yield record


def extract_maps(path: Path) -> dict:
    rows = list(iter_sheet_rows(path))
    if not rows:
        raise RuntimeError("Nao foi possivel ler linhas do Excel.")

    result = {"vigente": {}, "novo": {}}
    for row in rows[1:]:
        if not {"A", "B", "F", "G"} <= set(row.keys()):
            continue

        vigente = row["F"]
        novo = row["G"]
        if vigente not in VALID_CODES or novo not in VALID_CODES:
            continue

        ciclo = parse_ciclo(row.get("A", ""))
        hora = excel_time_to_hhmm(row.get("B", ""))
        estacao = parse_estacao(row.get("C", ""), row.get("D", ""))
        tipo = parse_tipo_dia(row.get("E", ""))

        result["vigente"].setdefault(ciclo, {}).setdefault(estacao, {}).setdefault(tipo, {})[hora] = vigente
        result["novo"].setdefault(ciclo, {}).setdefault(estacao, {}).setdefault(tipo, {})[hora] = novo

    return result


def main() -> None:
    workbook_path = find_workbook()
    maps = extract_maps(workbook_path)

    output_path = Path("mapas_tarifarios_15min_v2.json")
    output_path.write_text(json.dumps(maps, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Workbook: {workbook_path}")
    print(f"Gerado: {output_path.resolve()}")
    for regime, cycles in maps.items():
        print("REGIME", regime)
        for ciclo, seasons in sorted(cycles.items()):
            print(" ", ciclo, sorted(seasons.keys()))


if __name__ == "__main__":
    main()
