import json
import time
from unittest.mock import Mock
import pytest
from outdoor_mapping.core import Mapping
from outdoor_mapping.locomotion import profile, feedback_value
from outdoor_mapping.ros_input import Inputs
from test_core import feed, finish


@pytest.fixture
def clock(monkeypatch):
    value = [100.]
    monkeypatch.setattr(time, 'monotonic', lambda: value[0])
    return value


def recorded(path, clock, mode='legged', codes=None, code=7):
    m = Mapping(path, dict(locomotion_mode=mode, gait_code_map=codes))
    m.clock = clock
    feed(m, 0, gait=code)
    m.capture({'action': 'start'})
    for i in range(1, 8): feed(m, i, gait=code)
    return m


def test_custom_mapping_saved_without_rewriting_raw_codes(tmp_path, clock):
    m = recorded(tmp_path, clock, codes={'7': 'stairs', '0x9': 'flat'})
    value = finish(m, 'custom')
    assert value['gait_code_map'] == {'7': 'stairs', '9': 'flat'}
    assert all(r['gait'] == 'stairs' and r['gait_code'] == 7 for r in value['network']['roads'])
    feed(m, 9, gait=88)
    assert m.status()['gait'] == 'unknown'
    m.close()


@pytest.mark.parametrize('mode,codes', [('wheeled', None), ('legged', {'7': 'stairs'})])
def test_recovery_retains_original_profile(tmp_path, clock, mode, codes):
    m = recorded(tmp_path, clock, mode, codes)
    sid = m.recorder.session['session_id']
    m.capture({'action': 'cancel'}); m.close()
    restarted = Mapping(tmp_path, {'locomotion_mode': 'legged'})
    restarted.capture({'action': 'recover', 'session_id': sid})
    assert restarted.draft['locomotion_mode'] == mode
    assert restarted.draft['gait_code_map'] == (codes or {})
    restarted.close()


def test_legacy_recovery_requires_explicit_mode(tmp_path, clock):
    m = recorded(tmp_path, clock, 'wheeled')
    sid = m.recorder.session['session_id']; m.capture({'action': 'cancel'}); m.close()
    path = tmp_path / 'recordings' / sid / 'session.json'
    session = json.loads(path.read_text()); session.pop('locomotion_mode'); session.pop('gait_code_map')
    path.write_text(json.dumps(session))
    m = Mapping(tmp_path)
    with pytest.raises(ValueError, match='legacy_locomotion_mode'):
        m.capture({'action': 'recover', 'session_id': sid})
    assert m.draft is None
    m.capture({'action': 'recover', 'session_id': sid, 'legacy_locomotion_mode': 'wheeled'})
    assert m.draft['locomotion_mode'] == 'wheeled'
    m.close()


@pytest.mark.parametrize('cfg', [{'locomotion_mode': 'wheeled'},
                                {'locomotion_mode': 'legged', 'gait_code_map': {'7': 'flat'}}])
def test_incompatible_append_and_plan_rejected_but_view_allowed(tmp_path, clock, cfg):
    m = recorded(tmp_path, clock, codes={'7': 'stairs'})
    value = finish(m, 'stairs'); m.close()
    m = Mapping(tmp_path, cfg); m.clock = clock; m.load_map('stairs')
    feed(m, 8, gait=7)
    with pytest.raises(ValueError, match='Incompatible'):
        m.capture({'action': 'start'})
    assert m.recorder.stream is None
    roads = value['network']['roads']
    with pytest.raises(ValueError, match='Incompatible'):
        m.plan_route(dict(map_id='stairs', version=value['version'], start=roads[0]['points'][0], goal=roads[-1]['points'][-1]))
    assert m.get_map()['network'] == value['network']
    m.capture({'action': 'start', 'new_map': True})
    assert m.recorder.session['parent_map_id'] is None
    m.close()


def test_wheels_ignore_all_gait_configuration_and_observations(tmp_path, clock):
    m = Mapping(tmp_path, {'locomotion_mode': 'wheeled', 'gait_code_map': 'invalid-but-unused'})
    inputs = Inputs(None, m, {'locomotion_mode': 'wheeled', 'gait': {'contract': 'missing', 'msg_type': 'missing/msg/Type'}})
    inputs.endpoint = Mock(side_effect=AssertionError('Must not discover gait'))
    assert inputs.gait_binding() == ({}, None)
    m.observe('gait', {'code': 0x1003}, 'unwanted')
    assert 'gait' not in m.recorder.latest
    m.close()


@pytest.mark.parametrize('codes', [{True: 'flat'}, {'1': 'fly'}, {'1': 'flat', '0x1': 'stairs'}, {}, {'-1': 'flat'}])
def test_bad_code_mapping_rejected(codes):
    with pytest.raises(ValueError): profile('legged', codes)


def test_feedback_adapter_dotted_field():
    assert feedback_value({'state': {'mode': 7}}, 'state.mode') == 7
    with pytest.raises(ValueError): feedback_value({'mode': 7.5}, 'mode')
