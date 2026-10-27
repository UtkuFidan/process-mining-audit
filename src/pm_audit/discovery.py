"""Process discovery: variants, throughput times, directly-follows graph, inductive miner."""

from __future__ import annotations

from collections import Counter

import pandas as pd


def traces(df: pd.DataFrame) -> pd.Series:
    """Activity tuple per case, ordered by timestamp (log order breaks ties)."""
    return df.groupby("case_id", sort=False)["activity"].agg(tuple)


def variants(df: pd.DataFrame) -> pd.DataFrame:
    """Distinct traces with case counts, share and cumulative share (Pareto order)."""
    counts = traces(df).value_counts()
    out = counts.rename("cases").reset_index()
    out.columns = ["variant", "cases"]
    out["share"] = out["cases"] / out["cases"].sum()
    out["cum_share"] = out["share"].cumsum()
    out["rank"] = range(1, len(out) + 1)
    return out


def variant_summary(v: pd.DataFrame) -> dict:
    n_cases = int(v["cases"].sum())
    return {
        "cases": n_cases,
        "variants": len(v),
        "top1_share": float(v["share"].iloc[0]),
        "top10_share": float(v["share"].head(10).sum()),
        "variants_for_80pct": int((v["cum_share"] < 0.8).sum() + 1),
        "singleton_variants": int((v["cases"] == 1).sum()),
        "singleton_share_of_variants": float((v["cases"] == 1).mean()),
        "tail_cases_outside_top10": int(v["cases"].iloc[10:].sum()),
    }


def throughput(df: pd.DataFrame) -> pd.Series:
    """Case duration in days (first to last event)."""
    g = df.groupby("case_id")["timestamp"]
    return ((g.max() - g.min()).dt.total_seconds() / 86400).rename("days")


def throughput_summary(days: pd.Series) -> dict:
    q = days.quantile([0.5, 0.9, 0.95, 0.99])
    return {
        "median_days": float(q[0.5]),
        "p90_days": float(q[0.9]),
        "p95_days": float(q[0.95]),
        "p99_days": float(q[0.99]),
        "mean_days": float(days.mean()),
        "share_over_90_days": float((days > 90).mean()),
    }


def segment_times(df: pd.DataFrame) -> list[dict]:
    """Median/p90 waiting time between the key P2P milestones (cases having both)."""
    from .controls import CREATE_PO, GOODS_RECEIPT, INVOICE_RECEIPT, PAYMENT, first_times

    f = first_times(df, [CREATE_PO, GOODS_RECEIPT, INVOICE_RECEIPT, PAYMENT])
    out = []
    for a, b in [
        (CREATE_PO, GOODS_RECEIPT),
        (GOODS_RECEIPT, INVOICE_RECEIPT),
        (INVOICE_RECEIPT, PAYMENT),
        (CREATE_PO, PAYMENT),
    ]:
        d = ((f[b] - f[a]).dt.total_seconds() / 86400).dropna()
        out.append(
            {
                "from": a,
                "to": b,
                "cases": len(d),
                "median_days": float(d.median()),
                "p90_days": float(d.quantile(0.9)),
                "negative_share": float((d < 0).mean()),
            }
        )
    return out


def dfg(df: pd.DataFrame) -> tuple[Counter, Counter]:
    """Directly-follows edge counts and activity frequencies."""
    edges: Counter = Counter()
    acts: Counter = Counter()
    for t, n in traces(df).value_counts().items():
        for a in t:
            acts[a] += n
        for a, b in zip(t, t[1:], strict=False):
            edges[(a, b)] += n
    return edges, acts


def dfg_performance(df: pd.DataFrame, activities: list[str]) -> pd.DataFrame:
    """Directly-follows edges of the log projected on ``activities``.

    Columns: source, target, count (occurrences) and median_days between the two events.
    Events of other activities are removed first, so an edge can span them.
    """
    sub = df[df["activity"].isin(activities)]
    g = sub.groupby("case_id", sort=False)
    nxt_act, nxt_ts = g["activity"].shift(-1), g["timestamp"].shift(-1)
    m = nxt_act.notna()
    e = pd.DataFrame(
        {
            "source": sub.loc[m, "activity"],
            "target": nxt_act[m],
            "days": (nxt_ts[m] - sub.loc[m, "timestamp"]).dt.total_seconds() / 86400,
        }
    )
    out = e.groupby(["source", "target"])["days"].agg(count="size", median_days="median")
    return out.reset_index()


def inductive_tree(df: pd.DataFrame, noise: float = 0.2, max_cases: int = 50000):
    """Inductive Miner (infrequent, noise threshold) on a seeded case sample if large."""
    import pm4py

    from .data import sample_cases

    sub = sample_cases(df, max_cases) if df["case_id"].nunique() > max_cases else df
    tree = pm4py.discover_process_tree_inductive(
        sub,
        noise_threshold=noise,
        activity_key="activity",
        case_id_key="case_id",
        timestamp_key="timestamp",
    )
    return tree, sub["case_id"].nunique()
