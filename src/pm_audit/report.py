"""Assemble the audit-style markdown report from the JSON/CSV results of the analyses."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .data import DATA_SOURCE, DOI, EXPECTED_EVENTS, LICENSE

RECOMMENDATION = {
    "C1a": "Enforce system-side GR-based invoice verification; block invoices without prior GR.",
    "C1b": "Make the GR-based verification flag mandatory and block posting when no GR exists.",
    "C1c": "Block payment runs for documents without a verified invoice; review released items.",
    "C1d": "Investigate each payment lacking an invoice event; confirm supporting documents.",
    "C1e": "Accepted by configuration; confirm the risk acceptance and review vendors with "
    "high rates, or move them to GR-based verification.",
    "C1f": "Review the listed vendors; block payment where no receipt is booked within a set "
    "time after invoice receipt.",
    "C2": "Require PO before invoice receipt (no-PO-no-pay); review retrospective POs by buyer.",
    "C3a": "Remove approval rights from PO creators; enforce workflow-based release strategies.",
    "C3b": "Separate purchasing and payment duties; monitor residual cases with a conflict report.",
    "C3c": "Separate goods receipt and invoice verification roles in the authorisation concept.",
    "C4a": "Re-trigger approval on price/quantity changes beyond a tolerance; log change reasons.",
    "C4b": "Restrict post-receipt price/quantity changes; require second approval and reason code.",
    "C5": "Compare the listed vendors' payment runs with their contract terms; review discount "
    "capture and late-payment interest.",
    "C6": "Review approver credentials and VPN/ERP logins for the flagged items; restrict "
    "off-hours release or require reason codes.",
}
SHORT_CAT = {
    "3-way match, invoice after GR": "3-way, after GR",
    "3-way match, invoice before GR": "3-way, before GR",
    "2-way match": "2-way",
    "Consignment": "consignment",
}


def _rating(cid: str, rate: float) -> str:
    if cid == "C1e":
        return "Info"
    return "High" if rate >= 0.10 else "Medium" if rate >= 0.02 else "Low"


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def findings_table(controls: list[dict]) -> str:
    rows = []
    for c in controls:
        ex = "<br>".join(f"`{e['case_id']}`: {e['detail']}" for e in c["examples"][:3]) or "none"
        cats = "<br>".join(
            f"{SHORT_CAT.get(k, k)}: {v['flagged']:,} / {v['population']:,}"
            for k, v in c["by_category"].items()
            if v["population"]
        )
        rows.append(
            [
                c["id"],
                f"{c['name']}. {c['definition']}",
                f"{c['risk']} (indicative rating: {_rating(c['id'], c['rate'])})",
                f"{c['flagged']:,} of {c['population']:,} ({_pct(c['rate'])})<br>{cats}",
                ex,
                RECOMMENDATION.get(c["id"], ""),
            ]
        )
    return _md_table(
        ["ID", "Observation", "Risk", "Flagged / tested", "Examples", "Recommendation"], rows
    )


def scope_table(ctl: dict) -> str:
    cats = list(ctl["categories"])
    rows = [[c["id"], *("yes" if k in c["scope"] else "-" for k in cats)] for c in ctl["controls"]]
    rows.append(["Items", *(f"{ctl['categories'][k]:,}" for k in cats)])
    return _md_table(["Control", *(SHORT_CAT.get(k, k) for k in cats)], rows)


def sod_table(controls: list[dict]) -> str:
    rows = []
    for c in controls:
        if not c["id"].startswith("C3"):
            continue
        a = c["extra"]["all_users"]
        rows.append(
            [
                c["id"],
                f"{c['flagged']:,} / {c['population']:,} ({_pct(c['rate'])})",
                f"{a['flagged']:,} / {a['population']:,} ({_pct(a['rate'])})",
                f"{a['flagged_only_system_users']:,}",
            ]
        )
    return _md_table(
        [
            "Control",
            "Human users only",
            "All users incl. batch and NONE",
            "Flagged only through system users",
        ],
        rows,
    )


def slice_table(slices: pd.DataFrame, controls: list[dict], min_flagged: int = 50) -> str:
    keep = {c["id"] for c in controls if c["flagged"] >= min_flagged and c["id"] != "C1e"}
    s = slices[slices["control"].isin(keep)]
    rows = [
        [
            r.control,
            r.attribute.replace("_", " "),
            r.value,
            f"{int(r.flagged):,}",
            _pct(r.share_of_flagged),
            f"{int(r.population):,}",
            _pct(r.rate),
        ]
        for r in s.itertuples()
    ]
    return _md_table(
        [
            "Control",
            "Slice",
            "Value",
            "Flagged",
            "Share of all flagged",
            "Tested in slice",
            "Rate in slice",
        ],
        rows,
    )


def conformance_table(conf: dict) -> str:
    rows = []
    for cat, c in conf["categories"].items():
        tr, al = c["token_replay"], c["alignments"]
        rows.append(
            [
                cat,
                f"{c['cases']:,}",
                f"{c['projected_variants']:,}",
                f"{tr['fit_cases']:,} ({_pct(tr['fit_share'])})",
                f"{tr['weighted_trace_fitness']:.3f}",
                f"{al['alignment_fitness']:.3f}",
                f"{al['variants_aligned']:,} / {_pct(al['coverage_share'])}",
            ]
        )
    return _md_table(
        [
            "Item category",
            "Items",
            "Projected variants",
            "Replay without deviation",
            "Token-replay fitness",
            "Alignment fitness",
            "Variants aligned / case coverage",
        ],
        rows,
    )


def deviation_table(conf: dict) -> str:
    rows = []
    for cat, c in conf["categories"].items():
        al = c["alignments"]
        for kind, d in (
            ("missing", al["missing_step_cases"]),
            ("repeated or misplaced", al["extra_step_cases"]),
        ):
            for k, n in list(d.items())[:3]:
                rows.append([cat, kind, k, f"{n:,}", _pct(n / al["cases_covered"])])
    return _md_table(["Item category", "Deviation", "Activity", "Cases", "Share of aligned"], rows)


def write(reports: Path, docs: Path) -> Path:
    res = reports / "results"
    disc = json.loads((res / "discovery.json").read_text())
    conf = json.loads((res / "conformance.json").read_text())
    ctl = json.loads((res / "controls.json").read_text())
    slices = pd.read_csv(res / "slices.csv")
    v, t = disc["variants"], disc["throughput"]
    sample = f"random sample of {disc['sample']:,} cases" if disc.get("sample") else "full log"
    controls = ctl["controls"]
    by_id = {c["id"]: c for c in controls}
    top = sorted((c for c in controls if c["id"] != "C1e"), key=lambda c: -c["flagged"])[:3]
    c5 = by_id["C5"]

    L: list[str] = []
    L.append("# Purchase-to-pay process audit with process mining\n")
    L.append(
        f"Data: BPI Challenge 2019 (doi:{DOI}), {LICENSE}. Scope analysed: {sample}, "
        f"{ctl['cases']:,} purchase order items (cases), {disc['events']:,} events. "
        "Every figure below is generated by `pm-audit report`; nothing is hand-edited.\n"
    )
    L.append(f"Source: {DATA_SOURCE}\n")
    if not disc.get("sample"):
        L.append(
            f"Data note: {EXPECTED_EVENTS - disc['events']:,} events carrying the placeholder "
            "date 1948-01-26 (vendor invoices and debit memos) were excluded.\n"
        )
    L.append("## Summary\n")
    L.append(
        f"- The process has {v['variants']:,} distinct variants; the top 10 cover "
        f"{_pct(v['top10_share'])} of cases and {_pct(v['singleton_share_of_variants'])} of "
        "variants occur exactly once (long tail)."
    )
    L.append(
        f"- Median throughput is {t['median_days']:.0f} days (P90 {t['p90_days']:.0f}, "
        f"P95 {t['p95_days']:.0f})."
    )
    L.append(
        f"- Each item category is checked against its own normative model: "
        f"{_pct(conf['fit_share'])} of the {conf['modelled_cases']:,} modelled items replay "
        "without deviation (per category below)."
    )
    L.append(
        "- Largest control exceptions by count: "
        + "; ".join(f"{c['id']} {c['name']} ({c['flagged']:,}, {_pct(c['rate'])})" for c in top)
        + ".\n"
    )

    L.append("## Findings\n")
    L.append(
        "One case is one purchase order item. BPI 2019 assigns each item to one of four "
        "categories, and the controls differ between them: an invoice ahead of the receipt is "
        "allowed by configuration on 'invoice before GR' items and a bypass on GR-based "
        "items, and consignment stock is settled without an invoice chain. Each test "
        "therefore runs only on the categories marked in the scope table, and the flagged "
        "and tested counts are given per category. Each test covers 100% of its population. "
        "The rating is indicative and based only on the flag rate "
        "(High >= 10%, Medium >= 2%).\n"
    )
    L.append(scope_table(ctl) + "\n")
    L.append(findings_table(controls) + "\n")

    L.append("### Late payment against an empirical terms proxy\n")
    ex = c5["extra"]
    p10, p50, p90 = ex["proxy_days_p10_p50_p90"]
    L.append(
        "The log has no contractual payment terms, so a flat threshold such as 60 days would "
        "treat a vendor on 14-day terms and one on 90-day terms the same. Instead each item is "
        "compared with the median invoice-to-payment time of its own vendor, taken over all of "
        f"that vendor's paid items. Vendors with fewer than {ex['min_vendor_items']} paid items "
        "use their company's median. An item is flagged when it is paid more than "
        f"{ex['grace_days']} days (about one payment-run cycle) later than that median. "
        f"{ex['vendors_with_own_proxy']:,} vendors have their own proxy, covering "
        f"{ex['items_on_vendor_proxy']:,} items; {ex['items_on_company_proxy']:,} items use the "
        f"company median. The proxies run from {p10:.0f} days (P10) through {p50:.0f} (median) "
        f"to {p90:.0f} (P90). A vendor that is always paid late gets a late proxy, so the test "
        "finds departures from the vendor's usual behaviour, not contract breaches.\n"
    )
    L.append(
        _md_table(
            ["Tolerance above vendor median", "Flagged / tested", "Rate"],
            [
                [
                    f"{x['grace_days']} days",
                    f"{x['flagged']:,} / {x['population']:,}",
                    _pct(x["rate"]),
                ]
                for x in ctl["late_payment_sensitivity"]
            ],
        )
        + "\n"
    )

    L.append("### Segregation of duties: human users and system users\n")
    L.append(
        "Batch jobs (`batch_*`) and `NONE` post documents under shared ids. A match on "
        "them does not show one person doing two steps, so the headline figures count "
        "named users only. The table shows what including them would do.\n"
    )
    L.append(sod_table(controls) + "\n")

    L.append("## Root-cause slices\n")
    L.append(
        "The three largest contributors to each finding by company, vendor and spend "
        "area, for findings with at least 50 flagged items. Share is the part of all "
        "flagged items of that finding; rate is flagged over tested items inside the "
        "slice. Vendor ids are as anonymised in the log. Full data: "
        "`reports/results/slices.csv`.\n"
    )
    L.append(slice_table(slices, controls) + "\n")

    L.append("## Process discovery\n")
    L.append("![Process map](../docs/process_map.png)\n")
    L.append(
        "Directly-follows map of eight core activities. Nodes show event counts; edges show "
        "occurrences and the median time between the two events (edges below 10% of the "
        "largest are hidden). Other activities are removed before edges are computed, so "
        "an edge can span them.\n"
    )
    L.append(
        _md_table(
            ["#", "Cases", "Share", "Variant"],
            [
                [x["rank"], f"{x['cases']:,}", _pct(x["share"]), x["variant"]]
                for x in disc["top_variants"]
            ],
        )
        + "\n"
    )
    L.append("![Variant Pareto](../docs/variant_pareto.png)\n")
    L.append(
        f"{v['variants_for_80pct']:,} variants are needed to cover 80% of cases; "
        f"{v['tail_cases_outside_top10']:,} cases sit outside the top 10.\n"
    )
    L.append("![Throughput](../docs/throughput.png)\n")
    L.append(
        _md_table(
            ["From", "To", "Cases", "Median days", "P90 days", "Negative (wrong order)"],
            [
                [
                    s["from"],
                    s["to"],
                    f"{s['cases']:,}",
                    f"{s['median_days']:.1f}",
                    f"{s['p90_days']:.1f}",
                    _pct(s["negative_share"]),
                ]
                for s in disc["segments"]
            ],
        )
        + "\n"
    )
    im = disc["inductive_miner"]
    L.append(
        f"<details><summary>Inductive miner process tree (noise threshold "
        f"{im['noise_threshold']}, {im['cases_used']:,} cases)</summary>\n\n```\n"
        f"{im['tree']}\n```\n\n</details>\n"
    )

    L.append("## Conformance checking\n")
    L.append(
        "Each item category is replayed against its own process tree. The log is projected "
        "on the seven activities of the models first; other events are listed separately. "
        "Approval is optional because the log has an approval step for only a few percent "
        "of items. Consignment items are not modelled.\n"
    )
    for cat, c in conf["categories"].items():
        L.append(f"{cat}:\n\n```\n{c['model']}\n```\n")
    L.append(conformance_table(conf) + "\n")
    L.append("![Conformance deviations](../docs/conformance_deviations.png)\n")
    L.append(
        "Deviation view: shading shows the larger of the missing and the repeated or "
        "misplaced share of aligned cases at each step. A case counts once per activity.\n"
    )
    L.append(deviation_table(conf) + "\n")
    L.append("Most frequent projected variant per category:\n")
    L.append(
        _md_table(
            ["Item category", "Cases", "Share", "Variant"],
            [
                [cat, f"{x['cases']:,}", _pct(x["share"]), x["variant"]]
                for cat, c in conf["categories"].items()
                for x in c["top_projected_variants"][:1]
            ],
        )
        + "\n"
    )

    L.append("## Limitations\n")
    L.append("- Case = purchase order item; amounts are cumulative net worth, not invoice values.")
    L.append(
        "- The log has no payment terms and no vendor master data. Late payment uses an "
        "empirical proxy (above), and a vendor-creator-also-pays test cannot be run."
    )
    L.append(
        "- There is no explicit approval event. `Change Approval for Purchase Order` and "
        "`Release Purchase Order` are the closest, and they exist for only a few percent "
        "of items, so the approval-based tests cover a small population."
    )
    L.append(
        "- `Record Service Entry Sheet` is always posted under a system user, so "
        "the receipt-side segregation test covers goods receipts only."
    )
    L.append(
        "- Items still open when the log ends (April 2020) appear as missing steps in the "
        "conformance results. Timestamps are used as recorded."
    )
    L.append(
        "- Events with identical timestamps keep their order in the source file; variant "
        "counts can differ slightly from other tools."
    )
    L.append(
        "- Flags are exceptions for follow-up, not proof of error or fraud; many have "
        "legitimate causes (blanket orders, credit memos, partial deliveries).\n"
    )
    path = reports / "p2p_process_audit.md"
    path.write_text("\n".join(L))
    return path
