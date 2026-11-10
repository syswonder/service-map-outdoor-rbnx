import time
import pytest
from outdoor_mapping.core import Mapping
from test_core import finish


def test_wheeled_record_save_plan_without_gait(tmp_path, monkeypatch):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    m = Mapping(tmp_path, {"locomotion_mode": "wheeled"})
    def feed(i):
        clock[0] += 1
        m.observe("rtk_status", dict(receiver_quality=4, fix_age_s=0., measurement_age_s=0.), "status")
        m.observe("fix", dict(latitude=30., longitude=120.+i*.00001), "fix")
    feed(0)
    assert m.status()["ready"]
    m.capture({"action": "start"})
    for i in range(1, 8): feed(i)
    saved = finish(m, "wheel-test")
    assert saved["locomotion_mode"] == "wheeled"
    roads = saved["network"]["roads"]
    assert all(r["gait_code"] is None for r in roads)
    route = m.plan_route(dict(map_id=saved["map_id"], version=saved["version"], start=roads[0]["points"][0], goal=roads[-1]["points"][-1]))
    assert route["reference_path"] and not route["intervals"]
    assert not route["gait_annotations_available"]
    assert route["quality_intervals"]
    clock[0] += 3
    assert not m.status()["ready"]


def test_legged_still_requires_actual_gait(tmp_path):
    m = Mapping(tmp_path)
    m.observe("rtk_status", dict(receiver_quality=4, fix_age_s=0., measurement_age_s=0.), "status")
    m.observe("fix", dict(latitude=30., longitude=120.), "fix")
    with pytest.raises(ValueError): m.capture({"action": "start"})
