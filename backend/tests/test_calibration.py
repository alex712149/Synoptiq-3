import numpy as np
import pytest
from app.blending import calibration

def test_quantile_map_roundtrip_on_identity_distribution(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "CAL_DIR", tmp_path)
    rng = np.random.default_rng(0)
    blend_vals = rng.normal(50, 10, 500)
    truth_vals = blend_vals.copy()  # perfect model -> mapping should be ~identity
    calibration.fit_quantile_map(blend_vals, truth_vals, "test_variable_identity")
    mapped = calibration.apply_quantile_map(52.0, "test_variable_identity")
    assert abs(mapped - 52.0) < 3.0  # allow interpolation slack

def test_unseen_variable_does_not_use_fake_calibration_in_real_mode():
    if calibration.RUNTIME_MODE == "real":
        with pytest.raises(RuntimeError, match="REAL calibration artifact is missing"):
            calibration.apply_quantile_map(77.0, "no_such_variable_ever_fit")
    else:
        assert calibration.apply_quantile_map(77.0, "no_such_variable_ever_fit") == 77.0
