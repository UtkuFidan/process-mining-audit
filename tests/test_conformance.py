import pandas as pd

from pm_audit import conformance as cf
from pm_audit import controls as c


def test_conformance_per_category(log):
    res = cf.run(log, top=50)
    cats = res["categories"]
    assert set(cats) == {c.AFTER_GR, c.BEFORE_GR, c.TWO_WAY}  # consignment is not modelled
    assert res["not_modelled_cases"] == 1
    after = cats[c.AFTER_GR]
    assert after["cases"] == 6
    assert after["token_replay"]["fit_cases"] == 4  # ok, ok2, sod, rework
    assert after["alignments"]["perfectly_aligned_cases"] == 4
    # no_gr has no receipt (inv_first has one, but late; the optimal alignment may skip either step)
    assert after["alignments"]["missing_step_cases"]["Goods receipt / service entry sheet"] >= 1
    assert cats[c.BEFORE_GR]["token_replay"]["fit_cases"] == 0
    assert cats[c.TWO_WAY]["token_replay"]["fit_cases"] == 1  # no receipt expected
    assert res["fit_cases"] == 5
    assert res["modelled_cases"] == 10


def test_invoice_before_receipt_fits_only_the_relaxed_model(log):
    inv_first = log[log["case_id"] == "inv_first"].assign(item_category=c.BEFORE_GR)
    strict = cf.run_model(log[log["case_id"] == "inv_first"], cf.MODELS[c.AFTER_GR], top=5)
    relaxed = cf.run_model(inv_first, cf.MODELS[c.BEFORE_GR], top=5)
    assert strict["token_replay"]["fit_cases"] == 0
    assert relaxed["token_replay"]["fit_cases"] == 1


def test_alignment_counts_a_case_once_per_activity(log):
    ok = log[log["case_id"] == "ok"]
    inv = ok[ok["activity"] == c.INVOICE_RECEIPT]
    repeats = [inv.assign(timestamp=inv["timestamp"] + pd.Timedelta(hours=h)) for h in (1, 2)]
    rep = pd.concat([ok, *repeats]).sort_values("timestamp")
    al = cf.run_model(rep, cf.MODELS[c.AFTER_GR], top=5)["alignments"]
    assert al["extra_step_cases"][c.INVOICE_RECEIPT] == 1  # two repeats, one case
