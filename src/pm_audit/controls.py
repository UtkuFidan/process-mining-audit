"""Full-population control tests on a purchase-to-pay event log.

One case is one purchase order item. Each test runs only on the BPI item categories where it
applies (see ``SCOPES``) and returns the tested population, the flagged cases, a few examples and
the audit risk it addresses. Input is the normalised log from :func:`pm_audit.data.normalise`:
columns ``case_id``, ``activity``, ``timestamp``, ``resource`` plus the case attributes
``item_category``, ``company``, ``vendor``, ``spend_area`` where available.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

CREATE_PO = "Create Purchase Order Item"
APPROVAL = "Change Approval for Purchase Order"
GOODS_RECEIPT = "Record Goods Receipt"
SERVICE_ENTRY = "Record Service Entry Sheet"  # receipt evidence for service items
RELEASE_PO = "Release Purchase Order"
INVOICE_RECEIPT = "Record Invoice Receipt"
VENDOR_INVOICE = "Vendor creates invoice"
PAYMENT = "Clear Invoice"
CHANGE_PRICE = "Change Price"
CHANGE_QTY = "Change Quantity"

RECEIPTS = [GOODS_RECEIPT, SERVICE_ENTRY]
APPROVALS = [APPROVAL, RELEASE_PO]

SYSTEM_RESOURCE = r"^(?:NONE|batch_\d+)$"  # non-personal posting users in BPI 2019
BUSINESS_HOURS = (7, 19)  # approvals outside [07:00, 19:00) or at weekends are after-hours
GRACE_DAYS = 14  # tolerance above the usual days-to-pay, about one payment-run cycle
MIN_VENDOR_ITEMS = 30  # paid items a vendor needs before its own median is used as its proxy

# BPI 2019 item categories. Each control runs only on the categories where it is meaningful.
AFTER_GR = "3-way match, invoice after GR"  # invoice verification is GR-based
BEFORE_GR = "3-way match, invoice before GR"  # receipt required, invoice may be booked first
TWO_WAY = "2-way match"  # no goods receipt expected
CONSIGNMENT = "Consignment"  # settled on withdrawal, no order-invoice-payment chain
THREE_WAY = (AFTER_GR, BEFORE_GR)
INVOICED = (AFTER_GR, BEFORE_GR, TWO_WAY)
ALL_CATEGORIES = (AFTER_GR, BEFORE_GR, TWO_WAY, CONSIGNMENT)
SCOPES: dict[str, tuple[str, ...]] = {
    "C1a": (AFTER_GR,),
    "C1b": (AFTER_GR,),
    "C1c": INVOICED,
    "C1d": INVOICED,
    "C1e": (BEFORE_GR,),
    "C1f": (BEFORE_GR,),
    "C2": INVOICED,
    "C3a": ALL_CATEGORIES,
    "C3b": ALL_CATEGORIES,
    "C3c": THREE_WAY,
    "C4a": ALL_CATEGORIES,
    "C4b": ALL_CATEGORIES,
    "C5": INVOICED,
    "C6": ALL_CATEGORIES,
}


@dataclass
class ControlResult:
    id: str
    name: str
    risk: str
    population: int
    flagged: int
    definition: str
    examples: list[dict] = field(default_factory=list)
    population_ids: pd.Index = field(default_factory=lambda: pd.Index([]), repr=False)
    flagged_ids: pd.Index = field(default_factory=lambda: pd.Index([]), repr=False)
    extra: dict = field(default_factory=dict)
    by_category: dict = field(default_factory=dict)

    @property
    def rate(self) -> float:
        return self.flagged / self.population if self.population else 0.0

    @property
    def scope(self) -> tuple[str, ...]:
        return SCOPES[self.id]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "risk": self.risk,
            "definition": self.definition,
            "scope": list(self.scope),
            "population": self.population,
            "flagged": self.flagged,
            "rate": self.rate,
            "by_category": self.by_category,
            "extra": self.extra,
            "examples": self.examples,
        }


def scoped(df: pd.DataFrame, cid: str) -> pd.DataFrame:
    """Rows of the cases whose item category is in the scope of control ``cid``."""
    if "item_category" not in df:
        return df
    return df[df["item_category"].isin(SCOPES[cid])]


def first_times(df: pd.DataFrame, activities: list[str]) -> pd.DataFrame:
    """Earliest timestamp per case and activity (columns = activities, missing = NaT)."""
    sub = df[df["activity"].isin(activities)]
    wide = sub.groupby(["case_id", "activity"])["timestamp"].min().unstack("activity")
    wide = wide.reindex(index=pd.Index(df["case_id"].unique(), name="case_id"))
    for a in activities:
        if a not in wide:
            wide[a] = pd.NaT
    return wide[activities]


def _human(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["resource"].notna() & ~df["resource"].astype(str).str.match(SYSTEM_RESOURCE)]


def _make(
    cid, name, risk, definition, pop_mask, flag_mask, detail, n_examples=5, gap=None, extra=None
):
    """Build a ControlResult from boolean masks indexed by case_id."""
    flag_mask = flag_mask & pop_mask
    ids = flag_mask.index[flag_mask]
    if gap is not None:
        order = gap[ids].sort_values(ascending=False).index
    else:
        order = pd.Index(sorted(ids))
    examples = [{"case_id": c, "detail": detail(c)} for c in order[:n_examples]]
    return ControlResult(
        cid,
        name,
        risk,
        int(pop_mask.sum()),
        int(flag_mask.sum()),
        definition,
        examples,
        pop_mask.index[pop_mask],
        ids,
        extra or {},
    )


def _days(delta: pd.Series) -> pd.Series:
    return delta.dt.total_seconds() / 86400


def three_way_match(df: pd.DataFrame) -> list[ControlResult]:
    out = []

    d = scoped(df, "C1a")
    f = first_times(d, [*RECEIPTS, INVOICE_RECEIPT])
    gr, inv = f[RECEIPTS].min(axis=1), f[INVOICE_RECEIPT]
    gap = _days(gr - inv)
    out.append(
        _make(
            "C1a",
            "Invoice posted before goods receipt",
            "Paying for goods or services not (yet) received; three-way match bypassed.",
            "First 'Record Invoice Receipt' earlier than the first goods receipt / service entry "
            "sheet, on items where invoice verification is GR-based.",
            gr.notna() & inv.notna(),
            inv < gr,
            lambda c: f"invoice {gap[c]:.1f} days before goods receipt",
            gap=gap,
        )
    )
    out.append(
        _make(
            "C1b",
            "Invoice without any receipt",
            "Items with GR-based verification were invoiced without any receipt.",
            "An invoice receipt exists, no goods receipt / service entry sheet.",
            inv.notna(),
            gr.isna(),
            lambda c: "invoice receipt recorded, no goods receipt in the log",
        )
    )

    d = scoped(df, "C1e")
    f = first_times(d, [*RECEIPTS, INVOICE_RECEIPT, PAYMENT])
    gr, inv, pay = f[RECEIPTS].min(axis=1), f[INVOICE_RECEIPT], f[PAYMENT]
    gap = _days(gr - inv)
    out.append(
        _make(
            "C1e",
            "Invoice before goods receipt, where configuration allows it",
            "Permitted by configuration; accepted risk of invoicing before receipt, for scale.",
            "Item category 'invoice before GR'; invoice receipt earlier than the first receipt.",
            gr.notna() & inv.notna(),
            inv < gr,
            lambda c: f"invoice {gap[c]:.1f} days before goods receipt",
            gap=gap,
        )
    )
    out.append(
        _make(
            "C1f",
            "Payment without any receipt",
            "Paid with no goods receipt or service entry sheet on a three-way-match item.",
            "Item category 'invoice before GR'; 'Clear Invoice' exists, no receipt event.",
            pay.notna(),
            gr.isna(),
            lambda c: "payment cleared, no goods receipt in the log",
        )
    )

    d = scoped(df, "C1c")
    f = first_times(d, [INVOICE_RECEIPT, PAYMENT])
    inv, pay = f[INVOICE_RECEIPT], f[PAYMENT]
    gap2 = _days(inv - pay)
    out.append(
        _make(
            "C1c",
            "Payment before invoice receipt",
            "Payment released before the supplier invoice was verified.",
            "First 'Clear Invoice' earlier than first 'Record Invoice Receipt'.",
            pay.notna() & inv.notna(),
            pay < inv,
            lambda c: f"payment {gap2[c]:.1f} days before invoice receipt",
            gap=gap2,
        )
    )
    out.append(
        _make(
            "C1d",
            "Payment without any invoice receipt",
            "Payment with no supporting invoice document in the process.",
            "Case has 'Clear Invoice' but no 'Record Invoice Receipt'.",
            pay.notna(),
            inv.isna(),
            lambda c: "payment cleared, no invoice receipt in the log",
        )
    )
    return out


def maverick_buying(df: pd.DataFrame) -> ControlResult:
    f = first_times(scoped(df, "C2"), [CREATE_PO, VENDOR_INVOICE, INVOICE_RECEIPT])
    inv = f[[VENDOR_INVOICE, INVOICE_RECEIPT]].min(axis=1)
    po = f[CREATE_PO]
    po, inv = po.dt.normalize(), inv.dt.normalize()  # 'Vendor creates invoice' is date-only
    gap = _days(po - inv)
    return _make(
        "C2",
        "Maverick buying (PO created after the invoice)",
        "Commitment made before authorisation; spend outside purchasing controls.",
        "'Create Purchase Order Item' on a later calendar day than the earliest invoice event.",
        po.notna() & inv.notna(),
        po > inv,
        lambda c: f"PO created {gap[c]:.1f} days after the invoice",
        gap=gap,
    )


def _overlap(ua: pd.DataFrame, ub: pd.DataFrame, cases: pd.Index):
    """Cases with a user on both steps, cases where it is the same user, and who it was."""
    pop = pd.Series(cases.isin(set(ua["case_id"]) & set(ub["case_id"])), index=cases)
    both = ua.merge(ub, on=["case_id", "resource"])
    flag = pd.Series(cases.isin(set(both["case_id"])), index=cases)
    return pop, flag, both.groupby("case_id")["resource"].first()


def _same_user(df: pd.DataFrame, a: list[str], b: list[str], humans_only: bool):
    cases = pd.Index(df["case_id"].unique(), name="case_id")
    h = _human(df) if humans_only else df[df["resource"].notna()]
    ua = h[h["activity"].isin(a)][["case_id", "resource"]].drop_duplicates()
    ub = h[h["activity"].isin(b)][["case_id", "resource"]].drop_duplicates()
    return _overlap(ua, ub, cases)


def segregation_of_duties(df: pd.DataFrame) -> list[ControlResult]:
    """Same-user tests. The result is the human-only rate; the all-user rate is in ``extra``.

    Batch jobs and ``NONE`` post many documents under one id, so a match on them says nothing
    about two people. They are kept in the all-user figure to show how much they inflate it.
    """
    pairs = [
        (
            "C3a",
            [CREATE_PO],
            APPROVALS,
            "Same user creates and approves the purchase order",
            "Self-approval of own commitments.",
        ),
        (
            "C3b",
            [CREATE_PO],
            [PAYMENT],
            "Same user creates the PO and clears the invoice",
            "One person controls order-to-pay; fictitious-purchase risk.",
        ),
        (
            "C3c",
            RECEIPTS,
            [INVOICE_RECEIPT],
            "Same user records goods receipt and invoice",
            "Receipt and invoice verification not independent; weakens three-way match.",
        ),
    ]
    out = []
    for cid, a, b, name, risk in pairs:
        d = scoped(df, cid)
        pop, flag, users = _same_user(d, a, b, humans_only=True)
        pop_all, flag_all, _ = _same_user(d, a, b, humans_only=False)
        extra = {
            "all_users": {
                "population": int(pop_all.sum()),
                "flagged": int(flag_all.sum()),
                "rate": float(flag_all.sum() / pop_all.sum()) if pop_all.sum() else 0.0,
                "flagged_only_system_users": int((flag_all & ~flag).sum()),
            }
        }
        out.append(
            _make(
                cid,
                name,
                risk,
                f"Same named user (batch jobs and NONE excluded) on {' / '.join(a)} and "
                f"{' / '.join(b)} in one case.",
                pop,
                flag,
                lambda c, users=users: f"user {users[c]} performed both steps",
                extra=extra,
            )
        )
    return out


def rework_after_approval(df: pd.DataFrame) -> list[ControlResult]:
    out = []
    for cid, anchor, name, label in [
        ("C4a", "approval", "Price or quantity changed after approval", "approval"),
        ("C4b", "receipt", "Price or quantity changed after goods receipt", "goods receipt"),
    ]:
        d = scoped(df, cid)
        cases = pd.Index(d["case_id"].unique(), name="case_id")
        chg = d[d["activity"].isin([CHANGE_PRICE, CHANGE_QTY])]
        last_chg = chg.groupby("case_id")["timestamp"].max().reindex(cases)
        n_chg = chg.groupby("case_id").size().reindex(cases).fillna(0).astype(int)
        f = first_times(d, [*APPROVALS, *RECEIPTS])
        a = f[APPROVALS if anchor == "approval" else RECEIPTS].min(axis=1)
        gap = _days(last_chg - a)
        out.append(
            _make(
                cid,
                name,
                "Approved terms altered without re-approval; value manipulation after commitment.",
                f"A 'Change Price' / 'Change Quantity' event later than the first {label} event.",
                a.notna(),
                last_chg > a,
                lambda c, gap=gap, label=label, n_chg=n_chg: (
                    f"{n_chg[c]} change(s), last {gap[c]:.1f} days after {label}"
                ),
                gap=gap,
            )
        )
    return out


def payment_terms_proxy(
    lag: pd.Series, vendor: pd.Series, company: pd.Series, min_n: int = MIN_VENDOR_ITEMS
) -> tuple[pd.Series, pd.Series]:
    """Empirical payment terms per item from observed invoice-to-payment days.

    The vendor's median is used when the vendor has at least ``min_n`` paid items, otherwise the
    company median, otherwise the overall median. The median is robust to the late tail that the
    control is looking for, but a vendor that is always paid late gets a late "term"; the
    control finds deviations from a vendor's usual behaviour, not breaches of a contract.
    """
    d = pd.DataFrame({"lag": lag, "vendor": vendor, "company": company}).dropna(subset=["lag"])
    v = d.groupby("vendor")["lag"].agg(["median", "size"])
    v_med = d["vendor"].map(v["median"]).where(d["vendor"].map(v["size"]) >= min_n)
    c_med = d["company"].map(d.groupby("company")["lag"].median())
    proxy = v_med.fillna(c_med).fillna(d["lag"].median())
    source = pd.Series("company", index=d.index).where(v_med.isna(), "vendor")
    return proxy, source


def late_payment(
    df: pd.DataFrame, grace: int = GRACE_DAYS, min_n: int = MIN_VENDOR_ITEMS
) -> ControlResult:
    d = scoped(df, "C5")
    f = first_times(d, [INVOICE_RECEIPT, PAYMENT])
    lag = _days(f[PAYMENT] - f[INVOICE_RECEIPT])
    lag = lag.where(lag >= 0)
    attrs = d.groupby("case_id")[[c for c in ("vendor", "company") if c in d]].first()
    attrs = attrs.reindex(lag.index)
    nan = pd.Series(float("nan"), index=lag.index)
    proxy, source = payment_terms_proxy(
        lag, attrs.get("vendor", nan), attrs.get("company", nan), min_n
    )
    proxy = proxy.reindex(lag.index)
    pop = lag.notna()
    own = source.index[source == "vendor"]
    extra = {
        "grace_days": grace,
        "min_vendor_items": min_n,
        "vendors_with_own_proxy": int(attrs.loc[own, "vendor"].nunique())
        if "vendor" in attrs
        else 0,
        "items_on_vendor_proxy": len(own),
        "items_on_company_proxy": int((source == "company").sum()),
        "proxy_days_p10_p50_p90": [float(proxy[pop].quantile(q)) for q in (0.1, 0.5, 0.9)],
    }
    return _make(
        "C5",
        f"Payment later than the usual days-to-pay plus {grace} days",
        "Late-payment interest, lost discounts and supplier-relationship risk.",
        "First 'Clear Invoice' later than first 'Record Invoice Receipt' by more than the "
        f"median invoice-to-payment time of the item's vendor (company if the vendor has fewer "
        f"than {min_n} paid items) plus {grace} days. The log has no contractual terms.",
        pop,
        lag > proxy + grace,
        lambda c: (
            f"paid {lag[c]:.0f} days after invoice receipt, usual for this vendor "
            f"{proxy[c]:.0f} days"
        ),
        gap=lag - proxy,
        extra=extra,
    )


def late_payment_sensitivity(df: pd.DataFrame, graces=(0, 7, 14, 30, 60)) -> list[dict]:
    """Late-payment flag rate for several tolerances above the empirical terms proxy."""
    out = []
    for g in graces:
        r = late_payment(df, g)
        out.append(
            {"grace_days": g, "flagged": r.flagged, "population": r.population, "rate": r.rate}
        )
    return out


def after_hours_approvals(df: pd.DataFrame) -> ControlResult:
    d = scoped(df, "C6")
    ap = _human(d[d["activity"].isin(APPROVALS)])
    ts = ap["timestamp"]
    off = (
        (ts.dt.hour < BUSINESS_HOURS[0])
        | (ts.dt.hour >= BUSINESS_HOURS[1])
        | (ts.dt.dayofweek >= 5)
    )
    cases = pd.Index(d["case_id"].unique(), name="case_id")
    pop = pd.Series(cases.isin(set(ap["case_id"])), index=cases)
    flag = pd.Series(cases.isin(set(ap.loc[off, "case_id"])), index=cases)
    when = ap[off].groupby("case_id").first()
    return _make(
        "C6",
        "Approvals outside business hours",
        "Approval by credentials outside normal working pattern; possible shared or misused ID.",
        f"Named-user approval event before {BUSINESS_HOURS[0]:02d}:00, from "
        f"{BUSINESS_HOURS[1]:02d}:00, or on a weekend.",
        pop,
        flag,
        lambda c: f"{when.loc[c, 'resource']} at {when.loc[c, 'timestamp']:%a %Y-%m-%d %H:%M}",
    )


def category_counts(df: pd.DataFrame) -> dict[str, int]:
    if "item_category" not in df:
        return {}
    cat = df.groupby("case_id")["item_category"].first()
    return {k: int(v) for k, v in cat.value_counts().items()}


def run_all(df: pd.DataFrame) -> list[ControlResult]:
    res: list[ControlResult] = []
    res += three_way_match(df)
    res.append(maverick_buying(df))
    res += segregation_of_duties(df)
    res += rework_after_approval(df)
    res.append(late_payment(df))
    res.append(after_hours_approvals(df))
    if "item_category" in df:
        cat = df.groupby("case_id")["item_category"].first()
        for r in res:
            pop = cat.loc[r.population_ids].value_counts()
            flg = cat.loc[r.flagged_ids].value_counts()
            r.by_category = {
                c: {"population": int(pop.get(c, 0)), "flagged": int(flg.get(c, 0))}
                for c in r.scope
            }
    return sorted(res, key=lambda r: r.id)


def slice_rates(
    results: list[ControlResult],
    cases: pd.DataFrame,
    attrs: tuple[str, ...] = ("company", "vendor", "spend_area"),
    top: int = 3,
) -> pd.DataFrame:
    """Largest contributors to each finding per attribute, with share, rate and lift."""
    rows = []
    for r in results:
        if not r.flagged:
            continue
        pop = cases.loc[r.population_ids]
        flagged = set(r.flagged_ids)
        for attr in attrs:
            if attr not in cases:
                continue
            g = (
                pd.DataFrame(
                    {
                        "v": pop[attr].astype("string").fillna("(blank)"),
                        "f": pop.index.isin(flagged),
                    }
                )
                .groupby("v")["f"]
                .agg(["size", "sum"])
            )
            g["rate"] = g["sum"] / g["size"]
            g = g[g["sum"] > 0]
            for v, row in g.sort_values("sum", ascending=False).head(top).iterrows():
                rows.append(
                    {
                        "control": r.id,
                        "attribute": attr,
                        "value": v,
                        "population": int(row["size"]),
                        "flagged": int(row["sum"]),
                        "share_of_flagged": row["sum"] / r.flagged,
                        "rate": row["rate"],
                        "lift": row["rate"] / r.rate,
                    }
                )
    return pd.DataFrame(rows)
