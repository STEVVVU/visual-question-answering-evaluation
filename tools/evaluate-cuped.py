from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py-src"))
from data_formulator.experiments import ExperimentSpec, analyze_experiment


parser = argparse.ArgumentParser()
parser.add_argument("run_name")
parser.add_argument("--trials", type=int, default=500)
args = parser.parse_args()
if not args.run_name.replace("-", "").replace("_", "").isalnum() or args.trials < 100:
    parser.error("Use a unique run name and at least 100 trials")
output = ROOT / ".audit" / "evaluation" / f"{args.run_name}.json"
output.parent.mkdir(parents=True, exist_ok=True)
if output.exists():
    parser.error("Refusing to overwrite an evaluation")
seed = 20261020
rng = np.random.default_rng(seed)
results = []
for rho, effect, fraction in [(0.0, 0.0, 0.5), (0.8, 0.0, 0.5),
                               (-0.8, 0.0, 0.5), (0.8, 0.2, 0.5), (0.8, 0.2, 0.3)]:
    spec = ExperimentSpec.parse({
        "table": "synthetic", "unit_column": "unit", "variant_column": "arm",
        "outcome_column": "outcome", "covariate_column": "pre", "control": "control",
        "treatment": "treatment", "expected_treatment_fraction": fraction,
        "randomized_units_confirmed": True, "pre_period_confirmed": True, "fixed_horizon_confirmed": True,
    })
    trials = {method: [] for method in ("ordinary", "cuped")}
    blocked = 0
    for _ in range(args.trials):
        n = 1000
        x = rng.normal(size=n)
        assignment = np.zeros(n, dtype=bool)
        assignment[rng.permutation(n)[:int(n * fraction)]] = True
        y = rho * x + np.sqrt(1 - rho ** 2) * rng.normal(size=n) + effect * assignment
        frame = pd.DataFrame({"unit": [f"u{i:04d}" for i in range(n)],
                              "arm": np.where(assignment, "treatment", "control"),
                              "pre": x, "outcome": y})
        report = analyze_experiment(frame, spec)
        if report["status"] != "ready":
            blocked += 1
            continue
        for method in trials:
            trials[method].append(report["estimates"][method])
    assert blocked == 0, "An evaluation trial was blocked; do not silently exclude it"
    summary = {"rho": rho, "true_effect": effect, "treatment_fraction": fraction,
               "trials": args.trials, "units_per_trial": 1000, "blocked": blocked, "methods": {}}
    for method, observed in trials.items():
        effects = np.array([trial["effect"] for trial in observed])
        covered = np.array([trial["confidence_interval"][0] <= effect <= trial["confidence_interval"][1]
                            for trial in observed])
        mc_se = float(effects.std(ddof=1) / np.sqrt(args.trials))
        bias = float(effects.mean() - effect)
        summary["methods"][method] = {
            "mean_effect": float(effects.mean()), "bias": bias, "bias_monte_carlo_se": mc_se,
            "empirical_effect_variance": float(effects.var(ddof=1)),
            "mean_reported_variance": float(np.mean([trial["standard_error"] ** 2 for trial in observed])),
            "coverage": float(covered.mean()),
            "coverage_monte_carlo_se": float(np.sqrt(covered.mean() * (1 - covered.mean()) / args.trials)),
        }
        assert abs(bias) < 4 * mc_se, (method, "effect bias exceeds four simulation standard errors", summary)
        assert 0.90 <= covered.mean() <= 0.99, (method, "interval calibration outside evaluation bounds", summary)
    ratio = summary["methods"]["cuped"]["empirical_effect_variance"] / summary["methods"]["ordinary"]["empirical_effect_variance"]
    summary["empirical_variance_ratio"] = ratio
    summary["oracle_variance_ratio"] = 1 - rho ** 2
    assert (0.85 < ratio < 1.15) if rho == 0 else ratio < 0.5, summary
    results.append(summary)
    print(f"rho={rho:+.1f}, effect={effect:.1f}, fraction={fraction:.1f}: "
          f"variance ratio={ratio:.3f}; CUPED coverage={summary['methods']['cuped']['coverage']:.3f}", flush=True)

artifact = {
    "seed": seed, "numpy_version": np.__version__, "pandas_version": pd.__version__,
    "specification_example": asdict(spec), "scenarios": results,
    "limits": "Synthetic independent Gaussian units with fixed arm sizes and constant additive effects. Not a real-user study, universal coverage guarantee, sequential test or full CUPED paper reproduction.",
}
output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
print(f"Evaluation retained at {output}")
