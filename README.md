# Standard-Cell-Library-Design-and-Characterization

SKY130 standard cell library design and **ngspice** batch characterization (Digital Design II–style course project).

The repo holds **13 transistor-level subcircuits**, a **parametrized testbench**, a **Python sweep driver**, **NLDM JSON** (and an optional **Excel** workbook), and **report figures** (inverter delay vs load, RC vs SPICE at the table midpoint).

## Contents

- **7×7** sweeps over input transition (ns) and output load (pF).
- Extracted metrics (ns in JSON): `cell_rise`, `cell_fall`, `rise_transition`, `fall_transition`.
- One JSON file per cell under `results/nldm/`.

## Repository layout

| Path | Purpose |
|------|---------|
| `spice/stdcells.lib.spice` | All `.subckt` definitions (13 cells). |
| `spice/templates/char_testbench.spice.j2` | Jinja2 ngspice deck template. |
| `spice/ngspice_char_cwd/.spiceinit` | SkyWater/ngspice compatibility (`ngbehavior`, etc.). The driver runs `ngspice` with the PDK directory as cwd and copies this file there when needed. |
| `scripts/run_characterization.py` | Main characterization driver. |
| `scripts/plot_inverter_delay.py` | Inverter family **delay vs load** (SVG without extra deps; PNG if `matplotlib` is installed). |
| `scripts/plot_rc_vs_spice_midpoint.py` | **RC vs SPICE** grouped bars at NLDM midpoint (default: invx1, tin ≈ 0.1225 ns, C_load ≈ 0.0094 pF). |
| `scripts/generate_nldm_excel.py` | Builds `results/nldm/nldm_tables.xlsx` from all `results/nldm/*.json` (requires `openpyxl`). |
| `results/nldm/*.json` | NLDM tables per cell. |
| `results/nldm/nldm_tables.xlsx` | Excel report (optional; regenerate with script above). |
| `results/plots/` | Default output for figure scripts (git may or may not track—regenerate anytime). |
| `results/raw/` | Per-run netlists and logs (**gitignored**). Safe to delete; recreated on the next characterization run. |

## Requirements

- **Python 3.9+** (3.10+ recommended).
- **`ngspice`** on `PATH` for real PDK runs.
- **Python packages** (see `requirements.txt`):
  - `jinja2` — template rendering (built-in fallback exists but Jinja2 is recommended).
  - `openpyxl` — Excel generation.
  - `matplotlib` — optional; only needed for **PNG** plots. **SVG** figures use the standard library only.

Install:

```bash
pip install -r requirements.txt
```

## Environment (real SKY130 run)

Point to your actual `sky130.lib.spice` and the compiled standard cell library:

```bash
export SKY130_MODEL_LIB="/path/to/sky130.lib.spice"
export STDCELL_LIB_PATH="$(pwd)/spice/stdcells.lib.spice"
```

Both files must exist before simulations start.

## Quick start: characterization

```bash
python3 scripts/run_characterization.py --dry-run --cells invx1   # write decks only (still needs real SKY130_MODEL_LIB)
python3 scripts/run_characterization.py                              # full 13 cells × 49 points
```

`--quick` uses a 2×2 grid (faster sanity check; **not** the full 7×7 NLDM for submission). See `--help` for `--cells` and `--jobs`.

## Quick start: figures and Excel

From the repository root, after `results/nldm/*.json` exist:

```bash
# Inverter delay vs load (SVG — no matplotlib)
python3 scripts/plot_inverter_delay.py -d results/nldm -o results/plots/inverter_delay.svg --format svg

# RC model vs SPICE at NLDM midpoint (prints a ps table on stdout)
python3 scripts/plot_rc_vs_spice_midpoint.py -d results/nldm -o results/plots/rc_vs_spice_midpoint.svg --format svg

# Excel workbook for the report appendix
python3 scripts/generate_nldm_excel.py
```

## Troubleshooting simulations

### Do not double-include the TT corner

Use **only**:

```spice
.lib "/path/to/sky130.lib.spice" tt
.temp 25
```

`sky130.lib.spice` already includes the `tt` corner. A second `.include` of `tt.spice` can cause redefinition / bogus instances.

### `could not find a valid modelname` (e.g. `sky130_fd_pr__pfet_01v8__model`)

- Keep `spice/ngspice_char_cwd/.spiceinit` in the repo; ensure the driver’s **cwd strategy** matches your PDK layout.
- Some **macOS** ngspice builds still fail on SkyWater model cards. Prefer the **course Linux VM**, **conda-forge linux-64**, or an ngspice version your instructor recommends.
- To force cwd to stay in the repo (usually wrong for volare installs):

```bash
export NGSPICE_CWD_REPO=1
```

## Multi-input cells

NAND2, NOR2, and MAJ3 use the DUT instance line and **B/C bias** defined in `scripts/run_characterization.py` (`build_xdut_line`, `input_bias_for_cell`) so input **A** is the switching pin. These must match your `.subckt` port order in `spice/stdcells.lib.spice`.

## JSON output shape

Each `results/nldm/<cell>.json` contains:

- `input_transition_ns`, `load_cap_pf`
- `tables_ns`: four **7×7** matrices (values in **ns**)
- Optional `failures` / metadata for debugging failed points

## Cleaning generated artifacts

- **`results/raw/`** — netlists and logs; **gitignored**. Delete anytime; rerun characterization to refill.
- **`results/plots/`** — regenerate with the plot scripts. Exported **PNG** scratch files (e.g. `*-01.png`) are ignored by git—prefer **SVG** for the report or attach figures only in the PDF.

## Authors

Course project by **Mohamed El-Refai** and **Haya Shalaby** (American University in Cairo). See the submitted report for division of labor, AI-tool disclosure, and methodology details.
