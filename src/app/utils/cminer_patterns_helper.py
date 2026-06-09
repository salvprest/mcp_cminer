
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

    CMiner's internal DBGraph reinitialises the graph from the JSON export
    and in doing so re-indexes all edge keys as integers 0, 1, 2, ... (in JSON
    order), even though the session's current_graph retains the original UUID
    string edge keys.  The two graphs are loaded from the *same* JSON payload
    in the *same* order, so the ordering of edges between any (src, dst) pair
    is identical in both.  We exploit this to map

        CMiner integer key n  ->  list(graph[src][dst].keys())[n]

    i.e. the n-th UUID key in insertion order.  This ensures that when a
    pattern occurrence references one specific parallel edge (key=1 of three),
    only that one edge is highlighted rather than all three.

    Falls back to highlighting all edges between (src, dst) when:
      - the key is absent or None (legacy / fallback data), or
      - the key is out of range (e.g. graph was filtered after mining).

    Returns:
        (highlighted_nodes, highlighted_edges)
        highlighted_nodes : set of node IDs (strings)
        highlighted_edges : set of (src, dst, key) triples using the graph's own keys
    """
    highlighted_nodes: set = set()
    highlighted_edges: set = set()

    for occ in occurrences:
        for target_node in occ.get("nodes", {}).values():
            highlighted_nodes.add(target_node)
        for edge in occ.get("edges", []):
            src = edge["src"]
            dst = edge["dst"]
            raw_key = edge.get("key")
            if not graph.has_edge(src, dst):
                continue
            edge_keys = list(graph[src][dst].keys())
            if isinstance(raw_key, int) and 0 <= raw_key < len(edge_keys):
                # CMiner integer key is a 0-based position index.
                highlighted_edges.add((src, dst, edge_keys[raw_key]))
            elif raw_key is not None and raw_key in graph[src][dst]:
                # Key is already a valid UUID/string key (defensive path).
                highlighted_edges.add((src, dst, raw_key))
            else:
                # No usable key, fall back to all parallel edges.
                for k in edge_keys:
                    highlighted_edges.add((src, dst, k))

    return highlighted_nodes, highlighted_edges


def pattern_to_tve_gdata(pattern: PatternInfo, index: int = None) -> str:
    idx   = pattern.id if index is None else index
    lines = [f"t # {idx}"]
    for node_id, labels in pattern.nodes:
        lines.append(f"v {node_id} {' '.join(labels) if labels else 'unknown'}")
    for src, dst, label in pattern.edges:
        lines.append(f"e {src} {dst} {label}")
    return "\n".join(lines)