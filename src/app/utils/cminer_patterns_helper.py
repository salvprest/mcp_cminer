
import re
from dataclasses import dataclass


@dataclass
class PatternInfo:
    id: int
    nodes: list[tuple] # (node_id, label)
    edges: list[tuple] # (src, dst, label)
    support: float
    frequency: int


def parse_cminer_output(text: str) -> list[PatternInfo]:
    patterns: list[PatternInfo] = []
    blocks = re.split(r'-{5,}', text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        pat_id   = None
        nodes:  list[tuple] = []
        edges:  list[tuple] = []
        support   = 0.0
        frequency = 0
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r't\s+#\s+(\d+)', line)
            if m:
                pat_id = int(m.group(1))
                continue
            m = re.match(r'v\s+(\d+)(?:\s+(\S+))?', line)
            if m:
                nodes.append((int(m.group(1)), m.group(2) or ""))
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
        if pat_id is not None:
            patterns.append(PatternInfo(
                id=pat_id, nodes=nodes, edges=edges,
                support=support, frequency=frequency,
            ))
    return patterns


def pattern_to_tve_gdata(pattern: PatternInfo, index: int = None) -> str:
    idx   = pattern.id if index is None else index
    lines = [f"t # {idx}"]
    for node_id, label in pattern.nodes:
        lines.append(f"v {node_id} {label}")
    for src, dst, label in pattern.edges:
        lines.append(f"e {src} {dst} {label}")
    return "\n".join(lines)