import pandas as pd

from pm_audit import controls as c


def by_id(results):
    return {r.id: r for r in results}


def flagged(r):
    return set(r.flagged_ids)


def test_three_way_match(log):
    r = by_id(c.three_way_match(log))
    assert flagged(r["C1a"]) == {"inv_first"}
    assert r["C1a"].population == 5  # GR-based items with both a receipt and an invoice
    assert flagged(r["C1b"]) == {"no_gr"}
    assert r["C1e"].population == 1 and r["C1e"].flagged == 0  # invoice-before-GR items
    assert flagged(r["C1f"]) == {"pay_no_gr"}
    assert flagged(r["C1c"]) == {"pay_first"}
    assert flagged(r["C1d"]) == {"no_inv"}
    assert r["C1c"].examples[0]["case_id"] == "pay_first"


def test_controls_run_only_on_their_categories(log):
    results = by_id(c.run_all(log))
    for cid, cats in c.SCOPES.items():
        scope = log[log["item_category"].isin(cats)]["case_id"]
        assert set(results[cid].population_ids) <= set(scope), cid
    # consignment stock is outside every invoice-chain test; the two-way item has no receipt tests
    for cid in ("C1a", "C1b", "C1c", "C1d", "C1e", "C1f", "C2", "C5"):
        assert "consign" not in set(results[cid].population_ids)
    for cid in ("C1a", "C1b", "C1e", "C1f", "C3c"):
        assert "two_way" not in set(results[cid].population_ids)
    assert "two_way" in set(results["C1c"].population_ids)
    assert "consign" in flagged(results["C4b"])


def test_by_category_adds_up(log):
    for r in c.run_all(log):
        assert sum(v["population"] for v in r.by_category.values()) == r.population
        assert sum(v["flagged"] for v in r.by_category.values()) == r.flagged
        assert set(r.by_category) == set(r.scope)


def test_maverick_buying(log):
    r = c.maverick_buying(log)
    assert flagged(r) == {"pay_first"}
    assert r.population == 9  # invoiced items with a PO and an invoice event


def test_segregation_of_duties_human_only(log):
    r = by_id(c.segregation_of_duties(log))
    assert flagged(r["C3a"]) == {"sod"}
    assert flagged(r["C3b"]) == {"sod"}
    assert flagged(r["C3c"]) == {"sod"}
    assert r["C3a"].population == 4  # system users (batch_*, NONE) are not counted


def test_segregation_of_duties_all_users_view(log):
    b = by_id(c.segregation_of_duties(log))["C3b"]
    assert b.population == 1  # one case with a named user on both create and clear
    a = b.extra["all_users"]
    assert a["population"] == 10 and a["flagged"] == 2  # batch_9 creates and clears pay_no_gr
    assert a["flagged_only_system_users"] == 1


def test_rework(log):
    r = by_id(c.rework_after_approval(log))
    assert flagged(r["C4a"]) == {"rework"}
    assert flagged(r["C4b"]) == {"rework", "consign"}


def test_payment_terms_proxy_uses_vendor_then_company_median():
    lag = pd.Series([10.0, 12.0, 14.0, 90.0, 30.0], index=list("abcde"))
    vendor = pd.Series(["V", "V", "V", "W", "W"], index=lag.index)
    company = pd.Series(["C"] * 5, index=lag.index)
    proxy, source = c.payment_terms_proxy(lag, vendor, company, min_n=3)
    assert proxy.loc[list("abc")].eq(12.0).all() and source.loc[list("abc")].eq("vendor").all()
    assert proxy.loc[["d", "e"]].eq(14.0).all()  # W has 2 items: company median
    assert source.loc[["d", "e"]].eq("company").all()


def test_late_payment_against_vendor_median(log):
    r = c.late_payment(log, grace=14, min_n=1)
    assert flagged(r) == {"rework"}  # 60 days against a V1 median of 18
    assert r.population == 8  # pay_first (payment before invoice) has no valid lag
    assert "usual for this vendor 18 days" in r.examples[0]["detail"]
    assert r.extra["items_on_vendor_proxy"] == 8


def test_late_payment_falls_back_to_company_median(log):
    r = c.late_payment(log, grace=14, min_n=100)
    assert flagged(r) == {"rework"}
    assert r.extra["items_on_vendor_proxy"] == 0
    assert r.extra["items_on_company_proxy"] == r.population


def test_late_payment_sensitivity_is_monotone(log):
    s = c.late_payment_sensitivity(log, graces=(0, 14, 60))
    assert [x["flagged"] for x in s] == sorted((x["flagged"] for x in s), reverse=True)
    assert all(x["population"] == s[0]["population"] for x in s)


def test_after_hours_approvals(log):
    r = c.after_hours_approvals(log)
    assert flagged(r) == {"sod"}
    assert r.population == 4
    assert "Sat" in r.examples[0]["detail"]


def test_run_all_and_slices(log):
    results = c.run_all(log)
    assert [r.id for r in results] == sorted(c.SCOPES)
    cases = log.groupby("case_id")[["company", "vendor"]].first()
    s = c.slice_rates(results, cases, attrs=("company", "vendor"))
    row = s[(s.control == "C1c") & (s.attribute == "company")].iloc[0]
    assert row.value == "C2" and row.flagged == 1 and row.share_of_flagged == 1.0
    assert (s["flagged"] > 0).all()
    top = s[(s.control == "C4b") & (s.attribute == "vendor")]
    assert set(top["value"]) == {"V3", "V1"}  # consign (V3) and rework (V1), one each
