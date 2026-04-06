from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    tin_vector_ns: List[float]
    load_cap_pf: List[float]
    ngspice_bin: str = "ngspice"
    dry_run: bool = False
    smoke_test: bool = False
    jobs: int = 1
    # cwd for ngspice: directory containing sky130.lib.spice (PDK .include paths are relative).
    ngspice_cwd: Optional[Path] = None


def _extract_error_lines(blob: str, max_lines: int = 40) -> str:
    hits: List[str] = []
    for ln in blob.splitlines():
        if re.search(r"(?i)\berror\b|\bfatal\b|could not find|undefined|syntax error", ln):
            hits.append(ln.rstrip())
            if len(hits) >= max_lines:
                break
    return "\n".join(hits) if hits else ""


def _diag_preview(log_text: str, stdout: str, stderr: str) -> str:
    """SKY130 logs can be huge; errors are often at the top, .measure lines in the middle."""
    chunks: List[str] = []
    lt = log_text or ""
    if lt.strip():
        err_lines = _extract_error_lines(lt)
        if err_lines:
            chunks.append(f"--- log: error/fatal lines (first matches) ---\n{err_lines}")
        n = len(lt)
        head_n, tail_n = 8000, 12000
        if n <= head_n + tail_n:
            chunks.append(f"--- log (-o file) full ({n} chars) ---\n{lt}")
        else:
            chunks.append(f"--- log head ({n} chars total) ---\n{lt[:head_n]}")
            chunks.append(f"--- log tail ---\n{lt[-tail_n:]}")
    for label, blob in (("stdout", stdout), ("stderr", stderr)):
        s = (blob or "").strip()
        if not s:
            continue
        tail = s[-2500:] if len(s) > 2500 else s
        chunks.append(f"--- {label} (tail) ---\n{tail}")
    return "\n".join(chunks) if chunks else "(no log/stdout/stderr captured)"


def _parallel_sim_worker(payload: Tuple[object, ...]) -> Dict[str, object]:
    """Picklable worker: run one ngspice job. Must stay at module top level."""
    (
        i,
        j,
        run_name,
        ngspice_bin,
        cwd_str,
        deck_path_s,
        log_path_s,
        deck_content,
    ) = payload
    deck_path = Path(deck_path_s)
    log_path = Path(log_path_s)
    deck_path.write_text(deck_content, encoding="utf-8")
    cmd = [ngspice_bin, "-b", "-o", str(log_path), str(deck_path)]
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd_str if cwd_str else None,
    )
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.is_file() else ""
    out = proc.stdout or ""
    err = proc.stderr or ""
    # Batch ngspice often prints .measure results in the -o log, but some builds echo them on stdout/stderr.
    meas_blob = "\n".join((log_text, out, err))
    meas = parse_measures(meas_blob)
    raw_rc = proc.returncode
    if raw_rc != 0:
        extra = ""
        if out.strip():
            extra += "\n\n***** ngspice captured stdout *****\n" + out
        if err.strip():
            extra += "\n\n***** ngspice captured stderr *****\n" + err
        if extra:
            log_path.write_text(log_text + extra, encoding="utf-8")
            log_text = log_path.read_text(encoding="utf-8", errors="ignore")
    # ngspice may exit non-zero on benign warnings while still printing valid measurements.
    rc = raw_rc
    if rc != 0 and _measures_all_valid(meas):
        rc = 0
    return {
        "i": i,
        "j": j,
        "run_name": run_name,
        "returncode": rc,
        "ngspice_exit_code": raw_rc,
        "stderr": err.strip(),
        "diag_preview": _diag_preview(log_text, out, err),
        "meas": meas,
    }

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
    parser.add_argument(
        "--quick",
        action="store_true",
        help=(
            "Only 2×2 corner grid (min/max tin × min/max load) = 4 sims per cell. "
            "Faster for debugging; not the full 7×7 NLDM required for submission."
        ),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Run up to N ngspice processes in parallel (default 1). "
            "Try 2–4 on multi-core machines; each job loads the full PDK (~RAM heavy)."
        ),
    )
    return parser.parse_args()

def load_template(template_path: Path) -> str:
    return template_path.read_text(encoding="utf-8")

def render_template(template_text: str, context: Dict[str, object]) -> str:
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

def parse_measures(log_text: str) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {k: None for k in MEASURE_KEYS}
    # Allow '=' or ':'; ngspice-36+ may use spaces or different case in batch logs.
    key_alt = "|".join(re.escape(k) for k in MEASURE_KEYS)
    pat = re.compile(
        rf"\b(?P<name>{key_alt})\s*[:=]\s*"
        r"(?P<val>[+-]?(?:\d+\.?\d*|\d*\.?\d+)(?:[eE][+-]?\d+)?|nan|inf)",
        re.IGNORECASE,
    )
    for m in pat.finditer(log_text):
        name = m.group("name").lower()
        if name not in result:
            continue
        vraw = m.group("val")
        if vraw.lower() == "nan":
            result[name] = float("nan")
        elif vraw.lower() == "inf":
            result[name] = float("inf")
        else:
            result[name] = float(vraw) * 1e9  # seconds -> ns
    return result


def _measures_all_valid(m: Dict[str, Optional[float]]) -> bool:
    for k in MEASURE_KEYS:
        v = m.get(k)
        if v is None:
            return False
        if isinstance(v, float) and (v != v):  # NaN
            return False
    return True


def ensure_dirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)

def characterize_cell(cell: str, cfg: RunConfig, template_text: str) -> Dict[str, object]:
    cell_raw = cfg.raw_root / cell
    ensure_dirs(cell_raw)

    tins = cfg.tin_vector_ns
    cloads = cfg.load_cap_pf
    tables = {k: [[None for _ in cloads] for _ in tins] for k in MEASURE_KEYS}
    failures: List[Dict[str, object]] = []

    jobs_list: List[Tuple[object, ...]] = []
    for i, tin_ns in enumerate(tins):
        for j, cload_pf in enumerate(cloads):
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
            deck_content = render_template(template_text, context)

            if cfg.dry_run:
                deck_path.write_text(deck_content, encoding="utf-8")
                continue

            cwd_str = str(cfg.ngspice_cwd) if cfg.ngspice_cwd is not None and cfg.ngspice_cwd.is_dir() else ""
            jobs_list.append(
                (
                    i,
                    j,
                    run_name,
                    cfg.ngspice_bin,
                    cwd_str,
                    str(deck_path.resolve()),
                    str(log_path.resolve()),
                    deck_content,
                )
            )

    if not cfg.dry_run and jobs_list:
        total = len(jobs_list)
        print(f"[run] {cell}: {total} simulation(s), jobs={cfg.jobs}", flush=True)
        done = 0
        if cfg.jobs <= 1:
            for payload in jobs_list:
                result = _parallel_sim_worker(payload)
                _apply_sim_result(result, tables, failures)
                done += 1
                print(f"  [{done}/{total}] {result['run_name']}", flush=True)
        else:
            with ProcessPoolExecutor(max_workers=cfg.jobs) as pool:
                futures = [pool.submit(_parallel_sim_worker, p) for p in jobs_list]
                for fut in as_completed(futures):
                    result = fut.result()
                    _apply_sim_result(result, tables, failures)
                    done += 1
                    print(f"  [{done}/{total}] {result['run_name']}", flush=True)

    out: Dict[str, object] = {
        "cell": cell,
        "input_transition_ns": tins,
        "load_cap_pf": cloads,
        "tables_ns": tables,
        "failures": failures,
    }
    if cfg.smoke_test:
        out["mode"] = "smoke_test"
    if len(tins) != len(TIN_VECTOR_NS) or len(cloads) != len(CLOAD_VECTOR_PF):
        out["grid_note"] = "non_standard_grid"
    return out


def _apply_sim_result(
    result: Dict[str, object],
    tables: Dict[str, List[List[Optional[float]]]],
    failures: List[Dict[str, object]],
) -> None:
    i = int(result["i"])
    j = int(result["j"])
    run_name = str(result["run_name"])
    rc = int(result["returncode"])
    meas = result["meas"]
    assert isinstance(meas, dict)

    if rc != 0:
        failures.append(
            {
                "run": run_name,
                "error": "ngspice_failed",
                "ngspice_exit_code": result.get("ngspice_exit_code", rc),
                "stderr": result.get("stderr", ""),
                "diag_preview": result.get("diag_preview", ""),
            }
        )
        return

    for key in MEASURE_KEYS:
        v = meas.get(key)
        tables[key][i][j] = v
        if v is None:
            failures.append(
                {
                    "run": run_name,
                    "error": f"missing_measure:{key}",
                }
            )

def main() -> int:
    args = parse_args()
    if args.jobs < 1:
        raise SystemExit("--jobs must be >= 1")

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
        # Do not .include the PDK "spinit" file into the netlist: it contains `set ngbehavior=...`
        # lines that belong in .spiceinit / control context only; as included SPICE they parse as
        # circuit line 3 and fail with "Unable to find definition of model" on `set ngbehavior=hsa`.
        # Only .lib ... tt — sky130.lib.spice already .include's corners/tt.spice inside the tt block.
        # A second .include of tt.spice duplicates models and breaks parameter expansion (l=$, w=$).
        # Only .lib ... tt — sky130.lib.spice already .include's corners/tt.spice inside the tt block.
        # A second .include of tt.spice duplicates models and breaks parameter expansion (l=$, w=$).
        # Do NOT .include the vendor "spinit" file — it contains interactive `set` commands that are
        # not valid SPICE netlist syntax. Those settings are already applied via the .spiceinit that
        # the script copies to the ngspice cwd (PDK directory) before each run.
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

    tin_vec = list(TIN_VECTOR_NS)
    cload_vec = list(CLOAD_VECTOR_PF)
    if args.quick:
        tin_vec = [TIN_VECTOR_NS[0], TIN_VECTOR_NS[-1]]
        cload_vec = [CLOAD_VECTOR_PF[0], CLOAD_VECTOR_PF[-1]]

    cfg = RunConfig(
        root=root,
        template_path=template_path,
        raw_root=raw_root,
        out_root=out_root,
        stdcell_lib_path=stdcell_lib,
        sky130_model_lib=sky130_model,
        deck_preamble=deck_preamble,
        tin_vector_ns=tin_vec,
        load_cap_pf=cload_vec,
        ngspice_bin=args.ngspice_bin,
        dry_run=args.dry_run,
        smoke_test=args.smoke_test,
        jobs=args.jobs,
        ngspice_cwd=ngspice_cwd,
    )

    ensure_dirs(cfg.raw_root, cfg.out_root, root / "results" / "plots")
    template_text = load_template(cfg.template_path)

    if not args.smoke_test and not args.dry_run:
        print(
            "SKY130: first ngspice run can take several minutes while models compile; "
            "little terminal output is normal. Watch: ls -la results/raw/<cell>/\n"
            "If logs show can't find model 'sky130_fd_pr__*__model', apt's ngspice-36 is often missing "
            "SkyWater fixes — try conda-forge 'ngspice', a current build from https://ngspice.sourceforge.net, "
            "or the ngspice version your course documents.\n",
            flush=True,
        )

    for cell in args.cells:
        data = characterize_cell(cell, cfg, template_text)
        out_file = cfg.out_root / f"{cell}.json"
        out_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[done] {cell} -> {out_file}")
        if data["failures"]:
            fails = data["failures"]
            print(f"[warn] {cell}: {len(fails)} issues detected")
            ex = dict(fails[0])  # copy so we can shorten for terminal
            preview = ex.pop("diag_preview", None)
            print(f"       example: {ex}")
            if preview:
                cap = 9000
                tail = preview if len(preview) <= cap else "…\n" + preview[-cap:]
                print(f"       diag tail:\n{tail}")

    if args.dry_run:
        print("Dry run completed. Decks generated without ngspice execution.")
    elif args.smoke_test:
        print("Smoke test used behavioral fixtures — replace with SKY130 + real cells for submission.")
    if args.quick and not args.dry_run:
        print("Quick 2×2 grid — re-run without --quick for full 7×7 NLDM tables.")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
