from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

try:
    from jinja2 import Template
except ImportError:
    Template = None


TIN_VECTOR_NS = [0.0100, 0.0231, 0.0531, 0.1225, 0.2823, 0.6507, 1.5000]
CLOAD_VECTOR_PF = [0.0005, 0.0013, 0.0035, 0.0094, 0.0249, 0.0662, 0.1758]
MEASURE_KEYS = ["cell_rise", "cell_fall", "rise_transition", "fall_transition"]

DEFAULT_CELLS = [
    "invx1",
    "invx2",
    "invx4",
    "invx8",
    "nand2x1",
    "nand2x2",
    "nand2x4",
    "nor2x1",
    "nor2x2",
    "nor2x4",
    "maj3x1",
    "maj3x2",
    "maj3x4",
]

@dataclass
class RunConfig:
    root: Path
    template_path: Path
    raw_root: Path
    out_root: Path
    stdcell_lib_path: str
    sky130_model_lib: str
    deck_preamble: str
    ngspice_bin: str = "ngspice"
    dry_run: bool = False
    smoke_test: bool = False
    # cwd for ngspice: directory containing sky130.lib.spice (PDK .include paths are relative).
    ngspice_cwd: Optional[Path] = None

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ngspice NLDM characterization sweeps.")
    parser.add_argument(
        "--cells",
        nargs="+",
        default=DEFAULT_CELLS,
        help="Cell names to characterize.",
    )
    parser.add_argument(
        "--ngspice-bin",
        default="ngspice",
        help="Path to ngspice executable.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate decks but do not execute ngspice.",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help=(
            "Use bundled behavioral cell stubs and skip sky130.lib.spice. "
            "For validating automation only — not for submission timing."
        ),
    )
    return parser.parse_args()

def load_template(template_path: Path) -> str:
    return template_path.read_text(encoding="utf-8")

def render_template(template_text: str, context: Dict[str, str]) -> str:
    if Template is not None:
        return Template(template_text).render(**context)

    rendered = template_text
    for key, value in context.items():
        rendered = rendered.replace("{{ " + key + " }}", str(value))
    return rendered

def build_xdut_line(cell: str) -> str:
    if cell.startswith("inv"):
        return f"XDUT a y vdd vss {cell}"
    if cell.startswith("nand2") or cell.startswith("nor2"):
        return f"XDUT a b y vdd vss {cell}"
    if cell.startswith("maj3"):
        return f"XDUT a b c y vdd vss {cell}"
    return f"XDUT a y vdd vss {cell}"

def input_bias_for_cell(cell: str) -> Dict[str, str]:
    # noncontrolling defaults
    #  NAND2 hold B=1 so A controls output inversion path
    #  NOR2 hold B=0 so A controls output inversion path
    #  MAJ3 hold B=0, C=1 so output follows A
    if cell.startswith("nand2"):
        return {"b_level": "{VDD}", "c_level": "0"}
    if cell.startswith("nor2"):
        return {"b_level": "0", "c_level": "0"}
    if cell.startswith("maj3"):
        return {"b_level": "0", "c_level": "{VDD}"}
    return {"b_level": "0", "c_level": "0"}

def run_ngspice(
    ngspice_bin: str,
    deck_path: Path,
    log_path: Path,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess:
    cmd = [ngspice_bin, "-b", "-o", str(log_path), str(deck_path)]
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None and cwd.is_dir() else None,
    )

def parse_measures(log_text: str) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {k: None for k in MEASURE_KEYS}
    for key in MEASURE_KEYS:
        m = re.search(rf"\b{re.escape(key)}\s*=\s*([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)", log_text)
        if m:
            result[key] = float(m.group(1)) * 1e9  # seconds = ns
    return result

def ensure_dirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)

def characterize_cell(cell: str, cfg: RunConfig, template_text: str) -> Dict[str, object]:
    cell_raw = cfg.raw_root / cell
    ensure_dirs(cell_raw)

    tables = {k: [[None for _ in CLOAD_VECTOR_PF] for _ in TIN_VECTOR_NS] for k in MEASURE_KEYS}
    failures: List[Dict[str, object]] = []

    for i, tin_ns in enumerate(TIN_VECTOR_NS):
        for j, cload_pf in enumerate(CLOAD_VECTOR_PF):
            run_name = f"{cell}_tin{tin_ns:.4f}_cl{cload_pf:.4f}"
            deck_path = cell_raw / f"{run_name}.spice"
            log_path = cell_raw / f"{run_name}.log"

            context = {
                "cell_name": cell,
                "tin_ns": f"{tin_ns:.4f}",
                "cload_pf": f"{cload_pf:.4f}",
                "stdcell_lib_path": cfg.stdcell_lib_path,
                "sky130_model_lib": cfg.sky130_model_lib,
                "deck_preamble": cfg.deck_preamble,
                "xdut_line": build_xdut_line(cell),
                **input_bias_for_cell(cell),
            }
            deck_path.write_text(render_template(template_text, context), encoding="utf-8")

            if cfg.dry_run:
                continue

            proc = run_ngspice(cfg.ngspice_bin, deck_path, log_path, cwd=cfg.ngspice_cwd)
            if proc.returncode != 0:
                err_tail = proc.stderr.strip()
                if err_tail:
                    prev = (
                        log_path.read_text(encoding="utf-8", errors="ignore")
                        if log_path.is_file()
                        else ""
                    )
                    log_path.write_text(
                        prev + "\n\n***** ngspice stderr *****\n" + err_tail + "\n",
                        encoding="utf-8",
                    )
                failures.append(
                    {
                        "run": run_name,
                        "error": "ngspice_failed",
                        "stderr": err_tail,
                    }
                )
                continue

            log_text = log_path.read_text(encoding="utf-8", errors="ignore")
            meas = parse_measures(log_text)
            for key in MEASURE_KEYS:
                tables[key][i][j] = meas[key]
                if meas[key] is None:
                    failures.append(
                        {
                            "run": run_name,
                            "error": f"missing_measure:{key}",
                        }
                    )

    out: Dict[str, object] = {
        "cell": cell,
        "input_transition_ns": TIN_VECTOR_NS,
        "load_cap_pf": CLOAD_VECTOR_PF,
        "tables_ns": tables,
        "failures": failures,
    }
    if cfg.smoke_test:
        out["mode"] = "smoke_test"
    return out

def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    template_path = root / "spice" / "templates" / "char_testbench.spice.j2"
    raw_root = root / "results" / "raw"
    out_root = root / "results" / "nldm"

    smoke_fixtures = root / "spice" / "fixtures" / "smoke_stdcells.lib.spice"

    if args.smoke_test:
        stdcell_lib = str(smoke_fixtures.resolve())
        sky130_model = ""
        deck_preamble = (
            "* Automation smoke test: no SKY130 .lib (behavioral stubs only)\n"
            ".temp 25\n"
            "\n"
        )
        stdcell_path = Path(stdcell_lib)
        if not stdcell_path.is_file():
            raise SystemExit(f"Smoke fixture missing: {stdcell_path}")
    else:
        stdcell_lib = os.environ.get("STDCELL_LIB_PATH", str(root / "spice" / "stdcells.lib.spice"))
        sky130_model = os.environ.get("SKY130_MODEL_LIB", "")
        if not sky130_model:
            raise SystemExit(
                "Set SKY130_MODEL_LIB to your sky130.lib.spice absolute path.\n"
                "Or run with --smoke-test to validate automation without the PDK."
            )

        sky130_path = Path(sky130_model).expanduser().resolve()
        if not sky130_path.is_file():
            raise SystemExit(
                "SKY130_MODEL_LIB must point to an existing file.\n"
                f"  Got: {sky130_model}\n"
                f"  Resolved: {sky130_path}\n"
                "Do not use README placeholders like /absolute/path/ or /real/path/ — use the real path "
                "from your SKY130 / Open PDK install or course VM (e.g. find sky130.lib.spice with Finder "
                "or: find ~ -name sky130.lib.spice 2>/dev/null).\n"
                "Or run with --smoke-test to validate automation without the PDK."
            )

        stdcell_path = Path(stdcell_lib).expanduser().resolve()
        if not stdcell_path.is_file():
            raise SystemExit(
                "STDCELL_LIB_PATH must point to an existing file.\n"
                f"  Got: {stdcell_lib}\n"
                f"  Resolved: {stdcell_path}"
            )

        sky130_model = str(sky130_path)
        stdcell_lib = str(stdcell_path)
        # Only .lib ... tt — sky130.lib.spice already .include's corners/tt.spice inside the tt block.
        # A second .include of tt.spice duplicates models and breaks parameter expansion (l=$, w=$).
        deck_preamble = f'.lib "{sky130_model}" tt\n.temp 25\n\n'

    if not shutil.which(args.ngspice_bin):
        raise SystemExit(f"ngspice executable not found: {args.ngspice_bin}")

    # ngspice cwd: the directory that contains sky130.lib.spice (e.g. .../libs.tech/ngspice).
    # SkyWater's .lib uses relative .include "corners/tt.spice"; those paths resolve against
    # this directory. Using only the repo's spice/ngspice_char_cwd breaks model linking for
    # some devices (can't find sky130_fd_pr__*__model). We still copy our .spiceinit there so
    # batch mode loads ngbehavior/skywaterpdk/ng_nomodcheck.
    ngspice_char_cwd = root / "spice" / "ngspice_char_cwd"
    spiceinit_src = ngspice_char_cwd / ".spiceinit"
    ngspice_cwd: Optional[Path] = None
    if not args.smoke_test and sky130_model:
        ensure_dirs(ngspice_char_cwd)
        if not spiceinit_src.is_file():
            raise SystemExit(
                f"Missing {spiceinit_src} — restore it from the repository."
            )
        pdk_ngspice_dir = Path(sky130_model).expanduser().resolve().parent
        use_repo_only = os.environ.get("NGSPICE_CWD_REPO", "").strip() in ("1", "true", "yes")
        if use_repo_only:
            ngspice_cwd = ngspice_char_cwd.resolve()
        else:
            try:
                shutil.copy2(spiceinit_src, pdk_ngspice_dir / ".spiceinit")
            except OSError as exc:
                raise SystemExit(
                    f"Could not copy {spiceinit_src} to {pdk_ngspice_dir}/.spiceinit ({exc}).\n"
                    "Fix permissions on the PDK directory, or set NGSPICE_CWD_REPO=1 (may break simulations)."
                ) from exc
            ngspice_cwd = pdk_ngspice_dir

    cfg = RunConfig(
        root=root,
        template_path=template_path,
        raw_root=raw_root,
        out_root=out_root,
        stdcell_lib_path=stdcell_lib,
        sky130_model_lib=sky130_model,
        deck_preamble=deck_preamble,
        ngspice_bin=args.ngspice_bin,
        dry_run=args.dry_run,
        smoke_test=args.smoke_test,
        ngspice_cwd=ngspice_cwd,
    )

    ensure_dirs(cfg.raw_root, cfg.out_root, root / "results" / "plots")
    template_text = load_template(cfg.template_path)

    for cell in args.cells:
        data = characterize_cell(cell, cfg, template_text)
        out_file = cfg.out_root / f"{cell}.json"
        out_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[done] {cell} -> {out_file}")
        if data["failures"]:
            print(f"[warn] {cell}: {len(data['failures'])} issues detected")

    if args.dry_run:
        print("Dry run completed. Decks generated without ngspice execution.")
    elif args.smoke_test:
        print("Smoke test used behavioral fixtures — replace with SKY130 + real cells for submission.")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
