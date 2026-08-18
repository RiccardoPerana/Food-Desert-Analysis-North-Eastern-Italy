"""
population_istat.py
-------------------
Reads official ISTAT demographic data and matches it to towns by name.

--- HOW IT READS THE SOURCE DATA ---------------------------------------------
ISTAT's published .xlsx workbooks are read unmodified, exactly as downloaded.
No manual conversion step sits between the published data and the analysis, so
anyone can download the same files and regenerate the same numbers -- which is
what makes the demographic half of this project verifiable.

Three sheets are used:

  Tavola A1  header row 3, data from row 4
             A = province, B = ISTAT code, C = town name,
             J = total resident population at 31 December

  Tavola A3  header rows 3-4, data from row 5
             Five-year age brackets in columns E..Y, total in Z.
             Columns R..Y are 65-69, 70-74, 75-79, 80-84, 85-89, 90-94,
             95-99 and 100+ -- summed to give the 65+ population.

  Tavola A4  header rows 3-4, data from row 5
             F = "Indice di vecchiaia" (aging index) for 2024. This is
             ISTAT's own ratio of over-65s to under-15s, per 100. A value of
             272 means 272 residents aged 65+ for every 100 aged under 15.

The three sheets are joined on CODICE COMUNE (column B), a unique and stable
ISTAT identifier, rather than on town names. Names are not unique: Italy has
several distinct municipalities sharing one name across different provinces,
and more still that collide once accents and punctuation are normalised away
(there are two separate "San Gregorio nelle Alpi"). A name-keyed join has to
silently discard one of each pair. Codes do not collide.

Names are used only for the final match against OpenStreetMap, which carries
no ISTAT code -- one boundary where there is no alternative.

NOTE ON TRENTINO-ALTO ADIGE: ISTAT publishes this region as two separate
workbooks, one per autonomous province (Trento and Bolzano/Bozen). Both are
listed in config.ISTAT_POPULATION_XLSX and are combined automatically.
"""

import re
import unicodedata

import pandas as pd

from . import config

# --- Sheet layout (rows numbered as they appear in Excel, 1-indexed) -------
SHEET_POPULATION = "Tavola A1"
SHEET_AGE = "Tavola A3"
SHEET_INDICATORS = "Tavola A4"

POPULATION_FIRST_DATA_ROW = 4
AGE_FIRST_DATA_ROW = 5
INDICATORS_FIRST_DATA_ROW = 5

# --- Column positions (0-indexed, as pandas sees them) --------------------
COL_PROVINCE = 0            # A
COL_ISTAT_CODE = 1          # B
COL_NAME = 2                # C
COL_TOTAL_POPULATION = 9    # J, in Tavola A1

# Tavola A3: R..Y inclusive are the 65+ five-year brackets; Z is the total.
COL_AGE_65_START = 17       # R  (65-69)
COL_AGE_65_END = 24         # Y  (100 e piu), inclusive
COL_AGE_TOTAL = 25          # Z

COL_AGING_INDEX = 5         # F, in Tavola A4 (the 2024 column)


def _normalize_name(name):
    """
    Normalises town names for reliable joining: strips accents, casing and
    punctuation. "Arsie" and "Arsie'" both reduce to the same key.
    """
    if not isinstance(name, str):
        # Blank or NaN cells (stray footer rows) arrive as float.
        return ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]", "", name.lower().strip())


def _read_sheet(path, sheet_name, first_data_row):
    """
    Reads one sheet with no header interpretation and trims it to the real data.

    ISTAT workbooks carry explanatory footers below the data (notes on
    statistical adjustment), and Excel reports a max_row far past the last
    populated cell because of trailing formatting. Both are handled by cutting
    at the first row with no town name.
    """
    df = pd.read_excel(path, sheet_name=sheet_name, header=None,
                       skiprows=first_data_row - 1, engine="openpyxl")

    names = df.iloc[:, COL_NAME]
    blank = names.isna() | (names.astype(str).str.strip() == "")
    if blank.any():
        first_blank = blank[blank].index[0]
        df = df.loc[: first_blank - 1] if first_blank > 0 else df.iloc[0:0]

    return df.reset_index(drop=True)


def _clean_code(value):
    """
    ISTAT codes are zero-padded six-character strings ("025001"). Excel
    sometimes returns them as integers, dropping the leading zero, so they are
    normalised to one consistent form.
    """
    if pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6)


def _load_single_workbook(path):
    """
    Loads one ISTAT regional workbook, returning one row per town.

    Columns: istat_code, name, province, population, population_65plus,
             pct_65plus, aging_index
    """
    pop_df = _read_sheet(path, SHEET_POPULATION, POPULATION_FIRST_DATA_ROW)
    age_df = _read_sheet(path, SHEET_AGE, AGE_FIRST_DATA_ROW)
    ind_df = _read_sheet(path, SHEET_INDICATORS, INDICATORS_FIRST_DATA_ROW)

    base = pd.DataFrame({
        "istat_code": pop_df.iloc[:, COL_ISTAT_CODE].map(_clean_code),
        "name": pop_df.iloc[:, COL_NAME].astype(str).str.strip(),
        "province": pop_df.iloc[:, COL_PROVINCE].astype(str).str.strip(),
        "population": pd.to_numeric(
            pop_df.iloc[:, COL_TOTAL_POPULATION], errors="coerce"
        ).astype("Int64"),
    })

    # --- 65+ population, summed across the eight senior brackets ----------
    senior = age_df.iloc[:, COL_AGE_65_START:COL_AGE_65_END + 1]
    senior = senior.apply(pd.to_numeric, errors="coerce")
    age = pd.DataFrame({
        "istat_code": age_df.iloc[:, COL_ISTAT_CODE].map(_clean_code),
        "population_65plus": senior.sum(axis=1, min_count=1).astype("Int64"),
        "_age_sheet_total": pd.to_numeric(
            age_df.iloc[:, COL_AGE_TOTAL], errors="coerce"
        ).astype("Int64"),
    })

    indicators = pd.DataFrame({
        "istat_code": ind_df.iloc[:, COL_ISTAT_CODE].map(_clean_code),
        "aging_index": pd.to_numeric(ind_df.iloc[:, COL_AGING_INDEX], errors="coerce"),
    })

    # Joined on the ISTAT code, not the name -- see the module docstring.
    df = base.merge(age, on="istat_code", how="left")
    df = df.merge(indicators, on="istat_code", how="left")

    # --- Cross-check the two sheets against each other -------------------
    # Tavola A1's total and Tavola A3's total describe the same quantity from
    # different tables. A mismatch means the column positions assumed above no
    # longer hold -- exactly the kind of breakage that would otherwise produce
    # plausible-looking but wrong numbers.
    both = df["population"].notna() & df["_age_sheet_total"].notna()
    mismatched = int((df.loc[both, "population"] != df.loc[both, "_age_sheet_total"]).sum())
    if mismatched:
        print(f"[WARN] {mismatched} rows in {path.name}: the total in "
              f"'{SHEET_POPULATION}' disagrees with the age-table total in "
              f"'{SHEET_AGE}'. The sheet layout may have changed -- verify the "
              f"column positions at the top of population_istat.py.")
    df = df.drop(columns=["_age_sheet_total"])

    df["pct_65plus"] = (
        df["population_65plus"].astype("Float64")
        / df["population"].astype("Float64") * 100
    ).round(1)

    print(f"[INFO] Loaded {len(df)} towns from {path.name} "
          f"(median {df['pct_65plus'].median():.1f}% aged 65+).")
    return df


def load_population_table():
    """Loads and combines every workbook in config.ISTAT_POPULATION_XLSX."""
    all_dfs = []
    for path in config.ISTAT_POPULATION_XLSX:
        try:
            all_dfs.append(_load_single_workbook(path))
        except FileNotFoundError:
            print(f"[WARN] ISTAT workbook not found, skipping: {path}")
        except Exception as e:
            print(f"[WARN] Failed to load {path}: {type(e).__name__}: {e}")

    if not all_dfs:
        raise RuntimeError(
            "No ISTAT workbooks could be loaded. Check "
            "config.ISTAT_POPULATION_XLSX and confirm the files exist. "
            "Run `python run.py paths` to see where they are expected."
        )

    df = pd.concat(all_dfs, ignore_index=True)

    # Duplicate ISTAT codes across workbooks would mean one town appearing
    # twice, which should never happen -- the regional files partition the
    # country. Surfaced loudly if it ever does.
    dup_codes = df["istat_code"].duplicated(keep=False)
    if dup_codes.any():
        print(f"[WARN] {int(dup_codes.sum())} rows share an ISTAT code across "
              f"workbooks: {sorted(df.loc[dup_codes, 'name'].tolist())}. "
              f"Keeping the first of each.")
        df = df.drop_duplicates(subset="istat_code", keep="first")

    df["_join_key"] = df["name"].apply(_normalize_name)

    total_pop = int(df["population"].sum())
    total_65 = int(df["population_65plus"].sum())
    print(f"[INFO] Combined ISTAT table: {len(df)} towns across "
          f"{len(all_dfs)} workbook(s). Total population {total_pop:,}, of whom "
          f"{total_65:,} ({100 * total_65 / total_pop:.1f}%) are 65 or over.")
    return df


def _generate_name_candidates(name):
    """
    Generates candidate names to try when matching OSM names against ISTAT.

    Bilingual names are common in Trentino-Alto Adige (German - Italian, e.g.
    "Meran - Merano"; the language order is NOT consistent -- "Bolzano - Bozen"
    is Italian-first) and in Friuli-Venezia Giulia (Italian / Friulian or
    Slovenian, e.g. "Udine / Udin"). ISTAT records only the plain Italian name,
    while OSM's `name` tag often combines both languages into one string, so
    every segment is tried rather than just one.
    """
    candidates = [name]

    for sep in [" - ", "-", " / ", "/"]:
        if sep in name:
            candidates.extend(part.strip() for part in name.split(sep))

    # ISTAT and OSM disagree about Italian connective particles in place names.
    # OSM records "Vodo di Cadore"; ISTAT records "Vodo Cadore". Since
    # _normalize_name() strips punctuation but not words, those reduce to
    # "vododicadore" and "vodocadore" -- close, but not equal, so the town was
    # dropped as unmatched despite being a real municipality.
    #
    # Removing these particles is safe because they are never a whole name and
    # never the distinguishing part of one: no two Italian municipalities
    # differ only by a "di" or a "sul".
    particles = {"di", "in", "sul", "sulla", "sui", "del", "della", "dei",
                 "delle", "dello", "da", "al", "alla", "a", "d"}
    for base in list(candidates):
        words = [w for w in base.split() if w.lower().strip("'") not in particles]
        if words and len(words) != len(base.split()):
            candidates.append(" ".join(words))

    # De-duplicated, order preserved, so the exact name is always tried first.
    return list(dict.fromkeys(candidates))


def attach_population(towns_gdf):
    """
    Adds demographic columns to the town GeoDataFrame by matching names against
    the ISTAT table.

    Adds: population, province, population_65plus, pct_65plus, aging_index

    Towns that fail to match after every bilingual candidate has been tried are
    listed. In this study area an unmatched town is usually NOT a data problem
    -- it is a town from outside the target regions that the OSM extract
    happened to include (Austrian, Slovenian, or from a neighbouring Italian
    region), because ISTAT covers every real municipality within the three
    target regions. Treat a long unmatched list as a signal that the region
    filter needs tightening, not that ISTAT is incomplete.
    """
    pop_df = load_population_table()

    # Name collisions can still occur at this final OSM-facing boundary, since
    # the join key here is a normalised name rather than the ISTAT code.
    dup_mask = pop_df["_join_key"].duplicated(keep=False)
    if dup_mask.any():
        colliding = sorted(set(pop_df.loc[dup_mask, "name"]))
        print(f"[WARN] {int(dup_mask.sum())} ISTAT rows normalise to a shared "
              f"name: {colliding}. These are genuinely distinct towns sharing a "
              f"name across provinces; only the first can be identified from an "
              f"OSM name alone. Keeping the first of each.")
        pop_df = pop_df.drop_duplicates(subset="_join_key", keep="first")

    fields = ["population", "province", "population_65plus", "pct_65plus", "aging_index"]
    lookup = pop_df.set_index("_join_key")[fields].to_dict("index")

    towns_gdf = towns_gdf.copy()
    collected = {field: [] for field in fields}
    unmatched = []

    for name in towns_gdf["name"]:
        match = None
        for candidate in _generate_name_candidates(name):
            key = _normalize_name(candidate)
            if key in lookup:
                match = lookup[key]
                break

        if match:
            for field in fields:
                collected[field].append(match[field])
        else:
            unmatched.append(name)
            for field in fields:
                collected[field].append(None if field == "province" else pd.NA)

    for field in fields:
        towns_gdf[field] = collected[field]

    print(f"[INFO] Matched {len(towns_gdf) - len(unmatched)}/{len(towns_gdf)} "
          f"towns to ISTAT data.")

    if unmatched:
        print(f"[WARN] {len(unmatched)} towns had no ISTAT match after trying "
              f"bilingual name candidates: {unmatched}")
        print("       ISTAT covers every municipality in the target regions, so "
              "these are most likely towns from OUTSIDE the study area that the "
              "OSM extract included. See config.EXCLUDE_UNMATCHED_TOWNS.")

    if config.EXCLUDE_UNMATCHED_TOWNS and unmatched:
        before = len(towns_gdf)
        towns_gdf = towns_gdf[towns_gdf["population"].notna()].copy()
        print(f"[INFO] EXCLUDE_UNMATCHED_TOWNS is on -- dropped "
              f"{before - len(towns_gdf)} unmatched towns from the analysis.")

    return towns_gdf
