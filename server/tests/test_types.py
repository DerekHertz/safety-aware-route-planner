import pytest
from pydantic import ValidationError

from gapcross.types import Assessment, Evidence, LatLon


def test_types_reject_unknown_fields():
    with pytest.raises(ValidationError):
        LatLon(lat=37.85, lon=-122.29, alt=3)


def test_checked_label_is_not_an_evidence_source():
    with pytest.raises(ValidationError):
        Evidence(source="checked_label", ref="x", claim="y", retrieved_at="2026-11-06T00:00:00Z", is_lead=False)


def test_status_values_are_the_four_agreed():
    for status in ("flagged", "unflagged", "unknown", "check_failed"):
        Assessment(status=status, explanation="", evidence=[])
    with pytest.raises(ValidationError):
        Assessment(status="safe", explanation="", evidence=[])
