from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from data_formulator.experiments import ExperimentSpec, analyze_experiment


@pytest.fixture
def frame():
    return pd.read_csv(Path(__file__).resolve().parents[2] / "tools/fixtures/synthetic-experiment.csv")


@pytest.fixture
def spec():
    return ExperimentSpec.parse({
        "table": "experiment", "unit_column": "unit_id", "variant_column": "variant",
        "outcome_column": "outcome", "covariate_column": "pre_period",
        "control": "control", "treatment": "treatment", "expected_treatment_fraction": 0.5,
        "randomized_units_confirmed": True, "pre_period_confirmed": True,
        "fixed_horizon_confirmed": True,
    })


def test_ordinary_estimate_matches_independent_scipy_test(frame, spec):
    report = analyze_experiment(frame, spec)
    control = frame.loc[frame.variant == "control", "outcome"].to_numpy()
    treatment = frame.loc[frame.variant == "treatment", "outcome"].to_numpy()
    reference = stats.ttest_ind(treatment, control, equal_var=False)
    ordinary = report["estimates"]["ordinary"]
    assert report["status"] == "ready"
    assert ordinary["effect"] == pytest.approx(treatment.mean() - control.mean())
    assert ordinary["p_value"] == pytest.approx(reference.pvalue)
    assert ordinary["confidence_interval"] == pytest.approx(reference.confidence_interval())
    assert ordinary["degrees_of_freedom"] == pytest.approx(reference.df)


def test_paper_common_coefficient_and_adjusted_mean_difference(frame, spec):
    report = analyze_experiment(frame, spec)
    x, y = frame.pre_period.to_numpy(), frame.outcome.to_numpy()
    reference_theta = np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1)
    adjusted = y - reference_theta * (x - x.mean())
    treatment = frame.variant == "treatment"
    reference = stats.ttest_ind(adjusted[treatment], adjusted[~treatment], equal_var=False)
    assert report["theta"] == pytest.approx(reference_theta)
    assert report["estimates"]["cuped"]["effect"] == pytest.approx(
        adjusted[treatment].mean() - adjusted[~treatment].mean())
    assert report["estimates"]["cuped"]["confidence_interval"] == pytest.approx(reference.confidence_interval())
    assert report["estimated_effect_variance_reduction"] > 0.5


def test_units_are_sorted_for_reproducible_results_and_selected_input_hash(frame, spec):
    baseline = analyze_experiment(frame, spec)
    shuffled = frame.sample(frac=1, random_state=42)
    assert analyze_experiment(shuffled, spec) == baseline
    shuffled["unselected_metadata"] = "irrelevant"
    assert analyze_experiment(shuffled, spec) == baseline
    shuffled.loc[shuffled.index[0], "outcome"] += 1
    assert analyze_experiment(shuffled, spec)["selected_input_sha256"] != baseline["selected_input_sha256"]


def test_affine_covariate_changes_do_not_change_the_adjusted_effect(frame, spec):
    original = analyze_experiment(frame, spec)["estimates"]["cuped"]
    frame.pre_period = 19 - 7 * frame.pre_period
    changed = analyze_experiment(frame, spec)["estimates"]["cuped"]
    assert changed["effect"] == pytest.approx(original["effect"])
    assert changed["standard_error"] == pytest.approx(original["standard_error"])


@pytest.mark.parametrize("field,value", [
    ("pre_period_confirmed", False), ("randomized_units_confirmed", 1),
    ("fixed_horizon_confirmed", "true"), ("expected_treatment_fraction", True),
    ("expected_treatment_fraction", float("nan")), ("expected_treatment_fraction", 0.99),
    ("expected_treatment_fraction", 10 ** 400),
    ("control", "treatment"), ("unit_column", "variant"), ("table", ""),
    ("outcome_column", None),
])
def test_spec_rejects_invalid_fields(spec, field, value):
    with pytest.raises(ValueError):
        ExperimentSpec.parse({**asdict(spec), field: value})


def test_spec_rejects_extra_and_missing_fields(spec):
    with pytest.raises(ValueError):
        ExperimentSpec.parse({**asdict(spec), "model": {}})
    fields = asdict(spec)
    fields.pop("control")
    with pytest.raises(ValueError):
        ExperimentSpec.parse(fields)


@pytest.mark.parametrize("bad", [None, float("inf"), float("nan"), 1e13, True, "12"])
def test_invalid_outcome_values_are_not_silently_dropped_or_coerced(frame, spec, bad):
    frame["outcome"] = frame.outcome.astype(object)
    frame.loc[0, "outcome"] = bad
    with pytest.raises(ValueError):
        analyze_experiment(frame, spec)


@pytest.mark.parametrize("mutation,message", [
    ("duplicate", "unique"), ("blank", "nonempty"), ("null", "missing"),
    ("third-arm", "only"), ("small-arm", "30 independent"), ("column", "exist"),
])
def test_invalid_experiment_designs_are_rejected(frame, spec, mutation, message):
    if mutation == "duplicate":
        frame.loc[0, "unit_id"] = frame.loc[1, "unit_id"]
    elif mutation == "blank":
        frame.loc[0, "unit_id"] = " "
    elif mutation == "null":
        frame.loc[0, "unit_id"] = None
    elif mutation == "third-arm":
        frame.loc[0, "variant"] = "holdout"
    elif mutation == "small-arm":
        frame["variant"] = "control"
        frame.loc[:28, "variant"] = "treatment"
    elif mutation == "column":
        frame = frame.drop(columns=["pre_period"])
    with pytest.raises(ValueError, match=message):
        analyze_experiment(frame, spec)


def test_srm_blocks_inference_but_preserves_diagnostics(frame, spec):
    frame["variant"] = "control"
    frame.loc[:899, "variant"] = "treatment"
    report = analyze_experiment(frame, spec)
    assert report["status"] == "blocked"
    assert report["estimates"] is None
    assert report["diagnostics"][0]["code"] == "SAMPLE_RATIO_MISMATCH"
    matched = ExperimentSpec.parse({**asdict(spec), "expected_treatment_fraction": 0.9})
    assert analyze_experiment(frame, matched)["status"] == "ready"


def test_constant_covariate_and_zero_outcome_variance_block_results(frame, spec):
    frame["pre_period"] = 1.0
    report = analyze_experiment(frame, spec)
    assert report["status"] == "blocked"
    assert report["diagnostics"][0]["code"] == "CONSTANT_COVARIATE"
    frame["pre_period"] = np.arange(len(frame))
    frame["outcome"] = 0.0
    assert analyze_experiment(frame, spec)["diagnostics"][0]["code"] == "DEGENERATE_VARIANCE"


def test_row_limits_are_enforced(frame, spec):
    with pytest.raises(ValueError, match="between 60"):
        analyze_experiment(frame.head(59), spec)
    with pytest.raises(ValueError, match="100,000"):
        analyze_experiment(pd.concat([frame] * 101), spec)


@pytest.mark.parametrize("scale", [1e-80, 1e-100, 1e-140])
def test_tiny_outcome_units_keep_scale_equivalent_inference(frame, spec, scale):
    baseline = analyze_experiment(frame, spec)["estimates"]
    frame["outcome"] *= scale
    report = analyze_experiment(frame, spec)
    assert report["status"] == "ready"
    for method in ("ordinary", "cuped"):
        assert report["estimates"][method]["p_value"] == pytest.approx(baseline[method]["p_value"])
        assert report["estimates"][method]["effect"] / scale == pytest.approx(baseline[method]["effect"])


def test_unrepresentable_variance_comparison_blocks_instead_of_overflowing(spec):
    rng = np.random.default_rng(42)
    frame = pd.DataFrame({"unit_id": [str(i) for i in range(1000)],
        "variant": ["control"] * 500 + ["treatment"] * 500,
        "pre_period": rng.normal(size=1000),
        "outcome": np.r_[rng.normal(scale=1e-150, size=500), np.full(500, 1e12)]})
    result = analyze_experiment(frame, spec)
    assert result["status"] == "blocked"
    assert result["estimates"] is None
    assert result["diagnostics"][0]["code"] == "NUMERICAL_RANGE"
