"""
population_istat.py
--------------------
Loads official ISTAT population figures and matches them to comuni by name.

Supports MULTIPLE input files (config.ISTAT_POPULATION_CSVS) -- e.g. one
per region -- which get combined into a single lookup table automatically.

Handles two known ISTAT column-naming variants for the comune name field:
"Denominazione Comune" or "Nome Comune" (different exports use different
labels for the same thing).

Expected columns per file: Provincia, Codice Comune,
Denominazione/Nome Comune, Popolazione Totale.
"""

import pandas as pd
import unicodedata
import re

import config


def _normalize_name(name):
    """Normalizes comune names for reliable joining (accents, casing, punctuation)."""
    if not isinstance(name, str):
        # Handles blank/NaN cells (e.g. stray footer rows in the ISTAT export)
        # that pandas reads as float instead of text.
        return ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("utf-8")
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9]", "", name)
    return name


def _find_name_column(columns):
    """
    Finds the comune-name column across known ISTAT naming variants:
    'Denominazione Comune', 'Nome Comune', etc.
    """
    lower_cols = {c.lower(): c for c in columns}

    # Variant 1: contains "denomin" (e.g. "Denominazione Comune")
    for lower, original in lower_cols.items():
        if "denomin" in lower:
            return original

    # Variant 2: contains both "nome" and "comune" (e.g. "Nome Comune"),
    # but NOT "codice" (to avoid matching "Codice Comune" by mistake)
    for lower, original in lower_cols.items():
        if "nome" in lower and "comune" in lower and "codice" not in lower:
            return original

    return None


def _load_single_file(path):
    """Loads and cleans one ISTAT CSV file, returning a standardized DataFrame."""
    # ISTAT exports are Latin-1 (ISO-8859-1) encoded, not UTF-8 -- Italian
    # accented characters (Arsiè, Città, etc.) will fail to decode otherwise.
    df = pd.read_csv(path, sep=None, engine="python", encoding="latin1")
    df.columns = [c.strip() for c in df.columns]

    name_col = _find_name_column(df.columns)
    if name_col is None:
        raise ValueError(
            f"Could not find a comune-name column in {path}. "
            f"Columns found: {list(df.columns)}"
        )

    pop_col = next((c for c in df.columns if "popolazione" in c.lower()), None)
    if pop_col is None:
        pop_col = next((c for c in df.columns if "pop" in c.lower()), None)
    if pop_col is None:
        raise ValueError(
            f"Could not find a population column in {path}. "
            f"Columns found: {list(df.columns)}"
        )

    province_col = next((c for c in df.columns if "provincia" in c.lower()), None)

    keep_cols = [name_col, pop_col] + ([province_col] if province_col else [])
    df = df[keep_cols].rename(columns={
        name_col: "name",
        pop_col: "population",
        **({province_col: "province"} if province_col else {}),
    })
    if province_col is None:
        df["province"] = None
        print(f"[WARN] No PROVINCIA column found in {path} -- province "
              f"field will be blank for towns from this file.")

    # Drop rows with no comune name at all -- typically stray footer/notes
    # rows (totals, source citations) at the bottom of the ISTAT export.
    before_count = len(df)
    df = df.dropna(subset=["name"])
    df = df[df["name"].astype(str).str.strip() != ""]
    dropped = before_count - len(df)
    if dropped:
        print(f"[INFO] Dropped {dropped} blank/footer row(s) from {path}.")

    # ISTAT formats population as text with comma thousands-separators (e.g. "4,019").
    df["population"] = (
        df["population"]
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.strip()
    )
    df["population"] = pd.to_numeric(df["population"], errors="coerce").astype("Int64")

    print(f"[INFO] Loaded {len(df)} comuni from {path}.")
    return df


def load_population_table():
    """
    Loads and combines every file in config.ISTAT_POPULATION_CSVS into a
    single population lookup table.
    """
    all_dfs = []
    for path in config.ISTAT_POPULATION_CSVS:
        try:
            all_dfs.append(_load_single_file(path))
        except FileNotFoundError:
            print(f"[WARN] ISTAT file not found, skipping: {path}")
        except Exception as e:
            print(f"[WARN] Failed to load {path}: {e}")

    if not all_dfs:
        raise RuntimeError(
            "No ISTAT population files could be loaded. Check "
            "config.ISTAT_POPULATION_CSVS and confirm the files exist."
        )

    df = pd.concat(all_dfs, ignore_index=True)
    df["_join_key"] = df["name"].apply(_normalize_name)
    print(f"[INFO] Combined ISTAT population table: {len(df)} comuni total "
          f"across {len(all_dfs)} file(s).")
    return df


def _generate_name_candidates(name):
    """
    Generates candidate names to try matching against ISTAT data.

    Handles bilingual comune names, which are common in Trentino-Alto Adige
    (German - Italian, e.g. "Meran - Merano" -- note the language order is
    NOT consistent, some are Italian-first like "Bolzano - Bozen") and in
    Friuli-Venezia Giulia (Italian / Friulian or Slovenian, e.g.
    "Udine / Udin"). ISTAT's official records use only the plain Italian
    name, but OSM's "name" tag often combines both languages into one
    string -- so we try every segment as a candidate, not just one.
    """
    candidates = [name]
    for sep in [" - ", "-", " / ", "/"]:
        if sep in name:
            candidates.extend(p.strip() for p in name.split(sep))
    return candidates


def attach_population(comuni_gdf):
    """
    Adds 'population' and 'province' columns to the comuni GeoDataFrame by
    matching comune names against the ISTAT table. Tries multiple name
    candidates per comune (see _generate_name_candidates) to handle
    bilingual OSM names that don't match ISTAT's plain-Italian records
    directly. Logs any comuni that still fail to match after all
    candidates are tried.
    """
    pop_df = load_population_table()

    # Guard against duplicate normalized names in the combined ISTAT data
    # (e.g. two rows that only differ by accents/punctuation once
    # normalized) -- building a lookup dict requires unique keys, so we
    # keep the first occurrence of each and surface exactly which names
    # collided, in case it points to a real duplicate-row issue worth
    # checking in the source CSVs.
    dup_mask = pop_df["_join_key"].duplicated(keep=False)
    if dup_mask.any():
        dup_names = pop_df.loc[dup_mask, "name"].tolist()
        print(f"[WARN] {dup_mask.sum()} ISTAT rows have colliding normalized "
              f"names -- keeping only the first occurrence of each: {dup_names}")
        pop_df = pop_df.drop_duplicates(subset="_join_key", keep="first")

    pop_lookup = pop_df.set_index("_join_key")[["population", "province"]].to_dict("index")

    comuni_gdf = comuni_gdf.copy()
    populations = []
    provinces = []
    unmatched = []

    for name in comuni_gdf["name"]:
        match = None
        for candidate in _generate_name_candidates(name):
            key = _normalize_name(candidate)
            if key in pop_lookup:
                match = pop_lookup[key]
                break
        if match:
            populations.append(match["population"])
            provinces.append(match["province"])
        else:
            populations.append(pd.NA)
            provinces.append(None)
            unmatched.append(name)

    comuni_gdf["population"] = populations
    comuni_gdf["province"] = provinces

    if unmatched:
        print(f"[WARN] {len(unmatched)} comuni had no population match "
              f"after trying bilingual name candidates: {unmatched}")

    return comuni_gdf


if __name__ == "__main__":
    import geopandas as gpd
    comuni = gpd.read_file(config.COMUNI_CACHE_PATH)
    comuni_with_pop = attach_population(comuni)
    print(comuni_with_pop[["name", "province", "population"]].head(20))