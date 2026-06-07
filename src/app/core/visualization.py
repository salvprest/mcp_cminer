"""
Visualization HTML assembly + Focus Protocol specification.

Architecture
────────────
The HTML contains NO embedded mining data. The viewer is a thin client
that fetches everything it needs from same-origin API endpoints:

  GET /api/sessions/{sid}/meta      -> {num_patterns, num_models, focus}
  GET /api/sessions/{sid}/patterns  -> paginated pattern metadata
  GET /api/sessions/{sid}/models    -> list of {name, encoded}
  GET /api/sessions/{sid}/patterns/{pid}/models  -> matched models for a pattern
  GET /plantuml/svg/{encoded}       -> SVG image

This module just stitches three sibling asset files together:

  viz_assets/viewer.html   <- markup skeleton + placeholders
  viz_assets/viewer.css    <- styles
  viz_assets/viewer.js     <- client logic

================================================================================
                            FOCUS PROTOCOL
================================================================================

The "focus" is a piece of per-session state that tells the visualization
where to position itself the next time it is opened. Tools mutate the
focus as a side-effect of their work; the viewer reads it ONCE at boot
and then leaves the user in control.

There is NO polling, NO live sync, NO push channel. This is intentional:
the user should not have the UI yanked around while they explore. If
they want to see the latest tool's result, they reopen / reload the
visualization.

Schema (stored at session["focus"])
───────────────────────────────────
    {
      "tab":         "patterns" | "models" | None,
      "pattern_id":  int | None,    # selected in the patterns list
      "model_name":  str | None,    # selected in the models list
      "highlight":   None | {
          "pattern_id":       int,
          "model_name":       str,
          "occurrence_index": int | None,  # None = all occurrences
      },
    }

Field semantics
───────────────
- `tab`         which tab is shown on open. If None, the viewer picks
                a sensible default (models if any, else patterns).
- `pattern_id`  preselected pattern. Persists even when the user is on
                the models tab, so switching tabs reveals it already
                selected.
- `model_name`  preselected model. Same persistence rule.
- `highlight`   when set, the viewer enters the matched-models flow
                for `pattern_id` and opens `model_name` with the
                highlighted PlantUML. Mirrors a two-step user gesture
                (click pattern, click it again) but invoked declaratively.

Defaults when fields are missing
────────────────────────────────
- tab=models  + no model_name -> first model in the list is selected
- tab=patterns + no pattern_id -> first pattern on page 1 is selected
- no focus at all -> models tab if any models, else patterns tab

How to wire a new tool
──────────────────────
At the end of your tool, call `set_focus` from app.core.state:

    from app.core.state import set_focus
    set_focus(session, tab="models", model_name=name)

Only pass the fields you want to change; others are left untouched.
Pass `None` to explicitly clear a field.

How to add a new focus dimension
────────────────────────────────
1. Extend the schema above and `_empty_focus()` in app/core/state.py.
2. Handle the new field in `applyFocus()` in viz_assets/viewer.js.
3. Have your tool call `set_focus(session, <new_field>=...)`.

================================================================================
"""

import os
from pathlib import Path

_DEFAULT_BASE_URL = os.environ.get("CMINER_BASE_URL", "http://localhost:8222").rstrip("/")

_ASSETS_DIR = Path(__file__).resolve().parent / "viz_assets"


def _read(name: str) -> str:
    return (_ASSETS_DIR / name).read_text(encoding="utf-8")


def generate_html_visualization(
    session_id: str,
    base_url: str = _DEFAULT_BASE_URL,
    version: int = 0,
) -> str:
    """Assemble the viewer HTML by injecting CSS + JS into the template."""
    html = _read("viewer.html")
    css = _read("viewer.css")
    js = _read("viewer.js")
    # Inject assets first, then substitute the per-session placeholders.
    # Order matters: CSS/JS must not contain the per-session placeholders.
    return (
        html
        .replace("__CSS__", css)
        .replace("__JS__", js)
        .replace("__SESSION_ID__", session_id)
        .replace("__BASE_URL__", base_url)
        .replace("__VERSION__", str(version))
    )

