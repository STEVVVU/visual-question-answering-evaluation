from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import sys

import numpy as np
import pandas as pd
from scipy import stats


MAX_ROWS = 100_000
METHOD_VERSION = "trialdesk-cuped-1"


@dataclass(frozen=True)
class ExperimentSpec:
    table: str
    unit_column: str
    variant_column: str
    outcome_column: str
    covariate_column: str
    control: str
    treatment: str
    expected_treatment_fraction: float
    randomized_units_confirmed: bool
    pre_period_confirmed: bool
    fixed_horizon_confirmed: bool

    @classmethod
    def parse(cls, value: object) -> ExperimentSpec:
        keys = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError("Provide exactly the documented experiment specification fields.")
        for name in ("table", "unit_column", "variant_column", "outcome_column",
                     "covariate_column", "control", "treatment"):
            text = value[name]
            if not isinstance(text, str) or not text.strip() or len(text) > 200:
                raise ValueError(f"{name} must be a nonempty string of at most 200 characters.")
        columns = [value[name] for name in (
            "unit_column", "variant_column", "outcome_column", "covariate_column")]
        if len(set(columns)) != 4:
            raise ValueError("Choose four distinct columns for unit, variant, outcome and pre-period metric.")
        if value["control"] == value["treatment"]:
            raise ValueError("Control and treatment labels must differ.")
        fraction = value["expected_treatment_fraction"]
        if type(fraction) not in (int, float) or not 0.05 <= fraction <= 0.95:
            raise ValueError("Expected treatment fraction must be between 0.05 and 0.95.")
        for name in ("randomized_units_confirmed", "pre_period_confirmed", "fixed_horizon_confirmed"):
            if value[name] is not True:
                raise ValueError("Confirm randomized independent units, pre-treatment data and a fixed analysis horizon.")
        return cls(**value)


def _welch(values: np.ndarray, treatment: np.ndarray) -> dict | None:
    control_values, treatment_values = values[~treatment], values[treatment]
    n_control, n_treatment = len(control_values), len(treatment_values)
    control_mean, treatment_mean = float(control_values.mean()), float(treatment_values.mean())
    variance_control = float(control_values.var(ddof=1)) / n_control
    variance_treatment = float(treatment_values.var(ddof=1)) / n_treatment
    variance = variance_control + variance_treatment
    if variance <= 0 or not math.isfinite(variance):
        return None
    standard_error = math.sqrt(variance)
    degrees = 1 / (
        (variance_control / variance) ** 2 / (n_control - 1)
        + (variance_treatment / variance) ** 2 / (n_treatment - 1))
    effect = treatment_mean - control_mean
    margin = float(stats.t.ppf(0.975, degrees)) * standard_error
    return {
        "control_mean": control_mean,
        "treatment_mean": treatment_mean,
        "effect": effect,
        "standard_error": standard_error,
        "degrees_of_freedom": degrees,
        "confidence_interval": [effect - margin, effect + margin],
        "p_value": float(2 * stats.t.sf(abs(effect / standard_error), degrees)),
    }


def analyze_experiment(frame: pd.DataFrame, spec: ExperimentSpec) -> dict:
    columns = [spec.unit_column, spec.variant_column, spec.outcome_column, spec.covariate_column]
    if not frame.columns.is_unique or any(column not in frame.columns for column in columns):
        raise ValueError("Every selected column must exist exactly once in the table.")
    if not 60 <= len(frame) <= MAX_ROWS:
        raise ValueError(f"Analyze between 60 and {MAX_ROWS:,} rows without sampling.")
    selected = frame[columns].copy()
    if selected.isna().any().any():
        raise ValueError("Selected columns contain missing values. No rows were silently dropped.")
    units = selected[spec.unit_column].map(str)
    if units.str.strip().eq("").any() or units.duplicated().any():
        raise ValueError("Unit IDs must be nonempty and unique. Aggregate repeated units before analysis.")
    variants = selected[spec.variant_column].map(str)
    if not variants.isin([spec.control, spec.treatment]).all():
        raise ValueError("The variant column must contain only the specified control and treatment labels.")
    for column in (spec.outcome_column, spec.covariate_column):
        series = selected[column]
        if (not pd.api.types.is_numeric_dtype(series.dtype)
                or pd.api.types.is_bool_dtype(series.dtype)
                or pd.api.types.is_complex_dtype(series.dtype)):
            raise ValueError(f"{column} must contain real numeric values, not strings or booleans.")
        values = series.to_numpy(dtype=np.float64)
        if not np.isfinite(values).all() or (np.abs(values) > 1e12).any():
            raise ValueError(f"{column} must contain finite values with magnitude at most 1e12.")
    selected[spec.unit_column] = units
    selected[spec.variant_column] = variants
    selected = selected.sort_values(spec.unit_column, kind="stable")
    y = selected[spec.outcome_column].to_numpy(dtype=np.float64)
    x = selected[spec.covariate_column].to_numpy(dtype=np.float64)
    is_treatment = selected[spec.variant_column].eq(spec.treatment).to_numpy()
    n_treatment = int(is_treatment.sum())
    n_control = len(y) - n_treatment
    if min(n_control, n_treatment) < 30:
        raise ValueError("At least 30 independent units per arm are required for this approximate analysis.")
    canonical = {
        "columns": columns,
        "rows": [[unit, variant, float(outcome), float(pre)]
                 for unit, variant, outcome, pre in selected.itertuples(index=False, name=None)],
    }
    dataset_hash = hashlib.sha256(json.dumps(
        canonical, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    srm_p = float(stats.binomtest(n_treatment, len(y), spec.expected_treatment_fraction).pvalue)
    report = {
        "schema_version": 1,
        "method_version": METHOD_VERSION,
        "specification": asdict(spec),
        "selected_input_sha256": dataset_hash,
        "counts": {"control": n_control, "treatment": n_treatment, "total": len(y)},
        "sample_ratio_p_value": srm_p,
        "status": "blocked",
        "diagnostics": [],
        "estimates": None,
        "assumptions": [
            "One independent randomized unit per row; no interference or post-treatment selection.",
            "The covariate was measured before treatment; its timing is a user declaration, not inferred from the data.",
            "One preselected outcome and a fixed analysis horizon; intervals are not valid for optional stopping or multiple testing.",
            "Welch t intervals are approximate; CUPED uses one pooled estimated coefficient and plug-in standard errors.",
            "At least 30 units per arm is an input policy, not proof of valid asymptotics for heavy-tailed data.",
        ],
    }
    if srm_p < 0.001:
        report["diagnostics"].append({"code": "SAMPLE_RATIO_MISMATCH", "severity": "error",
            "message": "Allocation differs from the declared randomization ratio (exact binomial p < 0.001). Investigate before inference."})
        return report
    centered_x = x - x.mean()
    denominator = float(centered_x @ centered_x)
    if denominator <= 0 or not math.isfinite(denominator):
        report["diagnostics"].append({"code": "CONSTANT_COVARIATE", "severity": "error",
            "message": "The pre-period metric has no usable variance. Choose another covariate."})
        return report
    theta = float(centered_x @ (y - y.mean()) / denominator)
    adjusted = y - theta * centered_x
    ordinary, cuped = _welch(y, is_treatment), _welch(adjusted, is_treatment)
    if ordinary is None or cuped is None:
        report["diagnostics"].append({"code": "DEGENERATE_VARIANCE", "severity": "error",
            "message": "The outcome or adjusted outcome has no usable residual variance. No inferential result is reported."})
        return report
    if cuped["standard_error"] > ordinary["standard_error"] * math.sqrt(sys.float_info.max):
        report["diagnostics"].append({"code": "NUMERICAL_RANGE", "severity": "error",
            "message": "The variance comparison exceeds floating-point range. Rescale or inspect the outcome distribution; no inference is reported."})
        return report
    reduction = 1 - (cuped["standard_error"] / ordinary["standard_error"]) ** 2
    if reduction < 0:
        report["diagnostics"].append({"code": "VARIANCE_INCREASE", "severity": "warning",
            "message": "CUPED increases the estimated effect variance on this dataset. Do not assume adjustment always helps."})
    spread = float(y.std(ddof=1))
    if spread > 0 and float(np.max(np.abs(y - y.mean()))) > 10 * spread:
        report["diagnostics"].append({"code": "EXTREME_OUTCOME", "severity": "warning",
            "message": "An extreme outcome may undermine the approximation. Inspect the distribution and experiment design."})
    report.update({
        "status": "ready",
        "theta": theta,
        "estimated_effect_variance_reduction": reduction,
        "estimates": {"ordinary": ordinary, "cuped": cuped},
    })
    json.dumps(report, allow_nan=False)
    return report
