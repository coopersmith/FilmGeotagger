from datetime import datetime, timedelta, timezone

from filmgeo.signals.base import TrailPoint
from filmgeo.signals.places import between, from_trail, search

UTC = timezone.utc
T = datetime(2026, 7, 5, 12, tzinfo=UTC)
m = lambda n: T + timedelta(minutes=n)


def trail():
    return [
        TrailPoint(m(0), 41.55, -71.19, "visit", -14400, label="Home: Tiverton", ref="v-home"),
        TrailPoint(m(30), 41.55, -71.19, "visit", -14400, label="Home: Tiverton", ref="v-home"),
        TrailPoint(m(232), 41.5209, -71.1921, "visit", -14400, label="Venue: Little Compton", ref="v-farm"),
        TrailPoint(m(235), 41.5209, -71.1921, "visit", -14400, label="Venue: Little Compton", ref="v-farm"),
        TrailPoint(m(254), 41.5208, -71.1920, "swarm", -14400, label="Young Family Farm", ref="c-farm"),
        TrailPoint(m(400), 41.60, -71.25, "swarm", -14400, label="Anna D's Café", ref="c-cafe"),      # no visit near it
        TrailPoint(m(500), 41.55, -71.19, "nfc", -14400, label=None, ref="line 12"),
        TrailPoint(m(10), 41.55, -71.19, "photos", -14400, ref="photo"),                                 # not a place
        TrailPoint(m(20), None, None, "swarm", -14400, label="nowhere", ref="x"),                        # no fix
    ]


def test_checkin_merges_into_its_visit_and_names_it():
    ps = from_trail(trail())
    assert [p.name for p in ps] == ["Home: Tiverton", "Young Family Farm", "Anna D's Café", "NFC tap"]
    farm = ps[1]
    assert farm.kind == "check-in" and farm.start == m(232) and farm.end == m(254) and farm.checkin == m(254) and farm.when == m(254)
    assert farm.ref == "c-farm" and not farm.routine and (farm.lat, farm.lon) == (41.5208, -71.1920)
    assert ps[0].routine and ps[0].kind == "visit" and ps[0].when == m(0) and ps[0].end == m(30)
    assert ps[2].kind == "check-in" and ps[2].start == ps[2].end == m(400)
    assert ps[3].kind == "tap"


def test_between_and_search():
    ps = from_trail(trail())
    assert [p.name for p in between(ps, m(200), m(410))] == ["Young Family Farm", "Anna D's Café"]
    assert [p.name for p in between(ps, m(20), m(25))] == ["Home: Tiverton"]           # a span overlapping the range
    assert [p.name for p in search(ps, "family farm")] == ["Young Family Farm"]
    assert [p.name for p in search(ps, "ANNA")] == ["Anna D's Café"] and search(ps, "") == [] and search(ps, "zoo") == []
