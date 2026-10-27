from pm_audit import discovery as d


def test_variants_count_and_pareto(log):
    v = d.variants(log)
    assert v["cases"].sum() == 11
    assert v["cases"].iloc[0] == 3  # "ok", "ok2" and "sod" share one variant
    assert len(v) == 8
    assert abs(v["cum_share"].iloc[-1] - 1.0) < 1e-9
    s = d.variant_summary(v)
    assert s["singleton_variants"] == 6
    assert s["top1_share"] == 3 / 11


def test_throughput(log):
    days = d.throughput(log)
    assert round(days["ok"]) == 30
    assert days.index.nunique() == 11


def test_dfg(log):
    edges, acts = d.dfg(log)
    assert acts["Create Purchase Order Item"] == 11
    assert edges[("Record Goods Receipt", "Record Invoice Receipt")] == 3


def test_dfg_performance_counts_and_median_wait(log):
    e = d.dfg_performance(log, ["Create Purchase Order Item", "Record Goods Receipt"])
    row = e[(e.source == "Create Purchase Order Item") & (e.target == "Record Goods Receipt")]
    assert len(e) == 1  # only these two activities are kept
    assert row["count"].iloc[0] == 8  # every case with a PO and a receipt, receipt after the PO
    assert row["median_days"].iloc[0] == 8.0
