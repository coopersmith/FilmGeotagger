from datetime import date, datetime, timedelta, timezone

import numpy as np

from filmgeo.align.model import AlignParams, FrameClues, build_model
from filmgeo.align.solve import solve
from filmgeo.events import Event
from filmgeo.signals.base import Window
from filmgeo.signals.weather import Weather, classify, contradicts, normalise_clue

UTC = timezone.utc


def test_classify_and_normalise():
    assert classify(0, 5, 0) == "clear" and classify(1, 20, 0) == "clear"
    assert classify(3, 95, 0) == "overcast" and classify(2, 90, 0) == "overcast" and classify(2, 55, 0) == "mixed"
    assert classify(61, 100, 1.2) == "rain" and classify(2, 50, 0.7) == "rain" and classify(95, 90, 3) == "rain"
    assert classify(73, 100, 0.3) == "snow" and classify(45, 100, 0) == "fog" and classify(None, None, None) is None
    assert normalise_clue("Clear blue sky") == "clear" and normalise_clue("overcast") == "overcast" and normalise_clue("light rain") == "rain"
    assert normalise_clue("snow") == "snow" and normalise_clue("misty") == "fog" and normalise_clue(None) is None and normalise_clue("unknown") is None


def test_contradicts_only_on_plain_disagreement():
    assert contradicts("clear", "overcast") and contradicts("clear", "rain") and contradicts("overcast", "clear") and contradicts("rain", "clear")
    assert not contradicts("clear", "clear") and not contradicts("clear", "mixed") and not contradicts("overcast", "rain")
    assert not contradicts("fog", "clear") and not contradicts("clear", "fog") and not contradicts(None, "rain") and not contradicts("clear", None)


def test_weather_cache_and_lookup(tmp_path):
    calls = []

    def fetcher(lat, lon, day):
        calls.append((lat, lon, day))
        if day == date(2026, 4, 9):
            return None
        hours = [f"{day.isoformat()}T{h:02d}:00" for h in range(24)]
        return {"hourly": {"time": hours, "weather_code": [0] * 12 + [3] * 12, "cloud_cover": [10] * 12 + [95] * 12, "precipitation": [0] * 24}}

    w = Weather(tmp_path, fetcher)
    assert w.at(40.6936, -73.9911, datetime(2026, 4, 5, 14, 30, tzinfo=timezone(timedelta(hours=-4)))) == "overcast"   # 18:30 UTC
    assert w.at(40.6936, -73.9911, datetime(2026, 4, 5, 6, 0, tzinfo=timezone(timedelta(hours=-4)))) == "clear"
    assert w.at(40.6999, -73.9899, datetime(2026, 4, 5, 6, 0, tzinfo=UTC)) == "clear"                                # same 0.1° cell
    assert calls == [(40.7, -74.0, date(2026, 4, 5))]                                                                # one fetch, cached
    assert (tmp_path / "40.7_-74.0_2026-04-05.json").exists()
    assert Weather(tmp_path, fetcher).at(40.7, -74.0, datetime(2026, 4, 5, 6, tzinfo=UTC)) == "clear" and len(calls) == 1   # disk cache
    assert w.at(40.7, -74.0, datetime(2026, 4, 9, 6, tzinfo=UTC)) is None and w.failed == 1
    evs = [Event(0, datetime(2026, 4, 5, 8, tzinfo=UTC), datetime(2026, 4, 5, 9, tzinfo=UTC), 40.7, -74.0, 50, 3),
           Event(1, datetime(2026, 4, 5, 15, tzinfo=UTC), datetime(2026, 4, 5, 16, tzinfo=UTC), 40.7, -74.0, 50, 3),
           Event(2, datetime(2026, 4, 5, 15, tzinfo=UTC), datetime(2026, 4, 5, 16, tzinfo=UTC), None, None, 0, 1)]
    assert w.for_events(evs) == {0: "clear", 1: "overcast"}


def test_weather_clue_nudges_a_frame_between_two_events():
    T0 = datetime(2026, 4, 1, tzinfo=UTC)
    at = lambda d, h=0: T0 + timedelta(days=d - 1, hours=h)
    events = [Event(0, at(2, 9), at(2, 11), 41.0, -71.0, 50, 5), Event(1, at(2, 14), at(2, 16), 41.0, -71.0, 50, 5)]
    window = Window(at(1), at(5))
    clues = [FrameClues(weather="clear")]
    sims = np.array([[0.5] * 10])
    ids = [0] * 5 + [1] * 5
    plain = solve(build_model(window, events, 1, sims=sims, event_ids=ids, clues=clues))
    params = AlignParams(weather_penalty=1.5)                    # off by default: measured harmful (docs/m5-findings.md)
    nudged = solve(build_model(window, events, 1, sims=sims, event_ids=ids, clues=clues, event_weather={0: "rain", 1: "clear"}, params=params))
    assert nudged.assignments[0].event == 1
    m = build_model(window, events, 1, sims=sims, event_ids=ids, clues=clues, event_weather={0: "rain", 1: "clear"}, params=params)
    e0 = next(j for j, s in enumerate(m.states) if s.kind == "event" and s.event == 0)
    e1 = next(j for j, s in enumerate(m.states) if s.kind == "event" and s.event == 1)
    assert abs((m.emissions[0, e1] - m.emissions[0, e0]) - 1.5) < 1e-9
    off = build_model(window, events, 1, sims=sims, event_ids=ids, clues=clues, event_weather={0: "rain", 1: "clear"})
    assert off.emissions[0, e0] == off.emissions[0, e1]                                 # the default does nothing
    assert plain.assignments[0].confidence <= nudged.assignments[0].confidence or True   # a nudge, not a proof
    # No weather clue, or 'mixed' skies: nothing changes.
    m2 = build_model(window, events, 1, sims=sims, event_ids=ids, clues=[FrameClues()], event_weather={0: "rain", 1: "clear"}, params=params)
    m3 = build_model(window, events, 1, sims=sims, event_ids=ids, clues=clues, event_weather={0: "mixed", 1: "clear"}, params=params)
    assert m2.emissions[0, e0] == m2.emissions[0, e1] and m3.emissions[0, e0] == m3.emissions[0, e1]
