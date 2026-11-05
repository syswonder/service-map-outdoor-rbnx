"""Continuous road preference with interval metadata on the resulting reference."""
import math

from .road_preference import RoadPreference
from .routing import snap


def planning_bundle(value):
    roads = value["network"]["roads"]
    points = [p for road in roads for p in road["points"]]
    grid = value.get("grid")
    if not grid:
        low = [math.floor(min(p[axis] for p in points) - 30.) for axis in ("x", "y")]
        high = [math.ceil(max(p[axis] for p in points) + 30.) for axis in ("x", "y")]
        grid = {"origin":low,"width":math.ceil((high[0]-low[0])/.1),
                "height":math.ceil((high[1]-low[1])/.1),"resolution":.1}
    widths = value.get("road_widths_m", {})
    # New surveys have no width measurement; 4 m is the existing narrow-road demo width.
    widths = {r["id"]:widths.get(r["id"],r.get("width_m",4.)) for r in roads}
    if any(type(v) not in (int,float) or not math.isfinite(v) or v <= 0 for v in widths.values()):
        raise ValueError("Road widths must be positive finite meters")
    if grid["width"]*grid["height"]*grid["resolution"]**2/.5**2 > 4_000_000:
        raise ValueError("Planning extent exceeds 4 million preference cells")
    return {"network":value["network"],"grid":grid,"road_widths_m":widths}


def plan(value, start, goal, blocked, cost=50):
    if type(cost) is not int or not 1 <= cost <= 99:
        raise ValueError("road_offroad_cost must be an integer 1..99")
    bundle=planning_bundle(value)
    planner=RoadPreference(bundle,cost,.5,value.get("keepout_polygons",[]),.6)
    result=planner.plan(start,goal,blocked)
    roads={r["id"]:r for r in value["network"]["roads"]}
    has_gait=any(r.get("gait") in ("flat","stairs") and r.get("gait_code") is not None
                 for r in roads.values())
    points=[result["reference_path"][0]]
    intervals=[];offset=0.;segments=[]
    for a,b in zip(result["reference_path"],result["reference_path"][1:]):
        length=math.hypot(b["x"]-a["x"],b["y"]-a["y"])
        count=max(1,math.ceil(length/.5))
        for i in range(1,count+1):
            point={k:a[k]+(b[k]-a[k])*i/count for k in ("x","y")}
            if i==count:point=dict(b)
            previous=points[-1];distance=math.hypot(point["x"]-previous["x"],point["y"]-previous["y"])
            if distance < 1e-9:continue
            mid={k:(point[k]+previous[k])/2 for k in ("x","y")}
            nearest=snap(value["network"],mid)
            rid=nearest["road_id"] if nearest["road_id"] not in blocked and nearest["distance_m"] <= bundle["road_widths_m"][nearest["road_id"]]/2 else "offroad"
            road=roads.get(rid,{})
            info={"road_id":rid,"gait":road.get("gait","unknown"),
                  "gait_code":road.get("gait_code"),"grade":road.get("grade","unknown"),
                  "low_accuracy":road.get("low_accuracy",False),"sources":road.get("sources",[])}
            if has_gait:
                if intervals and all(intervals[-1].get(k)==v for k,v in info.items()):
                    intervals[-1]["end_m"]=offset+distance
                else:intervals.append({**info,"start_m":offset,"end_m":offset+distance})
            points.append(point);segments.append(rid);offset+=distance
    if len(points)>4096:
        raise ValueError("Reference exceeds 4096 points; split the route")
    if len(points)==1:points.append(dict(goal));segments.append(result["start_road_id"])
    return {**result,"reference_path":points,"segment_roads":segments,"length_m":offset,
            "intervals":intervals,"low_accuracy":any(i["low_accuracy"] for i in intervals),
            "gait_annotations_available":has_gait,"planning_policy":planner.policy(),
            "road_widths_m":bundle["road_widths_m"],
            "road_width_source":"map" if value.get("road_widths_m") else "unmeasured_demo_default_4m"}
