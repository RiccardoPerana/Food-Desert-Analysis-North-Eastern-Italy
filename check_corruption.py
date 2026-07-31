import pandas as pd

files = ["./data/comuni veneto.csv", "./data/comuni friuli.csv", "./data/comuni trentino.csv"]

for f in files:
    df = pd.read_csv(f, sep=None, engine="python", encoding="latin1")
    name_col = [c for c in df.columns if "nome" in c.lower() or "denomin" in c.lower()][0]
    suspicious = df[df[name_col].astype(str).str.contains("ï¿½|�", regex=True, na=False)]
    if not suspicious.empty:
        print(f"\n{f}: {len(suspicious)} suspicious row(s)")
        print(suspicious[[name_col]].to_string(index=False))
    else:
        print(f"\n{f}: no corrupted names found.")