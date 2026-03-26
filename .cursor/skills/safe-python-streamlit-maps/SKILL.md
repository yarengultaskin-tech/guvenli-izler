---
name: safe-python-streamlit-maps
description: Writes Python 3.10+ modular geospatial apps with Streamlit UI, Folium maps, and streamlit-folium integration. Use when building map-based safety apps, Streamlit dashboards, or location marker visualizations with green/yellow/red status colors.
---

# Safe Python Streamlit Maps

## Quick Start

Use this skill when the user asks for a Python web app with map visualization.

1. Write code compatible with Python 3.10+.
2. Use Streamlit as the only web UI framework.
3. Use Folium + streamlit-folium for map rendering in Streamlit.
4. Keep code modular: place helper utilities in separate files.
5. Use meaningful English variable names (for example: `safety_score`, `user_comment`).
6. Add `try-except` blocks for I/O, parsing, API calls, and map rendering.
7. Use marker colors from only this palette: green, yellow, red.

## Required Conventions

### Tech stack constraints

- UI framework: Streamlit only.
- Map library: Folium.
- Streamlit map bridge: streamlit-folium.
- Python version target: 3.10+ syntax and standard library usage.

### Project structure

Prefer this layout:

```text
project/
  app.py
  map_view.py
  utils/
    data_loader.py
    scoring.py
```

- `app.py`: Streamlit page layout and user interactions.
- `map_view.py`: Folium map creation and marker rendering.
- `utils/`: helper functions (validation, parsing, scoring, transforms).

### Naming and readability

- Variable and function names must be meaningful and English.
- Avoid short cryptic names except loop indices in trivial loops.
- Keep functions focused; split large functions into helper modules.

### Error handling rules

Always include explicit error handling where failures are likely:

- File reads/writes
- Network/API requests
- User input parsing
- Coordinate conversion
- Map creation and rendering

Pattern:

```python
try:
    # risky operation
    ...
except SpecificError as exc:
    st.error(f"Operation failed: {exc}")
```

## Map Marker Palette

Use only these marker colors to represent safety levels:

- `green`: safe / low risk
- `yellow`: caution / medium risk
- `red`: danger / high risk

Do not introduce additional status colors unless the user explicitly overrides this rule.

## Implementation Checklist

Before finishing, verify:

- [ ] Code is Python 3.10+ compatible.
- [ ] UI is Streamlit-only.
- [ ] Maps use Folium and are displayed with streamlit-folium.
- [ ] Helper functions are separated into modules/files.
- [ ] Variable names are meaningful English identifiers.
- [ ] Risky code paths are wrapped with `try-except`.
- [ ] Marker statuses use only green/yellow/red.
