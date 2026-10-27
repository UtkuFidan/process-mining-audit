"""Conformance checking against a normative P2P model (token replay and alignments)."""

from __future__ import annotations

import pandas as pd

from .controls import (
    AFTER_GR,
    APPROVAL,
    BEFORE_GR,
    CREATE_PO,
    GOODS_RECEIPT,
    INVOICE_RECEIPT,
    PAYMENT,
    RELEASE_PO,
    SERVICE_ENTRY,
    TWO_WAY,
)
from .discovery import traces

CORE = [CREATE_PO, APPROVAL, RELEASE_PO, GOODS_RECEIPT, SERVICE_ENTRY, INVOICE_RECEIPT, PAYMENT]

_APPROVE = f"X('{APPROVAL}', '{RELEASE_PO}', tau)"
_RECEIPT = f"X('{GOODS_RECEIPT}', '{SERVICE_ENTRY}')"
_RECEIPT_OPT = f"X('{GOODS_RECEIPT}', '{SERVICE_ENTRY}', tau)"
_PO, _IR, _PAY = f"'{CREATE_PO}'", f"'{INVOICE_RECEIPT}'", f"'{PAYMENT}'"
# One normative model per item category. BPI 2019 records an approval step for only a few
# percent of items, so it is optional. Consignment has no invoice chain and is not modelled.
MODELS = {
    # receipt is a precondition of the invoice
    AFTER_GR: f"->({_PO}, {_APPROVE}, {_RECEIPT}, {_IR}, {_PAY})",
    # invoice may be booked before or after the receipt
    BEFORE_GR: f"->({_PO}, {_APPROVE}, +({_RECEIPT}, {_IR}), {_PAY})",
    # no receipt expected, but one may exist
    TWO_WAY: f"->({_PO}, {_APPROVE}, +({_RECEIPT_OPT}, {_IR}), {_PAY})",
}
# Steps drawn in the deviation view: label and the activities that realise the step.
STEPS = [
    ("Create PO item", [CREATE_PO]),
    ("Approval (optional)", [APPROVAL, RELEASE_PO]),
    ("Goods receipt /\nservice entry sheet", [GOODS_RECEIPT, SERVICE_ENTRY]),
    ("Invoice receipt", [INVOICE_RECEIPT]),
    ("Payment\n(Clear Invoice)", [PAYMENT]),
]


def normative_net(model: str):
    import pm4py

    return pm4py.convert_to_petri_net(pm4py.parse_process_tree(model))


def projected_variants(df: pd.DataFrame) -> pd.DataFrame:
    """Variants of the log projected on the five normative activities, with case counts."""
    sub = df[df["activity"].isin(CORE)]
    t = traces(sub).reindex(df["case_id"].unique(), fill_value=())
    v = t.value_counts().rename("cases").reset_index()
    v.columns = ["variant", "cases"]
    return v


def _variant_log(v: pd.DataFrame):
    import pm4py

    rows = [
        (f"v{i}", a, pd.Timestamp("2020-01-01") + pd.Timedelta(minutes=j))
        for i, t in enumerate(v["variant"])
        for j, a in enumerate(t)
    ]
    d = pd.DataFrame(rows, columns=["case:concept:name", "concept:name", "time:timestamp"])
    return pm4py.convert_to_event_log(d)


def token_replay(v: pd.DataFrame, net) -> dict:
    import pm4py

    log = _variant_log(v)
    res = pm4py.conformance_diagnostics_token_based_replay(log, *net)
    w = v["cases"].to_numpy()
    fit = [r["trace_fitness"] for r in res]
    perfect = [bool(r["trace_is_fit"]) for r in res]
    return {
        "weighted_trace_fitness": float((pd.Series(fit) * w).sum() / w.sum()),
        "fit_cases": int(w[perfect].sum()),
        "fit_share": float(w[perfect].sum() / w.sum()),
        "variants": len(v),
    }


def _label(activity: str) -> str:
    """Goods receipt and service entry sheet are interchangeable receipt evidence."""
    return (
        "Goods receipt / service entry sheet"
        if activity in (GOODS_RECEIPT, SERVICE_ENTRY)
        else activity
    )


def alignments(v: pd.DataFrame, net, top: int = 300) -> dict:
    """Optimal alignments of the most frequent projected variants; classify deviations."""
    import pm4py

    vt = v.head(top).reset_index(drop=True)
    log = _variant_log(vt)
    res = pm4py.conformance_diagnostics_alignments(log, *net)
    missing: dict[str, int] = {}
    extra: dict[str, int] = {}
    fit_cases = 0
    fit_sum = 0.0
    for r, n in zip(res, vt["cases"], strict=True):
        fit_sum += r["fitness"] * n
        miss, ext = set(), set()
        for log_move, model_move in r["alignment"]:
            if log_move == model_move or model_move is None:
                continue  # synchronous or silent (tau) model move
            if model_move == ">>":  # log move: event the model does not allow here
                ext.add(_label(log_move))
            else:  # model move: normative step missing in the trace
                miss.add(_label(model_move))
        for k in miss:  # a case counts once per activity, however often the move repeats
            missing[k] = missing.get(k, 0) + int(n)
        for k in ext:
            extra[k] = extra.get(k, 0) + int(n)
        fit_cases += 0 if miss or ext else int(n)
    total = int(vt["cases"].sum())
    return {
        "variants_aligned": len(vt),
        "cases_covered": total,
        "coverage_share": float(total / v["cases"].sum()),
        "alignment_fitness": float(fit_sum / total),
        "perfectly_aligned_cases": fit_cases,
        "missing_step_cases": dict(sorted(missing.items(), key=lambda kv: -kv[1])),
        "extra_step_cases": dict(sorted(extra.items(), key=lambda kv: -kv[1])),
    }


def run_model(df: pd.DataFrame, model: str, top: int = 500) -> dict:
    """Token replay and alignments of the log against one normative model."""
    net = normative_net(model)
    v = projected_variants(df)
    total = int(v["cases"].sum())
    return {
        "model": model,
        "cases": total,
        "projected_variants": len(v),
        "token_replay": token_replay(v, net),
        "alignments": alignments(v, net, top),
        "top_projected_variants": [
            {"variant": " > ".join(t) or "(none)", "cases": int(n), "share": float(n / total)}
            for t, n in zip(v["variant"].head(8), v["cases"].head(8), strict=True)
        ],
    }


def run(df: pd.DataFrame, top: int = 500) -> dict:
    """Conformance per item category against its own model, plus the combined fit share."""
    case_cat = df.groupby("case_id")["item_category"].first()
    cats = {}
    for cat, model in MODELS.items():
        sub = df[df["case_id"].isin(case_cat.index[case_cat == cat])]
        if sub.empty:
            continue
        cats[cat] = run_model(sub, model, top)
    outside = df.loc[~df["activity"].isin(CORE), "activity"].value_counts()
    modelled = sum(c["cases"] for c in cats.values())
    fit = sum(c["token_replay"]["fit_cases"] for c in cats.values())
    return {
        "categories": cats,
        "modelled_cases": modelled,
        "not_modelled_cases": int(case_cat.size - modelled),
        "fit_cases": fit,
        "fit_share": fit / modelled if modelled else 0.0,
        "events_outside_model": {k: int(x) for k, x in outside.head(12).items()},
    }
