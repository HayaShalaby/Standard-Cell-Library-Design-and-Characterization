#!/usr/bin/env python3
"""Plot delay vs load for invx1/invx2/invx4/invx8 at a chosen input transition (default tin=0.1225 ns).

From repo root:
  python3 scripts/plot_inverter_delay.py
  python3 scripts/plot_inverter_delay.py -d results/nldm -o results/plots/delay.svg --format svg

SVG needs no extra packages; PNG requires matplotlib.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import matplotlib.pyplot as plt

    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False


TIN_DEFAULT = 0.1225
INV_CELLS = ["invx1", "invx2", "invx4", "invx8"]
SVG_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]


def closest_index(vec: List[float], target: float) -> int:
    return min(range(len(vec)), key=lambda i: abs(vec[i] - target))


def delay_avg_ns(rise: Optional[float], fall: Optional[float]) -> Optional[float]:
    """Average propagation delay in ns; use magnitudes (cell_fall may be negative)."""
    if rise is None or fall is None:
        return None
    return (abs(float(rise)) + abs(float(fall))) / 2.0


def resolve_nldm_dir(nldm_dir_arg: Optional[Path]) -> Path:
    if nldm_dir_arg is not None:
        p = Path(nldm_dir_arg).expanduser().resolve()
        if not p.is_dir():
            raise SystemExit(f"Not a directory: {p}")
        return p
    script_here = Path(__file__).resolve().parent
    repo_root = script_here.parent
    repo_nldm = repo_root / "results" / "nldm"
    if repo_nldm.is_dir():
        return repo_nldm
    cwd_nldm = Path.cwd() / "nldm"
    if cwd_nldm.is_dir():
        return cwd_nldm.resolve()
    raise SystemExit(
        "Could not find NLDM JSON folder. Pass -d explicitly, e.g.\n"
        "  python3 scripts/plot_inverter_delay.py -d results/nldm\n"
        "or copy *.json into ./nldm and run from the parent directory."
    )


def collect_curves(
    nldm_root: Path,
    cells: List[str],
    tin_target: float,
) -> List[Dict[str, Any]]:
    curves: List[Dict[str, Any]] = []
    for cell in cells:
        file_path = nldm_root / f"{cell}.json"
        if not file_path.is_file():
            print(f"[skip] missing {file_path}")
            continue
        data = json.loads(file_path.read_text(encoding="utf-8"))
        tins = [float(x) for x in data["input_transition_ns"]]
        loads = [float(x) for x in data["load_cap_pf"]]
        idx = closest_index(tins, tin_target)
        rise_row = data["tables_ns"]["cell_rise"][idx]
        fall_row = data["tables_ns"]["cell_fall"][idx]
        avg = [delay_avg_ns(r, f) for r, f in zip(rise_row, fall_row)]
        x = [l for l, y in zip(loads, avg) if y is not None]
        yv = [v for v in avg if v is not None]
        if not x:
            print(f"[skip] {cell}: no valid delay points at row tin={tins[idx]:.4g} ns.")
            continue
        curves.append(
            {
                "label": f"{cell} (tin={tins[idx]:.4g} ns)",
                "x": x,
                "y": yv,
            }
        )
    return curves


def write_svg(
    curves: List[Dict[str, Any]],
    out_path: Path,
    tin: float,
) -> None:
    w, h = 750, 480
    ml, mr, mt, mb = 72, 40, 48, 56
    pw, ph = w - ml - mr, h - mt - mb

    all_x = [v for c in curves for v in c["x"]]
    all_y = [v for c in curves for v in c["y"]]
    xmin, xmax = min(all_x), max(all_x)
    ymin, ymax = min(all_y), max(all_y)
    if xmax <= xmin:
        xmax = xmin + 1e-12
    if ymax <= ymin:
        ymax = ymin + 1e-12
    pad_x = (xmax - xmin) * 0.05 or 1e-6
    pad_y = (ymax - ymin) * 0.08 or 1e-6
    xmin -= pad_x
    xmax += pad_x
    ymin -= pad_y
    ymax += pad_y

    def tx(xv: float) -> float:
        return ml + (xv - xmin) / (xmax - xmin) * pw

    def ty(yv: float) -> float:
        return mt + ph - (yv - ymin) / (ymax - ymin) * ph

    parts: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        '<rect width="100%" height="100%" fill="#fafafa"/>',
        f'<text x="{w // 2}" y="{mt - 12}" text-anchor="middle" font-size="16" font-family="sans-serif">'
        f'{html.escape(f"Inverter delay vs load (tin ≈ {tin} ns)")}</text>',
    ]

    for i in range(6):
        gx = xmin + (xmax - xmin) * i / 5
        gy = ymin + (ymax - ymin) * i / 5
        parts.append(
            f'<line x1="{tx(gx):.2f}" y1="{mt:.2f}" x2="{tx(gx):.2f}" y2="{mt + ph:.2f}" '
            f'stroke="#ddd" stroke-width="1"/>'
        )
        parts.append(
            f'<line x1="{ml:.2f}" y1="{ty(gy):.2f}" x2="{ml + pw:.2f}" y2="{ty(gy):.2f}" '
            f'stroke="#ddd" stroke-width="1"/>'
        )

    parts.append(
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="#333" stroke-width="1.5"/>'
    )
    parts.append(
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#333" stroke-width="1.5"/>'
    )
    parts.append(
        f'<text x="{ml + pw // 2}" y="{h - 12}" text-anchor="middle" font-size="12" font-family="sans-serif">'
        f"Load capacitance (pF)</text>"
    )
    parts.append(
        f'<text x="18" y="{mt + ph // 2}" text-anchor="middle" font-size="12" font-family="sans-serif" '
        f'transform="rotate(-90 18 {mt + ph // 2})">Delay (ns)</text>'
    )

    for ci, c in enumerate(curves):
        color = SVG_COLORS[ci % len(SVG_COLORS)]
        pts = " ".join(f"{tx(xv):.2f},{ty(yv):.2f}" for xv, yv in zip(c["x"], c["y"]))
        parts.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}"/>'
        )
        for xv, yv in zip(c["x"], c["y"]):
            parts.append(
                f'<circle cx="{tx(xv):.2f}" cy="{ty(yv):.2f}" r="4" fill="{color}"/>'
            )

    lx = ml + pw - 8
    ly = mt + 14
    for ci, c in enumerate(curves):
        color = SVG_COLORS[ci % len(SVG_COLORS)]
        parts.append(f'<rect x="{lx - 140}" y="{ly + ci * 18 - 6}" width="12" height="12" fill="{color}"/>')
        parts.append(
            f'<text x="{lx - 124}" y="{ly + ci * 18 + 4}" font-size="11" font-family="sans-serif">'
            f"{html.escape(c['label'])}</text>"
        )

    parts.append("</svg>")
    out_path.write_text("\n".join(parts), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "-d",
        "--nldm-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Directory with invx*.json. Default: <repo>/results/nldm or ./nldm.",
    )
    p.add_argument(
        "--tin",
        type=float,
        default=TIN_DEFAULT,
        help=f"Target input transition (ns); nearest row is used (default {TIN_DEFAULT}).",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output file. Use .svg for no dependencies; .png needs matplotlib.",
    )
    p.add_argument(
        "--format",
        choices=("auto", "png", "svg"),
        default="auto",
        help="auto: SVG if matplotlib missing or path ends with .svg; else PNG.",
    )
    p.add_argument(
        "--cells",
        nargs="+",
        default=INV_CELLS,
        help="Inverter cell names (default: invx1 invx2 invx4 invx8).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    nldm_root = resolve_nldm_dir(args.nldm_dir)
    repo_root = Path(__file__).resolve().parent.parent

    if args.output is not None:
        out_path = Path(args.output).expanduser().resolve()
    elif args.nldm_dir is not None:
        out_path = Path.cwd() / "inverter_delay_vs_load.svg"
    else:
        out_path = repo_root / "results" / "plots" / "inverter_delay_vs_load.png"

    curves = collect_curves(nldm_root, list(args.cells), args.tin)
    if not curves:
        print(
            f"[error] No curves — need JSON with data in {nldm_root}/invx*.json.",
            file=sys.stderr,
        )
        return 1

    want_svg = args.format == "svg"
    if args.format == "auto":
        if str(out_path).lower().endswith(".svg"):
            want_svg = True
        elif not _HAS_MPL:
            want_svg = True
            if out_path.suffix.lower() in (".png", ""):
                out_path = out_path.with_suffix(".svg")
                print("[note] matplotlib not installed — writing SVG instead:", out_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if want_svg:
        write_svg(curves, out_path, args.tin)
        print(f"[done] wrote {out_path} (SVG, no matplotlib)")
        return 0

    if not _HAS_MPL:
        print(
            "matplotlib is not installed. Either:\n"
            "  pip install matplotlib\n"
            "or use SVG:\n"
            f"  python3 scripts/plot_inverter_delay.py -d {nldm_root} -o plot.svg --format svg",
            file=sys.stderr,
        )
        return 1

    plt.figure(figsize=(7.5, 5.0))
    for c in curves:
        plt.plot(c["x"], c["y"], marker="o", label=c["label"])
    plt.xlabel("Load capacitance (pF)")
    plt.ylabel("Propagation delay (ns), avg of |cell_rise| and |cell_fall|")
    plt.title(f"Inverter family: delay vs load (tin ≈ {args.tin} ns)")
    plt.grid(True, alpha=0.35)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=160)
    print(f"[done] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
