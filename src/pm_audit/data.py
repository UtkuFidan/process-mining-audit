"""Download, cache and normalise the BPI Challenge 2019 event log."""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

DOI = "10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1"
LANDING = "https://data.4tu.nl/articles/_/12715853/1"
URL = "https://ndownloader.figshare.com/files/24072995"  # official XES, 728,558,522 bytes
MD5 = "4eb909242351193a61e1c15b9c3cc814"
LICENSE = "CC BY 4.0"
# Fallback: the challenge's original CSV release (1,595,923 events), zipped, on a GitHub mirror.
MIRROR_URL = (
    "https://github.com/mrsonuk/BPI_Challenge_2019/raw/HEAD/Dataset/BPIChallenge2019_Original.zip"
)
EXPECTED_EVENTS = 1_595_923
DATA_SOURCE = (
    "the challenge's original CSV release (1,595,923 events, matching the official event count) "
    "from a public GitHub mirror, because the official 4TU/figshare download returned HTTP 504 "
    "when this analysis was run"
)
XES_NAME = "BPI_Challenge_2019.xes"
CSV_NAME = "BPI_Challenge_2019.csv"
CACHE_NAME = "bpi2019.parquet"
MIN_VALID_YEAR = 2017  # a few vendor events carry the placeholder date 1948-01-26

# Raw attribute -> normalised column (XES names, then the CSV release's prefixed names).
RENAME = {
    "case:concept:name": "case_id",
    "concept:name": "activity",
    "time:timestamp": "timestamp",
    "org:resource": "resource",
    "case:Company": "company",
    "case:Vendor": "vendor",
    "case:Item Category": "item_category",
    "case:Spend area text": "spend_area",
    "case:Document Type": "doc_type",
    "case:GR-Based Inv. Verif.": "gr_based_iv",
    "case:Item Type": "item_type",
    "Cumulative net worth (EUR)": "net_worth",
    "case concept:name": "case_id",
    "event concept:name": "activity",
    "event time:timestamp": "timestamp",
    "event org:resource": "resource",
    "case Company": "company",
    "case Vendor": "vendor",
    "case Item Category": "item_category",
    "case Spend area text": "spend_area",
    "case Document Type": "doc_type",
    "case GR-Based Inv. Verif.": "gr_based_iv",
    "case Item Type": "item_type",
    "event Cumulative net worth (EUR)": "net_worth",
}
CASE_ATTRS = ["company", "vendor", "item_category", "spend_area", "doc_type", "gr_based_iv"]


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as r, dest.open("wb") as f:  # noqa: S310
        shutil.copyfileobj(r, f)


def fetch(data_dir: Path, force: bool = False) -> Path:
    """Get the raw log into the gitignored data directory.

    Tries the official 4TU/figshare XES first; if that is unavailable (it returned HTTP 504 when
    this repo was built) falls back to the original CSV release on a GitHub mirror.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in (data_dir / XES_NAME, data_dir / CSV_NAME) if p.exists()]
    if existing and not force:
        print(f"already present: {existing[0]}")
        return existing[0]
    xes = data_dir / XES_NAME
    part = xes.with_suffix(".part")
    try:
        print(f"downloading {URL}")
        _download(URL, part)
        md5 = hashlib.md5()
        with part.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                md5.update(chunk)
        if md5.hexdigest() != MD5:
            raise RuntimeError("checksum mismatch")
        part.rename(xes)
        return xes
    except (OSError, RuntimeError) as e:
        part.unlink(missing_ok=True)
        print(f"official download failed ({e}); using mirror {MIRROR_URL}")
    archive = data_dir / "mirror.zip"
    _download(MIRROR_URL, archive)
    with zipfile.ZipFile(archive) as z:
        z.extract(CSV_NAME, data_dir)
    archive.unlink()
    return data_dir / CSV_NAME


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns, parse timestamps and keep a stable event order within a case."""
    df = df.rename(columns=RENAME)
    keep = [c for c in dict.fromkeys(RENAME.values()) if c in df.columns]
    df = df[keep].copy()
    if df["timestamp"].dtype == object or str(df["timestamp"].dtype).startswith("str"):
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="%d-%m-%Y %H:%M:%S.%f")
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None)
    df = df[df["timestamp"].dt.year >= MIN_VALID_YEAR]  # drop placeholder-dated events
    df["_order"] = range(len(df))
    df = df.sort_values(["case_id", "timestamp", "_order"], kind="stable")
    return df.drop(columns="_order").reset_index(drop=True)


def load(data_dir: Path, sample: int | None = None, seed: int = 42) -> pd.DataFrame:
    """Load the event log (parquet cache first, XES second); optionally sample cases."""
    cache = data_dir / CACHE_NAME
    if cache.exists():
        df = pd.read_parquet(cache)
    else:
        xes, csv = data_dir / XES_NAME, data_dir / CSV_NAME
        if xes.exists():
            import pm4py

            raw = pm4py.read_xes(str(xes), return_legacy_log_object=False)
        elif csv.exists():
            raw = pd.read_csv(csv, encoding="latin-1", low_memory=False)
            raw.columns = [c.strip() for c in raw.columns]
        else:
            raise FileNotFoundError(f"no log in {data_dir} - run `pm-audit fetch` first")
        df = normalise(raw)
        df.to_parquet(cache)
    return sample_cases(df, sample, seed)


def sample_cases(df: pd.DataFrame, n: int | None, seed: int = 42) -> pd.DataFrame:
    """Random subsample of whole cases (keeps traces intact)."""
    if not n:
        return df
    ids = df["case_id"].drop_duplicates()
    if n >= len(ids):
        return df
    keep = ids.sample(n=n, random_state=seed)
    return df[df["case_id"].isin(set(keep))].reset_index(drop=True)


def case_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per case with the descriptive attributes used for root-cause slices."""
    cols = [c for c in CASE_ATTRS if c in df.columns]
    return df.groupby("case_id", sort=False)[cols].first()
