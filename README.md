# Standard-Cell-Library-Design-and-Characterization

SKY130 standard cell library design and **ngspice** batch characterization (course project).

This repository contains the cell library netlists and an automation scaffold for NLDM characterization.

## What this gives you

- A batch characterization flow based on `ngspice` + Python.
- 7x7 sweeps for input transition and output load.
- Parsing for required timing metrics:
  - `cell_rise`
  - `cell_fall`
  - `rise_transition`
  - `fall_transition`
- JSON output per cell ready for reporting.

## Project layout

- `spice/stdcells.lib.spice` - your 13 `.subckt` definitions live here.
- `spice/templates/char_testbench.spice.j2` - parametrized ngspice deck template.
- `scripts/run_characterization.py` - main sweep driver.
- `results/raw/` - ngspice netlists and logs.
- `results/nldm/` - NLDM JSON tables per cell.
- `results/plots/` - plotting output location.

## Requirements

- Python 3.9+
- `ngspice` available in `PATH`
- Optional for template rendering:
  - `jinja2` (recommended)

If `jinja2` is not installed, the script can still run using a built-in fallback renderer.

## Quick start

1. Put your SKY130 model and library path in environment variables. Use the **real** path to `sky130.lib.spice` on your machine (from Open PDK, course files, or your VM)—not a placeholder string:

```bash
# Example only: replace with your actual path, e.g. from `find ~ -name sky130.lib.spice`
export SKY130_MODEL_LIB="/Users/you/path/to/sky130.lib.spice"
export STDCELL_LIB_PATH="$(pwd)/spice/stdcells.lib.spice"
```

The script checks that both files exist before running simulations.

### Test automation only (no PDK / no custom cells yet)

Use `--smoke-test` to run the full sweep against bundled **behavioral** stubs in
[`spice/fixtures/smoke_stdcells.lib.spice`](spice/fixtures/smoke_stdcells.lib.spice). You do **not** need
`SKY130_MODEL_LIB` or a filled `stdcells.lib.spice`.

```bash
python3 scripts/run_characterization.py --smoke-test --cells invx1
```

Output JSON includes `"mode": "smoke_test"`. Timing numbers are **not** valid for the course report—this
only validates decks, ngspice, parsing, and JSON output.

1. Fill `spice/stdcells.lib.spice` with your final 13 cell subcircuits.
2. Dry run first:

```bash
python3 scripts/run_characterization.py --dry-run --cells invx1
```

1. Real run (example):

```bash
python3 scripts/run_characterization.py --cells invx1 invx2 invx4 invx8
```

1. Full library run:

```bash
python3 scripts/run_characterization.py
```

## Notes on pin order and arcs

This automation assumes each cell has pins `(A Y VDD VSS)` by default.

For multi-input cells (NAND2/NOR2/MAJ3), set `pin_order` and `active_input` mappings in
`scripts/run_characterization.py` to match final `.subckt` definitions.

## Output format

Each file in `results/nldm/<cell>.json` contains:

- `input_transition_ns` vector
- `load_cap_pf` vector
- 4 matrices (`7x7`) for NLDM values in ns
- run metadata
