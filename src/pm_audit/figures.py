"""Small, committed figures: process map and conformance view (Graphviz), Pareto, throughput."""

from __future__ import annotations

from pathlib import Path

import graphviz
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import conformance as cf  # noqa: E402
from . import discovery  # noqa: E402

INK, MUTED, ACCENT, GRID = "#1f2933", "#7b8794", "#1f6feb", "#e4e7eb"
SHORT = {
    "Create Purchase Requisition Item": "Create PR item",
    "Create Purchase Order Item": "Create PO item",
    "Record Service Entry Sheet": "Service entry\nsheet",
    "Vendor creates invoice": "Vendor creates\ninvoice",
    "Record Goods Receipt": "Record goods\nreceipt",
    "Record Invoice Receipt": "Record invoice\nreceipt",
    "Clear Invoice": "Clear invoice\n(payment)",
    "Remove Payment Block": "Remove payment\nblock",
    "Change Approval for Purchase Order": "Change\napproval",
    "Receive Order Confirmation": "Receive order\nconfirmation",
    "Cancel Invoice Receipt": "Cancel invoice\nreceipt",
}


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)


PROCESS_MAP_ACTIVITIES = [
    "Create Purchase Requisition Item",
    "Create Purchase Order Item",
    "Record Goods Receipt",
    "Record Service Entry Sheet",
    "Vendor creates invoice",
    "Record Invoice Receipt",
    "Remove Payment Block",
    "Clear Invoice",
]
FONT = "Helvetica"


def _render(g: graphviz.Digraph, path: Path) -> None:
    """Write the graph as PNG; needs the Graphviz ``dot`` binary on PATH."""
    try:
        path.write_bytes(g.pipe(format="png"))
    except graphviz.ExecutableNotFound as e:
        raise SystemExit(
            "Graphviz `dot` not found; install it (brew install graphviz) to draw the process maps."
        ) from e


def _days(d: float) -> str:
    return f"{d * 24:.0f} h" if d < 1 else f"{d:.0f} d"


def process_map_graph(df: pd.DataFrame, min_edge_share: float = 0.1) -> graphviz.Digraph:
    """Directly-follows map of the core activities: event counts on nodes, occurrences and
    median waiting time on edges (edges below ``min_edge_share`` of the largest are dropped)."""
    edges = discovery.dfg_performance(df, PROCESS_MAP_ACTIVITIES)
    edges = edges[edges["source"] != edges["target"]]
    edges = edges[edges["count"] >= min_edge_share * edges["count"].max()]
    acts = df["activity"].value_counts()
    top = edges["count"].max()
    g = graphviz.Digraph(
        graph_attr={
            "rankdir": "LR",
            "dpi": "100",
            "nodesep": "0.35",
            "ranksep": "0.9",
            "pad": "0.2",
            "fontname": FONT,
            "bgcolor": "white",
        },
        node_attr={
            "shape": "box",
            "style": "rounded,filled",
            "fillcolor": "white",
            "color": INK,
            "fontname": FONT,
            "fontsize": "11",
            "fontcolor": INK,
        },
        edge_attr={"fontname": FONT, "fontsize": "9", "color": ACCENT, "fontcolor": INK},
    )
    used = set(edges["source"]) | set(edges["target"])
    for a in PROCESS_MAP_ACTIVITIES:
        if a in used:
            g.node(a, f"{SHORT.get(a, a)}\n{acts[a]:,} events")
    for r in edges.itertuples():
        g.edge(
            r.source,
            r.target,
            label=f"{r.count:,}\n{_days(r.median_days)}",
            penwidth=f"{0.6 + 5 * (r.count / top) ** 0.5:.2f}",
        )
    return g


def process_map(df: pd.DataFrame, path: Path) -> None:
    _render(process_map_graph(df), path)


def _shade(share: float) -> str:
    """White to red as the deviation share goes from 0 to 40% of cases."""
    k = min(share / 0.4, 1.0)
    r, g, b = (int(255 + (c - 255) * k) for c in (0xE0, 0x7A, 0x6E))
    return f"#{r:02x}{g:02x}{b:02x}"


def conformance_graph(conf: dict) -> graphviz.Digraph:
    """One normative model per item category; every step shows the share of aligned cases
    where it is missing (model move) or repeated / out of place (log move)."""
    g = graphviz.Digraph(
        graph_attr={
            "rankdir": "LR",
            "dpi": "100",
            "pad": "0.2",
            "ranksep": "0.5",
            "nodesep": "0.3",
            "fontname": FONT,
            "fontsize": "12",
            "labeljust": "l",
        },
        node_attr={
            "shape": "box",
            "style": "rounded,filled",
            "color": INK,
            "fontname": FONT,
            "fontsize": "10",
            "fontcolor": INK,
        },
        edge_attr={"color": MUTED, "arrowsize": "0.7"},
    )
    for ci, (cat, c) in enumerate(conf["categories"].items()):
        al = c["alignments"]
        n = al["cases_covered"]
        fit = c["token_replay"]["fit_share"]
        with g.subgraph(name=f"cluster_{ci}") as sg:
            sg.attr(
                label=f"{cat}: {c['cases']:,} items, {fit:.1%} replay without deviation",
                color=GRID,
                style="rounded",
            )
            ids = []
            for si, (label, acts) in enumerate(cf.STEPS):
                keys = {cf._label(a) for a in acts}
                miss = max((al["missing_step_cases"].get(k, 0) for k in keys), default=0)
                ext = max((al["extra_step_cases"].get(k, 0) for k in keys), default=0)
                nid = f"c{ci}s{si}"
                ids.append(nid)
                sg.node(
                    nid,
                    f"{label}\nmissing {miss / n:.1%}\nrepeated or misplaced {ext / n:.1%}",
                    fillcolor=_shade(max(miss, ext) / n),
                )
            po, ap, rc, iv, pay = ids
            sg.edge(po, ap, style="dashed")
            if cat == cf.AFTER_GR:
                sg.edge(ap, rc, style="dashed")
                sg.edge(rc, iv)
                sg.edge(iv, pay)
            else:  # receipt and invoice in either order
                sg.edge(ap, rc, style="dashed")
                sg.edge(ap, iv, style="dashed")
                sg.edge(rc, pay)
                sg.edge(iv, pay)
                sg.body.append(f"{{rank=same; {rc}; {iv}}}")
    return g


def conformance_view(conf: dict, path: Path) -> None:
    _render(conformance_graph(conf), path)


def variant_pareto(v: pd.DataFrame, path: Path, top: int = 25) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    head = v.head(top)
    ax.bar(head["rank"], head["share"] * 100, color=ACCENT, width=0.75)
    ax.set_xlim(0.3, top + 0.7)
    ax.set_xlabel(f"Variant rank (top {top} of {len(v):,})", color=MUTED)
    ax.set_ylabel("Share of cases (%)", color=MUTED)
    ax2 = ax.twinx()
    ax2.plot(head["rank"], head["cum_share"] * 100, color=INK, lw=1.8)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("Cumulative share (%)", color=MUTED)
    ax2.tick_params(colors=MUTED, labelsize=9)
    ax2.spines["top"].set_visible(False)
    _style(ax)
    ax.set_title(
        f"Variant Pareto: top {top} variants cover {head['cum_share'].iloc[-1]:.0%} of cases; "
        f"{(v['cases'] == 1).sum():,} variants occur once",
        fontsize=10.5,
        color=INK,
        loc="left",
    )
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def throughput_hist(days: pd.Series, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.2))
    d = days[days > 0]
    bins = np.logspace(np.log10(max(d.min(), 0.01)), np.log10(d.max()), 60)
    ax.hist(d, bins=bins, color=ACCENT)
    ax.set_xscale("log")
    for q, ls in [(0.5, "-"), (0.9, "--")]:
        ax.axvline(d.quantile(q), color=INK, ls=ls, lw=1.3)
        left = q < 0.6
        ax.text(
            d.quantile(q) * (0.93 if left else 1.07),
            ax.get_ylim()[1] * 0.97,
            f"P{int(q * 100)}: {d.quantile(q):.0f} d",
            color=INK,
            fontsize=9,
            va="top",
            ha="right" if left else "left",
        )
    ax.set_xlabel("Case throughput time, first to last event (days, log scale)", color=MUTED)
    ax.set_ylabel("Cases (PO items)", color=MUTED)
    _style(ax)
    ax.set_title("Throughput time distribution", fontsize=10.5, color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def make_discovery(df: pd.DataFrame, v, days, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    process_map(df, out / "process_map.png")
    variant_pareto(v, out / "variant_pareto.png")
    throughput_hist(days, out / "throughput.png")
