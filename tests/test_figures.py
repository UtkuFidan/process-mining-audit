from pm_audit import conformance as cf
from pm_audit import figures


def test_process_map_source_has_counts_and_median_waits(log):
    src = figures.process_map_graph(log, min_edge_share=0).source
    assert "Create PO item" in src and "11 events" in src
    assert "->" in src and "label=" in src


def test_process_map_drops_rare_edges(log):
    full = figures.process_map_graph(log, min_edge_share=0).source.count("->")
    pruned = figures.process_map_graph(log, min_edge_share=0.9).source.count("->")
    assert 0 < pruned < full


def test_conformance_graph_has_one_cluster_per_category(log):
    src = figures.conformance_graph(cf.run(log, top=50)).source
    assert src.count("subgraph cluster_") == 3
    assert "3-way match, invoice after GR" in src and "repeated or misplaced" in src
