"""Build and explicitly join measured route polylines."""
import copy
import hashlib
import json

from .coordinates import enu
from .track import distance, filter_track, projection, resample, simplify


def version(network):
    return hashlib.sha256(json.dumps(network, sort_keys=True, allow_nan=False).encode()).hexdigest()[:16]


def point(value):
    return {k: value[k] for k in ("x", "y")}


def build(points, session_id, origin=None):
    if not points:
        raise ValueError("Recording has no usable GNSS position")
    origin = origin or {k: points[0][k] for k in ("latitude", "longitude")}
    metric = [{**p, **dict(zip(("x", "y"), enu(p["latitude"], p["longitude"], origin)))} for p in points]
    groups = []
    for p in metric:
        key = (p["segment"], p["quality"], p["gait_code"])
        if not groups or groups[-1][0] != key:
            previous = groups[-1][1][-1] if groups else None
            prefix = [previous] if previous and previous["segment"] == p["segment"] else []
            groups.append((key, prefix))
        groups[-1][1].append(p)
    network = dict(nodes=[], roads=[])
    reports, filtered_tracks = [], []
    last_endpoint = None
    last_segment = None
    for _, group in groups:
        # Filtering cannot erase a gait or quality boundary: groups are processed separately.
        if len(group) < 2:
            continue
        chunks, report = filter_track(group)
        reports.append(report)
        for chunk_index, chunk in enumerate(chunks):
            if chunk_index:
                last_endpoint = None
            if len(chunk) < 2:
                last_endpoint = None
                continue
            geometry = [point(p) for p in simplify(resample(chunk))]
            if sum(distance(a, b) for a, b in zip(geometry, geometry[1:])) < .05:
                continue
            meta = group[-1]
            begin = last_endpoint if (last_endpoint and last_segment == meta["segment"]
                                      and distance(last_endpoint, geometry[0]) < 1e-6) else None
            if begin is None:
                begin = dict(id=f"{session_id}-n{len(network['nodes'])}", **geometry[0])
                network["nodes"].append(begin)
            end = dict(id=f"{session_id}-n{len(network['nodes'])}", **geometry[-1])
            network["nodes"].append(end)
            network["roads"].append(dict(id=f"{session_id}-r{len(network['roads'])}",
                name="", **{"from": begin["id"], "to": end["id"]}, points=geometry,
                length_m=sum(distance(a, b) for a, b in zip(geometry, geometry[1:])),
                gait=meta["gait"], gait_code=meta["gait_code"], grade=meta["grade"],
                quality=meta["quality"], low_accuracy=meta["quality"] == 2,
                sources=[session_id], bidirectional=True))
            filtered_tracks.append([dict(p) for p in chunk])
            last_endpoint, last_segment = end, meta["segment"]
    if not network["roads"]:
        raise ValueError("No continuous moving trajectory; raw recording is retained")
    return dict(origin_wgs84=origin, network=network, filtered_tracks=filtered_tracks,
                report={"chunks": reports, "absolute_accuracy_verified": False,
                        "unassociated_gait_events": "See original gait events; no position was invented"})


def _intersection(a, b, c, d):
    x, y, u, v = b["x"]-a["x"], b["y"]-a["y"], d["x"]-c["x"], d["y"]-c["y"]
    det = x*v-y*u
    if abs(det) < 1e-9:
        return None
    cx, cy = c["x"]-a["x"], c["y"]-a["y"]
    t, s = (cx*v-cy*u)/det, (cx*y-cy*x)/det
    if 0 <= t <= 1 and 0 <= s <= 1:
        return dict(x=a["x"]+t*x, y=a["y"]+t*y)
    return None


def preview(base, addition, radius):
    network = copy.deepcopy(base or {"nodes": [], "roads": []})
    network["nodes"].extend(copy.deepcopy(addition["nodes"]))
    network["roads"].extend(copy.deepcopy(addition["roads"]))
    segments = sum(len(r["points"])-1 for r in network["roads"])
    if segments*segments > 4_000_000:
        raise ValueError("Merge exceeds 2 million segment comparisons; split the survey into smaller maps")
    candidates, conflicts, seen = [], [], set()
    for road in network["roads"]:
        if road["length_m"] > radius*2 and distance(road["points"][0], road["points"][-1]) <= radius:
            candidates.append(dict(id=f"join-{len(candidates)}", a=road["id"], b=road["id"],
                offset_a=0., offset_b=road["length_m"], point_a=road["points"][0],
                point_b=road["points"][-1], distance_m=distance(road["points"][0], road["points"][-1])))
        offsets = [0.]
        for a,b in zip(road["points"], road["points"][1:]):
            offsets.append(offsets[-1]+distance(a,b))
        for i,(a,b) in enumerate(zip(road["points"], road["points"][1:])):
            for j in range(i+2,len(road["points"])-1):
                c,d = road["points"][j:j+2]
                p = _intersection(a,b,c,d)
                if p is None:
                    continue
                oa,ob = offsets[i]+distance(a,p), offsets[j]+distance(c,p)
                if ob-oa < radius*2:
                    continue
                key = (road["id"], round(oa,1), round(ob,1))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(dict(id=f"join-{len(candidates)}", a=road["id"], b=road["id"],
                    offset_a=oa,offset_b=ob,point_a=p,point_b=p,distance_m=0.))
    for i, a in enumerate(network["roads"]):
        for b in network["roads"][i+1:]:
            if set((a["from"], a["to"])) & set((b["from"], b["to"])):
                continue
            positions = [a["points"][0], a["points"][-1], b["points"][0], b["points"][-1]]
            positions.extend(p for x, y in zip(a["points"], a["points"][1:])
                             for u, v in zip(b["points"], b["points"][1:])
                             if (p := _intersection(x, y, u, v)) is not None)
            for p in positions:
                pa, pb = projection(p, a["points"]), projection(p, b["points"])
                if max(pa["distance_m"], pb["distance_m"]) > radius:
                    continue
                key = (a["id"], b["id"], round(pa["offset_m"], 1), round(pb["offset_m"], 1))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(dict(id=f"join-{len(candidates)}", a=a["id"], b=b["id"],
                    offset_a=pa["offset_m"], offset_b=pb["offset_m"],
                    point_a=pa["point"], point_b=pb["point"], distance_m=distance(pa["point"], pb["point"])))
            # Overlap requires every point in the new fragment to lie along the old road.
            offsets_b = [projection(p, a["points"]) for p in b["points"]]
            lower, upper = min(p["offset_m"] for p in offsets_b), max(p["offset_m"] for p in offsets_b)
            old_between, station = [], 0.
            for j, p in enumerate(a["points"]):
                if j:
                    station += distance(a["points"][j-1], p)
                if lower <= station <= upper:
                    old_between.append(p)
            if (base and a in base["roads"] and upper-lower > .05
                    and abs((upper-lower)-b["length_m"]) < .3
                    and all(p["distance_m"] < .3 for p in offsets_b)
                    and all(projection(p,b["points"])["distance_m"] < .3 for p in old_between)):
                conflicts.append(dict(id=f"overlap-{len(conflicts)}", old_road=a["id"],
                                      new_road=b["id"], gait_conflict=a["gait_code"] != b["gait_code"],
                                      start_m=lower, end_m=upper))
    return dict(network=network, candidates=candidates, conflicts=conflicts)


def _split(network, original_id, offset, location, node_id):
    roads = [r for r in network["roads"] if r.get("original_id", r["id"]) == original_id]
    for road in roads:
        start = road.get("original_offset", 0.)
        end = road.get("original_end", start+road["length_m"])
        local = offset-start
        if offset < start-1e-6 or offset > end+1e-6:
            continue
        if local <= 1e-6 or offset >= end-1e-6:
            endpoint = "from" if local <= 1e-6 else "to"
            old = road[endpoint]
            for r in network["roads"]:
                for k in ("from", "to"):
                    if r[k] == old:
                        r[k] = node_id
                        r["points"][0 if k == "from" else -1] = dict(location)
                        r["length_m"] = sum(distance(a,b) for a,b in zip(r["points"],r["points"][1:]))
            return
        walked = 0.
        for j, (a, b) in enumerate(zip(road["points"], road["points"][1:])):
            length = distance(a, b)
            if walked + length >= local - 1e-8:
                break
            walked += length
        pieces = [road["points"][:j+1]+[location], [location]+road["points"][j+1:]]
        network["roads"].remove(road)
        for k, pts in enumerate(pieces):
            fragment = {**road, "id": road["id"]+f".{k}", "points": pts,
                        "original_id": original_id, "original_offset": start+(local if k else 0),
                        "original_end": end if k else offset,
                        "length_m": sum(distance(a,b) for a,b in zip(pts,pts[1:]))}
            fragment["from" if k else "to"] = node_id
            network["roads"].append(fragment)
        return
    raise ValueError("Connection no longer matches the preview")


def apply(preview_value, decisions, choices):
    expected = {c["id"] for c in preview_value["candidates"]}
    if set(decisions) != expected or any(v not in ("connect", "separate") for v in decisions.values()):
        raise ValueError("Review every connection: connect or separate")
    conflicts = preview_value["conflicts"]
    if set(choices) != {c["id"] for c in conflicts} or any(v not in ("old", "new") for v in choices.values()):
        raise ValueError("Review every overlap: old or new gait record")
    result = copy.deepcopy(preview_value["network"])
    for road in result["roads"]:
        road.update(original_id=road["id"], original_offset=0., original_end=road["length_m"])
    prefix = preview_value.get("preview_id") or version(result)
    originals = {r["id"]: r for r in preview_value["network"]["roads"]}
    cuts = {rid: {} for rid in originals}

    def at(road, offset):
        walked = 0.
        for a, b in zip(road["points"], road["points"][1:]):
            length = distance(a, b)
            if walked + length >= offset - 1e-8:
                ratio = min(1., max(0., (offset-walked)/length)) if length else 0.
                return {k: a[k]+ratio*(b[k]-a[k]) for k in ("x", "y")}
            walked += length
        return dict(road["points"][-1])

    endpoint_cuts = {}

    def add_cut(rid, offset):
        # Reuse one station identity even when multiple joins name the same endpoint.
        key = round(offset, 7)
        if key not in cuts[rid]:
            road = originals[rid]
            endpoint = road["from"] if offset <= 1e-6 else road["to"] if offset >= road["length_m"]-1e-6 else None
            node_id = f"cut-{prefix}-{rid}-{len(cuts[rid])}"
            cuts[rid][key] = endpoint_cuts.setdefault(endpoint, node_id) if endpoint else node_id
        return cuts[rid][key]

    for c in preview_value["candidates"]:
        if decisions[c["id"]] == "connect":
            for key in ("a", "b"):
                add_cut(c[key], c["offset_"+key])
    for c in conflicts:
        for offset in (c["start_m"], c["end_m"]):
            add_cut(c["old_road"], offset)
    # Cut at original stations before moving any junction endpoint.
    for rid, stations in cuts.items():
        for offset, nid in sorted(stations.items()):
            location = at(originals[rid], offset)
            result["nodes"].append(dict(id=nid, **location))
            _split(result, rid, offset, location, nid)

    def join(old_id, new_id, location):
        for road in result["roads"]:
            for key in ("from", "to"):
                if road[key] == old_id:
                    road[key] = new_id
                    road["points"][0 if key == "from" else -1] = dict(location)
        for stations in cuts.values():
            for offset in stations:
                if stations[offset] == old_id:
                    stations[offset] = new_id

    for c in preview_value["candidates"]:
        if decisions[c["id"]] != "connect":
            continue
        na = cuts[c["a"]][round(c["offset_a"],7)]
        nb = cuts[c["b"]][round(c["offset_b"],7)]
        join(nb, na, c["point_a"])
    removed = set()
    for conflict in conflicts:
        old, new = originals[conflict["old_road"]], originals[conflict["new_road"]]
        selected = new if choices[conflict["id"]] == "new" else old
        overlapping = []
        for road in result["roads"]:
            if road["original_id"] != old["id"]:
                continue
            midpoint = (road["original_offset"]+road["original_end"])/2
            if conflict["start_m"]-1e-6 <= midpoint <= conflict["end_m"]+1e-6:
                road["gait"], road["gait_code"] = selected["gait"], selected["gait_code"]
                road["sources"] = list(dict.fromkeys(road["sources"]+new["sources"]))
                overlapping.append(road)
        old_ends = {r[k] for r in overlapping for k in ("from", "to")}
        new_fragments = [r for r in result["roads"] if r["original_id"] == new["id"]]
        # A separate decision must preserve the independently recorded road.
        if new_fragments and all(r[k] in old_ends for r in new_fragments for k in ("from", "to")):
            removed.add(new["id"])
        else:
            for road in new_fragments:
                road["gait"], road["gait_code"] = selected["gait"], selected["gait_code"]
                road["sources"] = list(dict.fromkeys(road["sources"]+old["sources"]))
                stations = [projection(p, old["points"])["offset_m"] for p in road["points"]]
                low, high = min(stations), max(stations)
                interior, offset = [], 0.
                for j, p in enumerate(old["points"]):
                    if j:
                        offset += distance(old["points"][j-1],p)
                    if low < offset < high:
                        interior.append(dict(p))
                pts = [at(old,low)] + interior + [at(old,high)]
                road["points"] = pts if stations[-1] >= stations[0] else list(reversed(pts))
                for key in ("from", "to"):
                    join(road[key], road[key], road["points"][0 if key == "from" else -1])
    result["roads"] = [r for r in result["roads"] if r["original_id"] not in removed]
    for road in result["roads"]:
        road["length_m"] = sum(distance(a,b) for a,b in zip(road["points"],road["points"][1:]))
    used = {r[k] for r in result["roads"] for k in ("from", "to")}
    endpoint_positions = {r[k]: r["points"][0 if k == "from" else -1]
                          for r in result["roads"] for k in ("from", "to")}
    result["nodes"] = [dict(id=nid, **endpoint_positions[nid]) for nid in sorted(used)]
    for road in result["roads"]:
        for key in ("original_id", "original_offset", "original_end"):
            road.pop(key, None)
    return result
