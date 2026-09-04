from datetime import datetime, timedelta, timezone

import numpy as np

from filmgeo.align.pipeline import Verdict, anchors_from_verdicts, clues_from_verdicts, load_verdicts, save_verdicts
from filmgeo.align.report import interval_text
from filmgeo.align.solve import Assignment
from filmgeo.photos.library import Asset

UTC = timezone.utc
T = datetime(2026, 4, 2, 9, tzinfo=UTC)


def test_verdicts_roundtrip_and_merge(tmp_path):
    v1 = {1: Verdict(["a", "b"], "a", 0.9, "same sofa", {"time_of_day": "morning", "indoor": True})}
    save_verdicts("r", v1, {"model": "m"}, tmp_path)
    save_verdicts("r", {2: Verdict(["c"], None, 0.3)}, {"model": "m"}, tmp_path)   # second run merges
    back = load_verdicts("r", tmp_path)
    assert set(back) == {1, 2} and back[1] == v1[1] and back[2].match is None
    assert load_verdicts("missing", tmp_path) == {}


def test_anchors_and_clues_from_verdicts():
    pool = [Asset("a", "a.jpg", T, -14400, 41.0, -71.0), Asset("b", "b.jpg", T + timedelta(hours=1), -14400, 41.1, -71.0)]
    sims = np.array([[0.95, 0.5], [0.4, 0.6], [0.1, 0.1]])
    verdicts = {1: Verdict(["a", "b"], "a", 0.9, clues={"time_of_day": "morning"}),
                2: Verdict(["b"], None, 0.2),
                3: Verdict(["zzz"], "zzz", 0.8)}             # a uuid not in the pool: ignored
    anchors = anchors_from_verdicts(verdicts, pool, [0, 0], sims)
    assert len(anchors) == 1
    a = anchors[0]
    assert (a.frame, a.uuid, a.time, a.confidence, a.similarity, a.lat, a.tzoffset) == (0, "a", T, 0.9, 0.95, 41.0, -14400)
    clues = clues_from_verdicts(verdicts, 3)
    assert clues[0].time_of_day == "morning" and clues[1] is None


def test_interval_text():
    a = Assignment(0, 0, "interpolated", T, T, T + timedelta(hours=3), 0.5, 0.0)
    assert interval_text(a) == "between Thu 2 Apr 09:00 and 12:00"
    a.t_hi = T + timedelta(days=2)
    assert interval_text(a).startswith("between Thu 2 Apr 09:00 and Sat 4 Apr")
    a.source = "anchored"
    a.t_hi = T + timedelta(hours=3)
    assert interval_text(a) == "this occasion, Thu 2 Apr 09:00–12:00"


def test_outings_feed_same_outing_pairs(tmp_path, monkeypatch):
    from filmgeo.verify import outing as op

    o = op.Outings("r", [{"frames": [1, 2, 3], "description": "x", "confidence": 0.9}])
    o.save(tmp_path)
    monkeypatch.setattr(op, "OUTINGS_DIR", tmp_path)
    loaded = op.Outings.load("r", tmp_path)
    assert loaded.same_outing_pairs(3) == {(0, 1), (1, 2)}
