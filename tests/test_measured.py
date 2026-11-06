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


def test_off_route_approach_retains_stairs_stations_and_map():
    value={"network":{"nodes":[],"roads":[road('a','a','b',(0,0),(0,20)),
        road('b','b','c',(0,20),(6,20),'stairs'),road('c','c','d',(6,20),(6,0))]}}
    result=plan(value,dict(x=-3.,y=0.),dict(x=6.,y=0.),[])
    assert result['length_m']==49.
    assert result['reference_path'][:2]==[dict(x=-3.,y=0.),dict(x=0.,y=0.)]
    assert result['approach']['requires_sensor_validation']
    assert result['intervals'][0]['source_kind']=='sensor_approach'
    assert result['intervals'][0]['sources']==[]
    assert [(i['start_m'],i['end_m']) for i in result['intervals'] if i['gait']=='stairs']==[(23.,29.)]
    assert len(value['network']['roads'])==3


def test_approach_has_no_distance_limit_and_goal_still_requires_a_measured_road():
    distance_m=1000.
    value={"network":{"nodes":[],"roads":[road('a','a','b',(0,0),(0,20))]}}
    result=plan(value,dict(x=-distance_m,y=0.),dict(x=0.,y=20.),[])
    assert result['approach']['length_m']==distance_m
    assert result['length_m']==distance_m+20.
    assert result['intervals'][1]['start_m']==distance_m
    with pytest.raises(ValueError,match='goal within 2 m'):
        plan(value,dict(x=0.,y=0.),dict(x=4.,y=20.),[])
    entry_goal=plan(value,dict(x=-3.,y=0.),dict(x=0.,y=0.),[])
    assert entry_goal['length_m']==3. and len(entry_goal['intervals'])==1


def test_approach_does_not_join_disconnected_roads_or_enter_stairs():
    value={"network":{"nodes":[],"roads":[road('a','a','b',(0,0),(0,20)),
        road('b','c','d',(6,0),(6,20))]}}
    with pytest.raises(ValueError,match='disconnected'):
        plan(value,dict(x=-3.,y=0.),dict(x=6.,y=20.),[])
    value['network']['roads']=[road('a','a','b',(0,0),(0,20),'stairs')]
    with pytest.raises(ValueError,match='No open flat road'):
        plan(value,dict(x=-3.,y=0.),dict(x=0.,y=20.),[])
    value['network']['roads']=[road('a','a','b',(0,0),(0,2)),road('b','b','c',(0,2),(0,5),'stairs')]
    with pytest.raises(ValueError,match='stairs boundary'):
        plan(value,dict(x=-3.,y=1.5),dict(x=0.,y=5.),[])
