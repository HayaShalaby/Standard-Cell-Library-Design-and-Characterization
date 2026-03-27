#!/usr/bin/env python3
"""Plot delay vs load for invx1/invx2/invx4/invx8 at tin=0.1225ns."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt


TIN_TARGET = 0.1225
INV_CELLS = ["invx1", "invx2", "invx4", "invx8"]


def closest_index(vec: list[float], target: float) -> int:
    return min(range(len(vec)), key=lambda i: abs(vec[i] - target))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    nldm_root = root / "results" / "nldm"
    out_path = root / "results" / "plots" / "inverter_delay_vs_load.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(7.5, 5.0))

    for cell in INV_CELLS:
        file_path = nldm_root / f"{cell}.json"
        if not file_path.exists():
            print(f"[skip] missing {file_path}")
            continue
        data = json.loads(file_path.read_text(encoding="utf-8"))
        tins = data["input_transition_ns"]
        loads = data["load_cap_pf"]
        idx = closest_index(tins, TIN_TARGET)

        rise = data["tables_ns"]["cell_rise"][idx]
        fall = data["tables_ns"]["cell_fall"][idx]
        avg = []
        for r, f in zip(rise, fall):
            if r is None or f is None:
                avg.append(None)
            else:
                avg.append((r + f) / 2.0)

        x = [l for l, y in zip(loads, avg) if y is not None]
        y = [y for y in avg if y is not None]
        plt.plot(x, y, marker="o", label=cell)

    plt.xlabel("Load Capacitance (pF)")
    plt.ylabel("Propagation Delay (ns) [avg rise/fall]")
    plt.title("Inverter Family Delay vs Load at tin=0.1225ns")
    plt.grid(True, alpha=0.35)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=160)
    print(f"[done] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
