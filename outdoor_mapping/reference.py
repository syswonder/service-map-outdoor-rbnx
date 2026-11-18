"""Recorded point semantics, independent of the robot's antenna installation."""
import math


def reference(value):
    kind = value.get('reference_point', 'gnss_antenna')
    if kind not in ('gnss_antenna', 'base_link'):
        raise ValueError('reference_point must be gnss_antenna or base_link')
    return kind


def require_same(a, b):
    if reference(a) != reference(b):
        raise ValueError('Cannot mix antenna and base_link recordings/maps; re-record or explicitly migrate with historical attitude')
    if reference(a) == 'base_link' and a.get('origin_wgs84') != b.get('origin_wgs84'):
        raise ValueError('base_link recordings require the same explicit origin_wgs84')


def body_sample(message, origin, timeout):
    ref = message.get('coordinate_reference') or {}
    pose = message.get('pose') or {}
    # Route capture needs a trustworthy pose; navigation also qualifies motion feedback.
    pose_ready = message.get('capture_ready')
    if 'capture_ready' not in message:
        pose_ready = message.get('ready') is True or (
            message.get('active') is True
            and message.get('ownership_conflict') is False
            and message.get('estimate_valid') is True
            and (message.get('heading') or {}).get('ready') is True
            and (message.get('extrinsics') or {}).get('ready') is True
        )
    age, stamp = message.get('pose_age_s'), message.get('pose_stamp_s')
    finite = lambda v: type(v) in (int, float) and math.isfinite(v)
    if (pose_ready is not True or message.get('grade') not in ('fixed','float')
            or ref.get('reference_point') != 'base_link' or ref.get('frame_id') != 'map'
            or ref.get('axes') != 'east_north' or ref.get('origin_wgs84') != origin
            or ref.get('map_offset_xy_m') != [0.,0.]
            or not all(finite(pose.get(k)) for k in ('x','y','yaw'))
            or not finite(age) or not 0 <= age <= timeout or not finite(stamp) or stamp <= 0):
        return None
    return dict(x=pose['x'], y=pose['y'], yaw=pose['yaw'], pose_stamp_s=stamp,
                reference_point='base_link', origin_wgs84=dict(origin),
                source_reference=dict(ref), quality=4 if message['grade']=='fixed' else 5,
                grade=message['grade'])
