import json
import time

import pytest

from outdoor_mapping.core import Mapping
from outdoor_mapping.network import apply, preview
from outdoor_mapping.routing import plan_route


@pytest.fixture
def mapping(tmp_path, monkeypatch):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    service = Mapping(tmp_path)
    service.clock = clock
    return service


def feed(service, i, quality=4, gait=0x1001, north=0):
    service.clock[0] += 1
    service.observe("rtk_status", dict(receiver_quality=quality, fix_age_s=0., measurement_age_s=0.), "status")
    service.observe("gait", {"code": gait}, "feedback")
    service.observe("fix", dict(latitude=30.+north*.00001, longitude=120.+i*.00001), "fix")


def finish(service, name):
    service.capture({"action": "stop"})
    value = service.merge
    service.apply_merge(dict(preview_id=value["preview_id"],
        decisions={c["id"]: "connect" for c in value["candidates"]},
        choices={c["id"]: "old" for c in value["conflicts"]}))
    service.save_map(name)
    service.load_map(name)
    return service.get_map()


def test_record_filter_persist_reverse_and_quality(mapping):
    feed(mapping, 0)
    mapping.capture({"action": "start"})
    for i in range(1, 8):
        feed(mapping, i, quality=2 if i >= 5 else 5 if i >= 3 else 4,
             gait=0x1003 if 2 <= i < 5 else 0x1001)
    saved = finish(mapping, "campus-v1")
    assert {r["grade"] for r in saved["network"]["roads"]} == {"fixed", "float", "dgps"}
    assert {r["gait"] for r in saved["network"]["roads"]} == {"flat", "stairs"}
    points = saved["network"]["roads"]
    begin, end = points[0]["points"][0], points[-1]["points"][-1]
    payload = dict(map_id=saved["map_id"], version=saved["version"], start=begin, goal=end)
    forward = mapping.plan_route(payload)
    reverse = mapping.plan_route({**payload, "start": end, "goal": begin})
    assert forward["low_accuracy"] and not forward["execution_enabled"]
    assert forward["reference_path"] == list(reversed(reverse["reference_path"]))
    assert [x["gait_code"] for x in forward["intervals"]] == list(reversed([x["gait_code"] for x in reverse["intervals"]]))
    assert mapping.store.load("campus-v1") == saved
    with pytest.raises(ValueError, match="immutable"):
        mapping.store.save(saved)
    raw = next(mapping.store.recordings.glob("*/events.jsonl")).read_text()
    assert '"raw_message"' not in raw  # This fixture supplies native dictionaries.
    assert '"kind": "gait"' in raw
    mapping.store.delete("campus-v1")
    with pytest.raises(ValueError, match="immutable"):
        mapping.store.save(saved)


def test_pause_invalid_fix_and_stationary_gait_keep_boundaries(mapping):
    feed(mapping, 0)
    mapping.capture({"action": "start"})
    feed(mapping, 1)
    mapping.capture({"action": "pause"})
    feed(mapping, 2, gait=0x1003)
    mapping.capture({"action": "resume"})
    feed(mapping, 3, gait=0x1003)
    feed(mapping, 4, gait=0x1003)
    feed(mapping, 5, quality=0)
    feed(mapping, 6)
    feed(mapping, 7)
    mapping.capture({"action": "stop"})
    roads = mapping.draft["network"]["roads"]
    assert len(roads) == 3
    assert roads[1]["gait"] == "stairs"
    assert roads[0]["to"] != roads[1]["from"]
    assert roads[1]["to"] != roads[2]["from"]
    assert len(mapping.recorder.points) == 6


def test_append_merge_requires_review_and_preserves_origin(mapping):
    feed(mapping, 0)
    mapping.capture({"action": "start"})
    for i in range(1, 6):
        feed(mapping, i)
    old = finish(mapping, "old")
    feed(mapping, 3)
    mapping.capture({"action": "start"})
    for n in range(1, 6):
        feed(mapping, 3, north=n)
    mapping.capture({"action": "stop"})
    with pytest.raises(ValueError, match="Review"):
        mapping.apply_merge({"preview_id": mapping.merge["preview_id"]})
    value = mapping.merge
    mapping.apply_merge(dict(preview_id=value["preview_id"],
        decisions={c["id"]: "connect" for c in value["candidates"]}, choices={}))
    mapping.save_map("new")
    assert mapping.store.load("old") == old
    new = mapping.store.load("new")
    assert new["origin_wgs84"] == old["origin_wgs84"] and new["parent_map_id"] == "old"
    mapping.load_map("new")
    result = mapping.plan_route(dict(map_id="new", version=new["version"],
        start=old["network"]["roads"][0]["points"][0], goal=new["network"]["roads"][-1]["points"][-1]))
    assert result["length_m"] > 0


def test_unconfirmed_intersection_is_disconnected():
    def road(name, points):
        return dict(id=name, **{"from": name+"a", "to": name+"b"},
            points=[dict(x=x,y=y) for x,y in points], length_m=10., gait="flat", gait_code=1,
            grade="fixed", sources=[name])
    a = road("a", [(0,0),(5,0),(10,0)])
    b = road("b", [(5,-5),(5,0),(5,5)])
    net = dict(nodes=[], roads=[a,b])
    with pytest.raises(ValueError, match="disconnected"):
        plan_route(net, dict(x=0,y=0), dict(x=5,y=5))
    value = preview(None, net, .1)
    joined = apply(value, {c["id"]:"connect" for c in value["candidates"]}, {})
    assert plan_route(joined, dict(x=0,y=0), dict(x=5,y=5))["length_m"] == 10


def test_restart_retains_raw_and_marks_interrupted(mapping):
    feed(mapping, 0)
    mapping.capture({"action": "start"})
    feed(mapping, 1)
    sid = mapping.recorder.session["session_id"]
    mapping.recorder.stream.flush()
    restarted = Mapping(mapping.store.root)
    session = json.loads((mapping.store.recordings/sid/"session.json").read_text())
    assert session["state"] == "interrupted"
    restarted.capture({"action":"recover", "session_id":sid})
    assert restarted.draft["network"]["roads"]
    mapping.close()


def test_failed_new_capture_keeps_loaded_map(mapping):
    feed(mapping, 0)
    mapping.capture({"action":"start"})
    feed(mapping, 1)
    saved = finish(mapping,"retained")
    mapping.clock[0] += 10
    with pytest.raises(ValueError,match="Fresh"):
        mapping.capture({"action":"start","new_map":True})
    assert mapping.get_map() == saved


def test_partial_overlap_and_self_crossing():
    from outdoor_mapping.track import distance
    def road(name, xy, gait="flat"):
        points = [dict(x=x,y=y) for x,y in xy]
        return dict(id=name, **{"from":name+'a',"to":name+'b'},points=points,
                    length_m=sum(distance(a,b) for a,b in zip(points,points[1:])),
                    gait=gait,gait_code=1 if gait=='flat' else 2,sources=[name])
    old=road('old',[(0,0),(10,0)])
    new=road('new',[(4,0),(6,0)],'stairs')
    value=preview(dict(nodes=[],roads=[old]),dict(nodes=[],roads=[new]),.1)
    merged=apply(value,{c['id']:'separate' for c in value['candidates']},
                 {c['id']:'new' for c in value['conflicts']})
    assert any(r['id']=='new' for r in merged['roads'])
    assert sum(r['length_m'] for r in merged['roads'] if r['gait']=='stairs') == pytest.approx(4)
    assert sum(r['length_m'] for r in merged['roads'] if r['gait']=='flat') == pytest.approx(8)
    loop=road('loop',[(-4,-4),(4,4),(-4,4),(4,-4)])
    value=preview(None,dict(nodes=[],roads=[loop]),.1)
    merged=apply(value,{c['id']:'connect' for c in value['candidates']},{})
    assert all(r['length_m']>.001 for r in merged['roads'])
    assert plan_route(merged,dict(x=-4,y=-4),dict(x=4,y=-4))['length_m']==pytest.approx(8*2**.5)


def test_outage_at_same_position_does_not_join():
    from outdoor_mapping.network import build
    points=[dict(latitude=30.,longitude=120.+x*.00001,time_s=t,utc=str(t),
                 index=i,quality=4,grade='fixed',gait='flat',gait_code=1,segment=0)
            for i,(x,t) in enumerate([(0,0),(1,1),(1,10),(2,11)])]
    roads=build(points,'outage')['network']['roads']
    assert len(roads)==2
    assert roads[0]['to']!=roads[1]['from']


def test_stale_feedback_jump_and_truncated_recovery(mapping):
    feed(mapping,0)
    mapping.capture({'action':'start'})
    for i in range(1,15):
        mapping.clock[0] += 1
        mapping.observe('rtk_status',dict(receiver_quality=4,fix_age_s=0.,measurement_age_s=0.),'status')
        mapping.observe('fix',dict(latitude=30.,longitude=120.+(100 if i==6 else i)*.00001),'fix')
    mapping.capture({'action':'stop'})
    assert any(r['gait']=='unknown' for r in mapping.draft['network']['roads'])
    assert any(c['rejected'] for c in mapping.draft['report']['chunks'])
    sid=mapping.recorder.session['session_id']
    raw=mapping.store.recordings/sid/'events.jsonl'
    original=raw.read_bytes()
    raw.write_bytes(original+b'{"kind":')
    mapping.capture({'action':'discard'})
    mapping.capture({'action':'recover','session_id':sid})
    assert mapping.draft['report']['recovery_warning']
    assert raw.read_bytes()==original+b'{"kind":'


def test_confirmed_branches_share_an_existing_junction():
    import math
    def road(name,a,b,begin=None,end=None):
        return dict(id=name,**{'from':begin or name+'0','to':end or name+'1'},
            points=[dict(x=a[0],y=a[1]),dict(x=b[0],y=b[1])],length_m=math.dist(a,b),
            gait='flat',gait_code=1,sources=[name])
    old=dict(nodes=[],roads=[road('A',(-5,0),(0,0),end='shared'),road('B',(0,0),(5,0),begin='shared')])
    new=dict(nodes=[],roads=[road('C',(0,1),(0,5)),road('D',(0,-1),(0,-5))])
    value=preview(old,new,1.1)
    decisions={c['id']:('connect' if (c['a'],c['b']) in [('A','C'),('B','D')] else 'separate') for c in value['candidates']}
    merged=apply(value,decisions,{})
    assert plan_route(merged,dict(x=0,y=5),dict(x=0,y=-5))['length_m']==pytest.approx(10)
    assert len({n['id'] for n in merged['nodes']})==len(merged['nodes'])


@pytest.mark.parametrize('latitude,longitude', [(30.2241,120.2598),(39.9042,116.4074)])
def test_new_site_uses_first_usable_fix_and_plans_both_directions(mapping,latitude,longitude):
    from outdoor_mapping.coordinates import local_radii
    import math
    east_radius,_=local_radii(latitude)
    def position(x,quality=4):
        mapping.clock[0]+=1
        mapping.observe('rtk_status',dict(receiver_quality=quality,fix_age_s=0.,measurement_age_s=0.),'status')
        mapping.observe('gait',{'code':0x1001},'feedback')
        mapping.observe('fix',dict(latitude=latitude,longitude=longitude+math.degrees(x/east_radius)),'fix')
    position(-1,quality=1)
    mapping.capture({'action':'start','new_map':True})
    assert mapping.recorder.points==[]
    for x in range(10):
        position(x)
    first=finish(mapping,'local-v1')
    assert first['origin_wgs84']==dict(latitude=latitude,longitude=longitude)
    road=first['network']['roads'][0]
    assert road['points'][0]['x']==pytest.approx(0.)
    assert road['length_m']==pytest.approx(9.,abs=.01)
    payload=dict(map_id=first['map_id'],version=first['version'],start=road['points'][0],goal=road['points'][-1])
    forward=mapping.plan_route(payload)
    reverse=mapping.plan_route({**payload,'start':payload['goal'],'goal':payload['start']})
    assert forward['length_m']==pytest.approx(reverse['length_m'])
    position(5)
    mapping.capture({'action':'start'})
    position(6)
    position(7)
    mapping.capture({'action':'stop'})
    assert mapping.draft['origin_wgs84']==first['origin_wgs84']


@pytest.mark.parametrize('pause_first',[False,True])
def test_cancel_keeps_raw_and_allows_new_recording(mapping,pause_first):
    feed(mapping,0)
    mapping.capture({'action':'start'})
    feed(mapping,1)
    sid=mapping.recorder.session['session_id']
    raw=mapping.store.recordings/sid/'events.jsonl'
    original=raw.read_bytes()
    if pause_first:
        mapping.capture({'action':'pause'})
    result=mapping.capture({'action':'cancel'})
    assert result['session']['state']=='interrupted'
    assert mapping.recorder.stream is None
    assert mapping.draft is None and mapping.merge is None
    assert raw.read_bytes()==original
    mapping.capture({'action':'start'})
    assert mapping.recorder.session['session_id']!=sid
    mapping.capture({'action':'cancel'})
