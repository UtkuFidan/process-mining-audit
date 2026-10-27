"""Command line interface: ``pm-audit fetch | discover | conformance | controls | report``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import conformance as cf
from . import controls as ct
from . import data, discovery, figures, report


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n")


def cmd_fetch(a) -> None:
    data.fetch(Path(a.data_dir), force=a.force)
    df = data.load(Path(a.data_dir))
    print(f"{len(df):,} events, {df['case_id'].nunique():,} cases cached")


def cmd_discover(a) -> None:
    df = data.load(Path(a.data_dir), a.sample)
    v = discovery.variants(df)
    days = discovery.throughput(df)
    _, acts = discovery.dfg(df)
    tree, n_tree = discovery.inductive_tree(df, a.noise)
    res = {
        "events": len(df),
        "sample": a.sample,
        "variants": discovery.variant_summary(v),
        "top_variants": [
            {
                "rank": int(r["rank"]),
                "cases": int(r["cases"]),
                "share": float(r["share"]),
                "variant": " > ".join(r["variant"]),
            }
            for _, r in v.head(10).iterrows()
        ],
        "throughput": discovery.throughput_summary(days),
        "segments": discovery.segment_times(df),
        "activities": {k: int(n) for k, n in acts.most_common()},
        "inductive_miner": {"noise_threshold": a.noise, "cases_used": n_tree, "tree": str(tree)},
    }
    out = Path(a.reports_dir) / "results"
    _write_json(out / "discovery.json", res)
    figures.make_discovery(df, v, days, Path(a.docs_dir))
    print(json.dumps(res["variants"], indent=2))


def cmd_conformance(a) -> None:
    df = data.load(Path(a.data_dir), a.sample)
    res = cf.run(df, a.top)
    _write_json(Path(a.reports_dir) / "results" / "conformance.json", res)
    figures.conformance_view(res, Path(a.docs_dir) / "conformance_deviations.png")
    for cat, c in res["categories"].items():
        print(f"{cat:34} {c['cases']:>8,} items  fit {c['token_replay']['fit_share']:.1%}")


def cmd_controls(a) -> None:
    df = data.load(Path(a.data_dir), a.sample)
    results = ct.run_all(df)
    slices = ct.slice_rates(results, data.case_table(df))
    out = Path(a.reports_dir) / "results"
    _write_json(
        out / "controls.json",
        {
            "cases": int(df["case_id"].nunique()),
            "categories": ct.category_counts(df),
            "controls": [r.to_dict() for r in results],
            "late_payment_sensitivity": ct.late_payment_sensitivity(df),
        },
    )
    slices.to_csv(out / "slices.csv", index=False)
    for r in results:
        print(f"{r.id:4} {r.flagged:>8,} / {r.population:>8,}  {r.rate:6.2%}  {r.name}")


def cmd_report(a) -> None:
    out = Path(a.reports_dir)
    for name, fn in (
        ("discovery", cmd_discover),
        ("conformance", cmd_conformance),
        ("controls", cmd_controls),
    ):
        if a.refresh or not (out / "results" / f"{name}.json").exists():
            fn(a)
    path = report.write(out, Path(a.docs_dir))
    print(f"wrote {path}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="pm-audit", description=__doc__)
    p.add_argument("--data-dir", default="data")
    p.add_argument("--reports-dir", default="reports")
    p.add_argument("--docs-dir", default="docs")
    p.add_argument("--sample", type=int, default=None, help="random sample of N cases")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download and cache the BPI 2019 log")
    f.add_argument("--force", action="store_true")
    f.set_defaults(fn=cmd_fetch)
    d = sub.add_parser("discover", help="variants, throughput, DFG, inductive miner, figures")
    d.add_argument("--noise", type=float, default=0.2)
    d.set_defaults(fn=cmd_discover)
    c = sub.add_parser("conformance", help="token replay and alignments vs normative model")
    c.add_argument("--top", type=int, default=500, help="variants to align")
    c.set_defaults(fn=cmd_conformance)
    k = sub.add_parser("controls", help="full-population control tests and root-cause slices")
    k.set_defaults(fn=cmd_controls)
    r = sub.add_parser("report", help="build reports/p2p_process_audit.md")
    r.add_argument("--noise", type=float, default=0.2)
    r.add_argument("--top", type=int, default=500)
    r.add_argument("--refresh", action="store_true", help="re-run all analyses")
    r.set_defaults(fn=cmd_report)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
