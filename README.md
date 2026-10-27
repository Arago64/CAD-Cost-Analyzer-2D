# CAD Cost Analyzer 2D — v3.3

A Python/Streamlit application for **2D sheet nesting and material-use analysis**. Import or draw parts, create candidate layouts, compare material usage, estimate processing costs, inspect reusable offcuts, and export DXF geometry.

> **Status:** Engineering portfolio / prototype, not a production-certified CAD/CAM application. Exported DXFs are geometric layouts, **not machine-ready toolpaths or G-code**. Verify clearances, kerf, clamping, offcut separability and CAM output before fabrication.

## Features

- Import DXF part outlines and enter quantities; edit parts in the built-in 2D layout editor.
- Arrange up to **50 individual part instances** (including duplicates).
- Hybrid geometric nesting: NFP-based placement, local-neighborhood search, order changes, exact-contact translations and group moves.
- Validate candidate placements against polygon geometry, holes and requested gaps with Shapely/GEOS.
- Prioritize smaller stock-sheet area or evaluate usable leftover material; configure sheet sizes, margins and kerf.
- Reuse a **validated layout imported from an earlier result ZIP**, optionally with manual-editor JSON edits, as an optimization starting point.
- Estimate costs and generate layout previews, result packages and **DXF R2010** output.
- Interface available in **English, German and Russian**.

**Limitations:** This is a bounded heuristic optimizer: it cannot guarantee a globally optimal layout. Performance varies with shape geometry, part order, algorithm settings and time budget. The deeper Engine 2 search is restricted to smaller instances. Circular and curved geometry may be approximated by closed polylines in exported DXFs.

## Quick start

Requires an installed **Python 3.11+** interpreter and internet access on the first installation of dependencies.

**Windows**

```bat
START_WINDOWS.bat
```

**Linux / macOS**

```sh
sh START_LINUX_MAC.sh
```

Or install and run manually:

```sh
python -m venv .venv
# Activate the virtual environment for your platform.
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

The launch scripts use a local virtual environment, install requirements and start a Streamlit server. **Do not commit or distribute `.venv/`**; each user should create their own environment. Dependencies are specified as supported version *ranges* in `requirements.txt`, not as an exact reproducible lockfile.

### Build an optional Windows executable

Run `BUILD_WINDOWS_EXE.bat` on Windows (requires PyInstaller dependencies and a build environment), or use the manual GitHub Actions workflow in `.github/workflows/build-windows.yml`. A compiled EXE is **not included**. The Windows build process has not been validated by this README review.

## Worked 16-part benchmark

A documented v3.3 benchmark uses a **previously hand-improved layout as the starting seed**. The reported sheet area improved from **6,859,892 mm²** to **6,749,808 mm²**, a reduction of **110,084 mm² (1.60%)**. The final 4,476 × 1,508 mm rectangular sheet, 16 outlines and 3 mm gaps were geometry-checked, and the DXF was parsed back with ezdxf.

Artifacts are under `examples/BENCHMARK_16_v33_optimized.dxf`, the companion SVG, and `BENCHMARK_16_comparison.json`.

**Do not interpret this as a verified 1.60% autonomous improvement from a random initial layout**; the previous manual result was supplied as a seed. The numbers describe a particular input and are not a general performance guarantee.

## Tests

With `pytest` installed:

```sh
python -m pytest -q tests/test_v33_pressure.py tests/test_v32_hybrid_50.py tests/test_dxf_export_i18n.py
```

Additional test modules cover geometry validation, engine versions, remnant handling, orchestration, editor behavior and export. Automated tests do not replace checking a real CAM workflow.

## Project structure

- `app.py` — Streamlit UI and workflow
- `nesting_*.py`, `optimizer_v15.py`, `bounded_search.py` — arrangement algorithms
- `cad_geometry.py`, `dxf_reader.py`, `dxf_export.py` — geometry and DXF input/output
- `layout_editor.py`, `reference_import.py` — editing and seeded-layout imports
- `material_priority.py`, `remnant_*.py`, `process_cost.py` — material and cost analysis
- `tests/` — automated tests; `examples/` — sample DXFs and benchmark artifacts
- `VERSION_INFO.txt` — v3.3 release notes

## Other documentation and licenses

The original Russian v3.3 instructions remain in [`README_RU.md`](README_RU.md). `README_v3_3_RU.md` is a duplicate of those instructions; `README_MATERIAL_FIRST_RU.md` describes an **older v2.5 change** and is kept only for historical reference.

Third-party dependencies have their own licenses; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). **No license for the project's original source code is currently supplied.** Add one before inviting third-party reuse.
