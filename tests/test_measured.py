import pytest

from outdoor_mapping.measured import plan


def road(name, begin, end, a, b, gait="flat"):
    return dict(id=name, **{"from": begin, "to": end}, points=[dict(x=a[0],y=a[1]),dict(x=b[0],y=b[1])],
                length_m=((a[0]-b[0])**2+(a[1]-b[1])**2)**.5, gait=gait,
                gait_code=0x1003 if gait=="stairs" else 0x1001, grade="fixed",sources=["test"])


def test_stairs_are_not_shortcut_and_reverse_keeps_interval_distances(tmp_path):
    value={"network":{"nodes":[],"roads":[road('a','a','b',(0,0),(0,20)),
        road('b','b','c',(0,20),(6,20),'stairs'),road('c','c','d',(6,20),(6,0))]}}
    start,goal=dict(x=0.,y=0.),dict(x=6.,y=0.)
    forward=plan(value,start,goal,[]);reverse=plan(value,goal,start,[])
    assert forward['length_m']==46.
    from outdoor_mapping.core import Mapping
    mapping=Mapping(tmp_path)
    mapping.current={**value,'map_id':'recorded','version':'a'*16,
        'origin_wgs84':{'latitude':39.9,'longitude':116.4},'legacy_bundle_sha256':'retired-import'}
    through_service=mapping.plan_route({'map_id':'recorded','version':'a'*16,'start':start,'goal':goal})
    assert through_service['length_m']==46. and through_service['planner']=='measured_network'
    assert forward['reference_path']==list(reversed(reverse['reference_path']))
    assert [(i['start_m'],i['end_m']) for i in forward['intervals'] if i['gait']=='stairs']==[(20.,26.)]
    with pytest.raises(ValueError,match='disconnected'):
        plan(value,start,goal,['b'])


def test_unconfirmed_gap_and_crossing_stay_disconnected():
    value={"network":{"nodes":[],"roads":[road('a','a','b',(0,0),(5,0)),road('b','c','d',(6,0),(11,0))]}}
    with pytest.raises(ValueError,match='disconnected'):
        plan(value,dict(x=0,y=0),dict(x=11,y=0),[])
