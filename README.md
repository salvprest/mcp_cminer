# MCP CMiner

An [MCP](https://modelcontextprotocol.io/) server for **graph pattern mining on conceptual models**.  
It exposes a set of tools that let an LLM agent upload ontology/architecture models, convert them to graphs, configure the miner, and retrieve recurring structural patterns — all through natural-language interactions.

---

## What it does

The server wraps two libraries:

| Library | Role |
|---|---|
| **cml2graph** | Parses conceptual model files (OntoUML, Archimate, …) and converts them into labeled graphs, with support for filtering and summarising the graph structure. |
| **CMiner** | Runs frequent subgraph mining (TVE-gdata-based) on the converted graphs to find recurring patterns across a model database. |

The result is an interactive agent workflow: the LLM can explore a model, focus on relevant parts, launch the miner, and inspect or visualise the discovered patterns.

---

## How to run

**Prerequisites:** Python ≥ 3.12 and [uv](https://github.com/astral-sh/uv).

```bash
# 1. Clone and enter the project
git clone <this-repo>
cd mcp_cminer

# 2. Install dependencies
uv sync

# 3. Start the server
uv run main
```

The server starts a FastAPI/FastMCP application (default port **8000**).  
A REST endpoint `POST /upload` is also exposed for uploading model files before the MCP session begins.

---

## Pipeline

```
Upload model file  ──►  Load into session  ──►  (optional) Filter graph
     /upload              cml2graph_load_model     cml2graph_filter

         ──►  Mine patterns  ──►  Inspect / Visualise
               cminer_run          cminer_list_patterns
                                   cminer_generate_visualization
```

1. **Upload** – send a JSON model file (OntoUML, Archimate, …) via `POST /upload`. The server auto-detects the format and converts it to a node-link graph.
2. **Load** – call `cml2graph_load_model` to bring the converted graph into the active session.
3. **Explore & filter** – use `cml2graph_explain_model` and `cml2graph_list_filters` to understand the graph, then `cml2graph_filter` to narrow down the scope.
4. **Mine** – call `cminer_run` to execute frequent subgraph mining. Parameters (support threshold, node count bounds, database file) can be tuned with the `cminer_set_parameter_*` tools.
5. **Inspect** – retrieve results with `cminer_list_patterns` / `cminer_get_pattern_info`, or render an interactive HTML visualisation via `cminer_generate_visualization`.
