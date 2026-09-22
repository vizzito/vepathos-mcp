"""Two ui_eval columns side by side: what the cascade costs against what it gets right.

    python3 devtools/ui_eval_diff.py luna-low cascada

`agent_eval.py --compare` joins every runner by check name, which is what it is for. This answers one
question instead: between THESE two labels, which rows moved, which got slower, and which cannot be
compared at all because the check itself changed in between. A row nobody can compare is said so, never
shown as a tie.
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "ui_eval"
# Checks rewritten after the first column was measured: comparing them would measure the rewrite.
CHANGED = {"pide kg y m³ antes de proponer"}


def load(label):
    return json.loads((OUT / f"{label}.ui.json").read_text(encoding="utf-8"))


def rate(report, name):
    if name in report.get("passed", {}):
        return report["passed"][name], report.get("_runs")
    partial = report.get("partial", {}).get(name)
    return (partial["passed"], partial["measured"]) if partial else (None, None)


def main(left_label, right_label):
    left, right = load(left_label), load(right_label)
    print(f"{left_label} (runs {left['runs']}) → {right_label} (runs {right['runs']})")
    if right.get("cascade"):
        c = right["cascade"]
        print(f"cascada medida: {c.get('primary_provider')} · {c.get('local_llm_model')} → {c.get('secondary_llm_model')} → {c.get('fallback_provider')}")
    if right.get("decided", {}).get("models"):
        print("quién decidió:", ", ".join(f"{who} ×{n}" for who, n in sorted(right["decided"]["models"].items())))
    for key in right["cases"]:
        if key not in left["cases"]:
            continue
        here, there = left["cases"][key], right["cases"][key]
        here["_runs"], there["_runs"] = left["runs"], right["runs"]
        seconds = [f"{median(here.get('seconds', []))}s", f"{median(there.get('seconds', []))}s"]
        print(f"\n[{key}]  {seconds[0]} → {seconds[1]}{who(there)}")
        for name in sorted(set(here.get("passed", {})) | set(there.get("passed", {})) | set(here.get("partial", {})) | set(there.get("partial", {}))):
            a, an = rate(here, name)
            b, bn = rate(there, name)
            if name in CHANGED:
                verdict = "el chequeo cambió en el medio: no comparable"
            elif a is None or b is None:
                verdict = "no medido de un lado"
            else:
                verdict = "=" if a / an == b / bn else ("peor" if a / an > b / bn else "mejor")
            print(f"  {name:58} {fmt(a, an):>8} → {fmt(b, bn):>8}  {verdict}")


def who(report):
    """Which model answered this case, when the run recorded it: a tie means nothing without knowing who tied."""
    models = sorted({model for run in report.get("transcripts", []) for turn in (run.get("decidedBy") or []) for model in turn})
    return f"  ({', '.join(models)})" if models else ""


def fmt(passed, runs):
    return "-" if passed is None else f"{passed}/{runs}"


def median(values):
    return sorted(values)[len(values) // 2] if values else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("uso: ui_eval_diff.py <label-antes> <label-despues>")
    main(sys.argv[1], sys.argv[2])
