"""Plan on confirmed survey edges and retain every gait boundary."""
import math

from .routing import plan_route, snap


def plan(value, start, goal, blocked):
    network = value["network"]
    if snap(network, goal)["distance_m"] > 2.:
        raise ValueError("Select a goal within 2 m of a measured road")
    entry = snap(network, start)
    approach_start = None
    if entry["distance_m"] > 2.:
        flat = {**network, "roads": [r for r in network["roads"]
                if r.get("gait") == "flat" and r["id"] not in blocked]}
        if not flat["roads"]:
            raise ValueError("No open flat road is available for a sensor-checked approach")
        entry = snap(flat, start)
        approach_start, start = dict(start), entry["point"]
    route = plan_route(network, start, goal, blocked)
    roads = {r["id"]: r for r in network["roads"]}
    intervals, offset = [], 0.
    for a, b, rid in zip(route["reference_path"], route["reference_path"][1:], route["segment_roads"]):
        length = math.hypot(b["x"]-a["x"], b["y"]-a["y"])
        if length < 1e-9:
            continue
        road = roads[rid]
        info = {"road_id": rid, "gait": road.get("gait", "unknown"),
                "gait_code": road.get("gait_code"), "grade": road.get("grade", "unknown"),
                "low_accuracy": road.get("low_accuracy", False), "sources": road.get("sources", [])}
        if intervals and all(intervals[-1][k] == v for k, v in info.items()):
            intervals[-1]["end_m"] = offset+length
        else:
            intervals.append({**info, "start_m": offset, "end_m": offset+length})
        offset += length
    approach = None
    if approach_start is not None:
        if intervals and intervals[0]["gait"] != "flat":
            raise ValueError("The approach must enter a flat road, away from stairs")
        if any(i["gait"] == "stairs" and i["start_m"] < 1. for i in intervals):
            raise ValueError("The road entry is too close to a stairs boundary")
        length = math.hypot(approach_start["x"]-route["start"]["x"],
                            approach_start["y"]-route["start"]["y"])
        approach = {"start": approach_start, "entry": route["start"], "length_m": length,
                    "requires_sensor_validation": True}
        for interval in intervals:
            interval["start_m"] += length
            interval["end_m"] += length
        approach_code = intervals[0]["gait_code"] if intervals else roads[entry["road_id"]].get("gait_code")
        intervals.insert(0, {"start_m": 0., "end_m": length, "road_id": None,
            "gait": "flat", "gait_code": approach_code, "grade": "unknown", "low_accuracy": False,
            "sources": [], "source_kind": "sensor_approach", "requires_sensor_validation": True})
        route.update(start=approach_start, reference_path=[approach_start, *route["reference_path"]],
                     segment_roads=[None, *route["segment_roads"]], length_m=route["length_m"]+length)
    if len(route["reference_path"]) > 4096:
        raise ValueError("Reference exceeds 4096 points; split the route")
    wheeled = value.get("locomotion_mode") == "wheeled"
    return {**route, "approach": approach, "quality_intervals": intervals if wheeled else [],
            "intervals": [] if wheeled else intervals,
            "gait_annotations_available": bool(intervals) and not wheeled,
            "low_accuracy": any(i["low_accuracy"] for i in intervals),
            "planner": "measured_network", "planning_policy": {"mode": "measured_network"}}
