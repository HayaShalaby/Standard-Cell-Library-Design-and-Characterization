#!/usr/bin/env python3
"""Grouped bar chart: RC model vs SPICE (NLDM) delays at the table midpoint.

Uses invx1 NLDM at tin ≈ 0.1225 ns and C_load ≈ 0.0094 pF by default.
Mapping: tPLH ≈ cell_rise (output rising, PMOS), tPHL ≈ |cell_fall| (output falling, NMOS).

Examples:
  python3 scripts/plot_rc_vs_spice_midpoint.py -o results/plots/rc_vs_spice_midpoint.svg --format svg
  python3 scripts/plot_rc_vs_spice_midpoint.py -o results/plots/rc_vs_spice_midpoint.png --format png
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import List, Tuple

try:
    import matplotlib.pyplot as plt
    import numpy as np

    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False


def closest_index(vec: List[float], target: float) -> int:
    return min(range(len(vec)), key=lambda i: abs(vec[i] - target))


def rc_delays_ps(r_nmos_ohm: float, r_pmos_ohm: float, cload_pf: float) -> Tuple[float, float, float]:
    """tp = 0.69 * R * C with C in F; return (tPHL, tPLH, tp_avg) in ps."""
    c_f = cload_pf * 1e-12
    tphl = 0.69 * r_nmos_ohm * c_f * 1e12
    tplh = 0.69 * r_pmos_ohm * c_f * 1e12
    return tphl, tplh, (tphl + tplh) / 2.0


def load_spice_delays_ps(
    json_path: Path,
    tin_ns: float,
    cload_pf: float,
) -> Tuple[float, float, float, int, int]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    tins = [float(x) for x in data["input_transition_ns"]]
    loads = [float(x) for x in data["load_cap_pf"]]
    ri = closest_index(tins, tin_ns)
    ci = closest_index(loads, cload_pf)
    rise_ns = data["tables_ns"]["cell_rise"][ri][ci]
    fall_ns = data["tables_ns"]["cell_fall"][ri][ci]
    if rise_ns is None or fall_ns is None:
        raise SystemExit(f"NLDM has null at row {ri}, col {ci} in {json_path}")
    tplh = abs(float(rise_ns)) * 1e3
    tphl = abs(float(fall_ns)) * 1e3
    return tphl, tplh, (tphl + tplh) / 2.0, ri, ci


def pct_err(rc: float, spice: float) -> float:
    if rc == 0:
        return float("nan")
    return 100.0 * (spice - rc) / rc


def print_report_table(
    labels: List[str],
    rc_vals: List[float],
    sp_vals: List[float],
) -> None:
    print("\n--- paste into report §8.3–8.4 (values in ps) ---")
    print(f"{'Quantity':<14} {'RC':>10} {'SPICE':>10} {'Error %':>10}")
    for lab, rc, sp in zip(labels, rc_vals, sp_vals):
        print(f"{lab:<14} {rc:10.2f} {sp:10.2f} {pct_err(rc, sp):10.2f}")
    print("---\n")


def write_svg_grouped_bars(
    out_path: Path,
    categories: List[str],
    rc_vals: List[float],
    sp_vals: List[float],
    title: str,
    subtitle: str,
) -> None:
    w, h = 640, 420
    ml, mr, mt, mb = 72, 28, 56, 88
    pw, ph = w - ml - mr, h - mt - mb
    vmax = max(max(rc_vals), max(sp_vals)) * 1.18
    if vmax <= 0:
        vmax = 1.0

    def ty(v: float) -> float:
        return mt + ph - (v / vmax) * ph

    n = len(categories)
    group_w = pw / n
    bw = min(36.0, group_w * 0.32)
    gap = 6.0

    parts: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        '<rect width="100%" height="100%" fill="#fafafa"/>',
        f'<text x="{w // 2}" y="{28}" text-anchor="middle" font-size="15" font-family="sans-serif" font-weight="bold">'
        f"{html.escape(title)}</text>",
        f'<text x="{w // 2}" y="{48}" text-anchor="middle" font-size="11" font-family="sans-serif" fill="#444">'
        f"{html.escape(subtitle)}</text>",
    ]

    # Y grid + axis
    for g in range(6):
        gv = vmax * g / 5
        yy = ty(gv)
        parts.append(
            f'<line x1="{ml}" y1="{yy:.1f}" x2="{ml + pw}" y2="{yy:.1f}" stroke="#e0e0e0" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{ml - 8}" y="{yy + 4:.1f}" text-anchor="end" font-size="10" font-family="sans-serif">{gv:.0f}</text>'
        )

    parts.append(
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="#333" stroke-width="1.5"/>'
    )
    parts.append(
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#333" stroke-width="1.5"/>'
    )
    parts.append(
        f'<text x="16" y="{mt + ph // 2}" text-anchor="middle" font-size="11" font-family="sans-serif" '
        f'transform="rotate(-90 16 {mt + ph // 2})">Delay (ps)</text>'
    )

    rc_color, sp_color = "#7f7f7f", "#1f77b4"
    for i, cat in enumerate(categories):
        cx = ml + group_w * (i + 0.5)
        x_rc = cx - bw - gap / 2
        x_sp = cx + gap / 2
        h_rc = (rc_vals[i] / vmax) * ph
        h_sp = (sp_vals[i] / vmax) * ph
        y0 = mt + ph
        parts.append(
            f'<rect x="{x_rc - bw / 2:.1f}" y="{y0 - h_rc:.1f}" width="{bw:.1f}" height="{h_rc:.1f}" '
            f'fill="{rc_color}" stroke="#555" stroke-width="0.5"/>'
        )
        parts.append(
            f'<rect x="{x_sp - bw / 2:.1f}" y="{y0 - h_sp:.1f}" width="{bw:.1f}" height="{h_sp:.1f}" '
            f'fill="{sp_color}" stroke="#155" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{mt + ph + 22:.1f}" text-anchor="middle" font-size="11" font-family="sans-serif">'
            f"{html.escape(cat)}</text>"
        )
        # value labels on bars
        parts.append(
            f'<text x="{x_rc:.1f}" y="{y0 - h_rc - 4:.1f}" text-anchor="middle" font-size="9" font-family="sans-serif">'
            f"{rc_vals[i]:.1f}</text>"
        )
        parts.append(
            f'<text x="{x_sp:.1f}" y="{y0 - h_sp - 4:.1f}" text-anchor="middle" font-size="9" font-family="sans-serif">'
            f"{sp_vals[i]:.1f}</text>"
        )

    # Legend
    lx, ly = ml + pw - 10, mt + 12
    parts.append(f'<rect x="{lx - 130}" y="{ly}" width="12" height="12" fill="{rc_color}" stroke="#555"/>')
    parts.append(
        f'<text x="{lx - 114}" y="{ly + 10}" font-size="10" font-family="sans-serif">RC model</text>'
    )
    parts.append(f'<rect x="{lx - 130}" y="{ly + 20}" width="12" height="12" fill="{sp_color}" stroke="#155"/>')
    parts.append(
        f'<text x="{lx - 114}" y="{ly + 30}" font-size="10" font-family="sans-serif">SPICE (NLDM)</text>'
    )

    parts.append("</svg>")
    out_path.write_text("\n".join(parts), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-d", "--nldm-dir", type=Path, default=None, help="Directory with <cell>.json (default: <repo>/results/nldm)")
    p.add_argument("--cell", type=str, default="invx1", help="Cell name for NLDM JSON (default: invx1)")
    p.add_argument("--tin", type=float, default=0.1225, help="Input transition (ns), nearest row (default: 0.1225)")
    p.add_argument("--cload", type=float, default=0.0094, help="Load capacitance (pF), nearest column (default: 0.0094)")
    p.add_argument("--r-nmos", type=float, default=7190.0, help="R_NMOS for RC model (ohm, default: 7190)")
    p.add_argument("--r-pmos", type=float, default=17060.0, help="R_PMOS for RC model (ohm, default: 17060)")
    p.add_argument("-o", "--output", type=Path, required=True, help="Output file (.svg or .png)")
    p.add_argument("--format", choices=("auto", "png", "svg"), default="auto", help="auto: from extension or matplotlib availability")
    p.add_argument("--no-print-table", action="store_true", help="Do not print comparison table to stdout")
    return p.parse_args()


def resolve_nldm_dir(arg: Path | None) -> Path:
    if arg is not None:
        p = Path(arg).expanduser().resolve()
        if not p.is_dir():
            raise SystemExit(f"Not a directory: {p}")
        return p
    root = Path(__file__).resolve().parent.parent
    cand = root / "results" / "nldm"
    if cand.is_dir():
        return cand
    raise SystemExit("Pass -d to your NLDM folder (e.g. results/nldm).")


def main() -> int:
    args = parse_args()
    nldm_root = resolve_nldm_dir(args.nldm_dir)
    json_path = nldm_root / f"{args.cell}.json"
    if not json_path.is_file():
        raise SystemExit(f"Missing {json_path}")

    rc_tphl, rc_tplh, rc_avg = rc_delays_ps(args.r_nmos, args.r_pmos, args.cload)
    sp_tphl, sp_tplh, sp_avg, ri, ci = load_spice_delays_ps(json_path, args.tin, args.cload)

    labels = ["tPHL (ps)", "tPLH (ps)", "tp_avg (ps)"]
    rc_list = [rc_tphl, rc_tplh, rc_avg]
    sp_list = [sp_tphl, sp_tplh, sp_avg]
    cats = ["tPHL", "tPLH", "tp_avg"]

    if not args.no_print_table:
        print_report_table(labels, rc_list, sp_list)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        tins = data["input_transition_ns"]
        loads = data["load_cap_pf"]
        print(
            f"NLDM grid point: row {ri} tin={tins[ri]} ns, col {ci} Cload={loads[ci]} pF "
            f"({json_path.name})\n"
        )

    out_path = Path(args.output).expanduser().resolve()
    want_svg = args.format == "svg"
    if args.format == "auto":
        if str(out_path).lower().endswith(".svg"):
            want_svg = True
        elif not _HAS_MPL:
            want_svg = True
            if out_path.suffix.lower() == ".png":
                out_path = out_path.with_suffix(".svg")
                print("[note] matplotlib not installed — writing SVG:", out_path, file=sys.stderr)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    title = "RC model vs SPICE (NLDM) at midpoint"
    subtitle = (
        f"{args.cell}: tin ≈ {args.tin} ns, C_load ≈ {args.cload} pF — "
        f"tPHL≈|cell_fall|, tPLH≈cell_rise"
    )

    if want_svg:
        write_svg_grouped_bars(out_path, cats, rc_list, sp_list, title, subtitle)
        print(f"[done] wrote {out_path} (SVG)")
        return 0

    if not _HAS_MPL:
        print(
            "matplotlib required for PNG. Use:\n"
            f"  python3 scripts/plot_rc_vs_spice_midpoint.py -o plot.svg --format svg\n"
            "or: pip install matplotlib",
            file=sys.stderr,
        )
        return 1

    x = np.arange(len(cats))
    width = 0.35
    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    ax.bar(x - width / 2, rc_list, width, label="RC model", color="#7f7f7f", edgecolor="#555")
    ax.bar(x + width / 2, sp_list, width, label="SPICE (NLDM)", color="#1f77b4", edgecolor="#155")
    ax.set_ylabel("Delay (ps)")
    ax.set_xticks(x)
    ax.set_xticklabels(cats)
    ax.set_title(title + "\n" + subtitle, fontsize=10)
    ax.legend()
    ax.grid(True, axis="y", alpha=0.35)
    ymax = max(max(rc_list), max(sp_list)) * 1.15
    ax.set_ylim(0, ymax)
    for i, (rv, sv) in enumerate(zip(rc_list, sp_list)):
        ax.text(i - width / 2, rv + ymax * 0.02, f"{rv:.1f}", ha="center", va="bottom", fontsize=8)
        ax.text(i + width / 2, sv + ymax * 0.02, f"{sv:.1f}", ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    print(f"[done] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
