"""Property tests for configurable local B-cell smoothing windows."""
from __future__ import annotations

from math import isfinite

from hypothesis import given, strategies as st

from app.tools import bcell_local


_AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
_RESULT_KEYS = {
    "epitope_score",
    "is_epitope",
    "epitope_positions",
    "epitope_fragments",
    "position_wise_scores",
    "threshold",
    "window_size",
    "method",
}


def _assert_valid_prediction_shape(prediction: dict, sequence: str) -> None:
    assert set(prediction) == _RESULT_KEYS
    assert isinstance(prediction["epitope_score"], float)
    assert isfinite(prediction["epitope_score"])
    assert isinstance(prediction["is_epitope"], bool)
    assert len(prediction["position_wise_scores"]) == len(sequence)
    assert all(isinstance(score, float) and isfinite(score) for score in prediction["position_wise_scores"])
    assert prediction["epitope_positions"] == sorted(prediction["epitope_positions"])
    assert all(1 <= position <= len(sequence) for position in prediction["epitope_positions"])
    for fragment in prediction["epitope_fragments"]:
        assert set(fragment) == {"start", "end", "length"}
        assert fragment["length"] == fragment["end"] - fragment["start"] + 1


@given(
    sequence=st.text(alphabet=_AMINO_ACIDS, min_size=1, max_size=80),
    alternate_window=st.sampled_from((1, 3, 5, 9, 11, 16, 21)),
)
def test_bcell_windows_preserve_legacy_identity_threshold_and_shape(
    sequence: str,
    alternate_window: int,
) -> None:
    """Window 7 is legacy-compatible; other windows only alter smoothing inputs.

    The scores and fragments are obtained from the real local predictor rather
    than from fabricated expected scientific values.

    **Validates: Requirements 2.7, 3.6**
    """
    legacy = bcell_local.predict_bepipred_epitope(sequence)
    explicit_legacy = bcell_local.predict_bepipred_epitope(sequence, window_size=7)
    alternate = bcell_local.predict_bepipred_epitope(
        sequence,
        window_size=alternate_window,
    )

    assert explicit_legacy == legacy
    assert legacy["window_size"] == explicit_legacy["window_size"] == 7
    assert legacy["threshold"] == explicit_legacy["threshold"] == 0.5
    assert alternate["window_size"] == alternate_window
    assert alternate["threshold"] == 0.5
    assert alternate["method"] == legacy["method"] == "bepipred_local"

    _assert_valid_prediction_shape(legacy, sequence)
    _assert_valid_prediction_shape(explicit_legacy, sequence)
    _assert_valid_prediction_shape(alternate, sequence)
