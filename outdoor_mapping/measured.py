"""Plan on confirmed survey edges and retain every gait boundary."""
import math

from .routing import plan_route, snap


def plan(value, start, goal, blocked):
    network = value["network"]
    for point in (start, goal):
        if snap(network, point)["distance_m"] > 2.:
            raise ValueError("Select a point within 2 m of a measured road")
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
    if len(route["reference_path"]) > 4096:
        raise ValueError("Reference exceeds 4096 points; split the route")
    return {**route, "intervals": intervals,
            "gait_annotations_available": bool(intervals),
            "low_accuracy": any(i["low_accuracy"] for i in intervals),
            "planner": "measured_network", "planning_policy": {"mode": "measured_network"}}
