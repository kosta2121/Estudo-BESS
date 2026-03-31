import pandas as pd
from pathlib import Path


def main() -> None:
    path = Path(
        r"g:\O meu disco\Profissional\Legislação Autoconsumo\Alteração períodos horários\calculadora_fatura_bte_mt_at_mat.xlsx"
    )
    xls = pd.ExcelFile(path)
    print("SHEETS:", xls.sheet_names)

    sheet_name = None
    for s in xls.sheet_names:
        if "mapa" in s.lower():
            sheet_name = s
            break
    if sheet_name is None:
        sheet_name = xls.sheet_names[0]
    print("USING_SHEET:", sheet_name)

    df = pd.read_excel(path, sheet_name=sheet_name)
    with pd.option_context("display.max_rows", 200, "display.max_columns", 40, "display.width", 260):
        print(df.head(120).to_string(index=False))


if __name__ == "__main__":
    main()

