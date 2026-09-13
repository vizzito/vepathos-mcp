from vepathos_mcp.schemas.outputs import GeocodedStop
from vepathos_mcp.tools.geocode import classify_geocoded_stops, confidence_ratio


def test_confidence_ratio_accepts_percent_or_unit_interval() -> None:
    assert confidence_ratio(0.75) == 0.75
    assert confidence_ratio(80) == 0.8
    assert confidence_ratio(None) is None


def test_classify_lists_unresolved_and_low_confidence() -> None:
    unresolved, review = classify_geocoded_stops(
        [
            GeocodedStop(stop_id="ok", latitude=-34.6, longitude=-58.4, band="valid", confidence=0.91),
            GeocodedStop(stop_id="edge", latitude=-34.6, longitude=-58.4, band="valid", confidence=0.8),
            GeocodedStop(stop_id="low", latitude=-34.6, longitude=-58.4, band="valid", confidence=0.79),
            GeocodedStop(stop_id="pct", latitude=-34.6, longitude=-58.4, band="valid", confidence=79),
            GeocodedStop(stop_id="rev", latitude=-34.6, longitude=-58.4, band="review", confidence=0.95),
            GeocodedStop(stop_id="miss", latitude=None, longitude=None, band="needs_geocoding"),
        ]
    )
    assert unresolved == ["miss"]
    assert review == ["low", "pct", "rev"]
