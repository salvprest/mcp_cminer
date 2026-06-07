
import re
from dataclasses import dataclass


@dataclass
class PatternInfo:
    id: int
    nodes: list[tuple]  # (node_id, labels: list[str])
    edges: list[tuple]  # (src, dst, label)
    support: float
    frequency: int
    mappings: str | None = None  # raw mappings text from the info: block


def parse_cminer_output(text: str) -> list[PatternInfo]:
    patterns: list[PatternInfo] = []
    blocks = re.split(r'-{5,}', text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        pat_id = None
        nodes: list[tuple] = []
        edges: list[tuple] = []
        support = 0.0
        frequency = 0
        mappings_lines: list[str] = []
        in_info = False
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            if in_info:
                mappings_lines.append(line)
                continue
            if line.lower() == "info:":
                in_info = True
                continue
            m = re.match(r't\s+#\s+(\d+)', line)
            if m:
                pat_id = int(m.group(1))
                continue
            m = re.match(r'v\s+(\d+)(?:\s+(.*))?', line)
            if m:
                node_id = int(m.group(1))
                labels = m.group(2).strip().split() if m.group(2) else []
                nodes.append((node_id, labels))
                continue
            m = re.match(r'e\s+(\d+)\s+(\d+)(?:\s+(\S+))?', line)
            if m:
                edges.append((int(m.group(1)), int(m.group(2)), m.group(3) or ""))
                continue
            m = re.match(r's\s+([\d.]+)', line)
            if m:
                support = float(m.group(1))
                continue
            m = re.match(r'f\s+(\d+)', line)
            if m:
                frequency = int(m.group(1))
                continue
        mappings = "\n".join(mappings_lines) if mappings_lines else None
        if pat_id is not None:
            patterns.append(PatternInfo(
                id=pat_id, nodes=nodes, edges=edges,
                support=support, frequency=frequency,
                mappings=mappings,
            ))
    return patterns


def resolve_occurrence_highlights(
    occurrences: list[dict],
    graph,
) -> tuple[set, set]:
    """
    Derive the sets of highlighted nodes and edges from CMiner occurrence mappings.

    CMiner's internal DBGraph rekeys edges as integers (0, 1, …) when loading
    JSON via networkx's node_link_graph, whereas the session's current_graph
    retains the original UUID string edge keys.  We therefore match occurrences
    by (src, dst) node-ID pairs and resolve them to the actual edge keys that
    exist in the graph.

    Returns:
        (highlighted_nodes, highlighted_edges)
        highlighted_nodes : set of node IDs (strings)
        highlighted_edges : set of (src, dst, key) triples using the graph's own keys
    """
    highlighted_nodes: set = set()
    highlighted_edge_pairs: set = set()  # (src, dst) from CMiner mappings

    for occ in occurrences:
        for target_node in occ.get("nodes", {}).values():
            highlighted_nodes.add(target_node)
        for edge in occ.get("edges", []):
            highlighted_edge_pairs.add((edge["src"], edge["dst"]))

    # Resolve (src, dst) pairs → actual (src, dst, key) triples in the graph
    highlighted_edges: set = set()
    for src, dst in highlighted_edge_pairs:
        if graph.has_edge(src, dst):
            for key in graph[src][dst]:
                highlighted_edges.add((src, dst, key))

    return highlighted_nodes, highlighted_edges


def pattern_to_tve_gdata(pattern: PatternInfo, index: int = None) -> str:
    idx   = pattern.id if index is None else index
    lines = [f"t # {idx}"]
    for node_id, labels in pattern.nodes:
        lines.append(f"v {node_id} {' '.join(labels) if labels else 'unknown'}")
    for src, dst, label in pattern.edges:
        lines.append(f"e {src} {dst} {label}")
    return "\n".join(lines)