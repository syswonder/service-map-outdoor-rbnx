"""Portable trajectory filtering extracted from outdoorNav."""
import math
from statistics import median

PARAMETERS = {
    "hampel_window": 11,
    "hampel_min_threshold_m": 0.5,
    "hampel_sigma": 3,
    "max_speed_m_s": 3,
    "max_gap_s": 2,
    "stationary_duration_s": 10,
    "stationary_radius_m": 0.5,
    "smoothing_weights": [1, 4, 6, 4, 1],
    "max_smoothing_displacement_m": 0.3,
    "turn_degrees": 25,
    "resample_spacing_m": 0.5,
    "preserve_resampled_turns": True,
    "simplification_tolerance_m": 0.15,
}

def distance(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def center(points):
    return {axis: median(p[axis] for p in points) for axis in ("x", "y")}


def collapse_stops(points):
    result, stops = [], []
    i = 0
    while i < len(points):
        j = i + 1
        while (
            j < len(points)
            and points[j]["time_s"] - points[i]["time_s"]
            < PARAMETERS["stationary_duration_s"]
        ):
            j += 1
        window = points[i : j + 1]
        if (
            j < len(points)
            and max(distance(p, center(window)) for p in window)
            <= PARAMETERS["stationary_radius_m"]
        ):
            anchor = center(window)
            j += 1
            while (
                j < len(points)
                and distance(points[j], anchor) <= PARAMETERS["stationary_radius_m"]
            ):
                j += 1
            group = points[i:j]
            representative = {**group[-1], **center(group), "stationary": True}
            result.append(representative)
            stops.append(
                {
                    "from_utc": group[0]["utc"],
                    "to_utc": group[-1]["utc"],
                    "count": len(group),
                    "position": center(group),
                }
            )
            i = j
        else:
            result.append(points[i])
            i += 1
    return result, stops


def smooth(points):
    result = []
    for i, point in enumerate(points):
        if i < 2 or i >= len(points) - 2 or point.get("stationary"):
            result.append(dict(point))
            continue
        a, b = points[i - 2], points[i + 2]
        u = (point["x"] - a["x"], point["y"] - a["y"])
        v = (b["x"] - point["x"], b["y"] - point["y"])
        lengths = math.hypot(*u) * math.hypot(*v)
        angle = (
            math.degrees(
                math.acos(max(-1, min(1, (u[0] * v[0] + u[1] * v[1]) / lengths)))
            )
            if lengths
            else 0
        )
        if angle >= PARAMETERS["turn_degrees"]:
            result.append(dict(point))
            continue
        candidate = {
            axis: sum(
                weight * points[i + j - 2][axis]
                for j, weight in enumerate(PARAMETERS["smoothing_weights"])
            )
            / 16
            for axis in ("x", "y")
        }
        shift = distance(candidate, point)
        ratio = (
            min(1, PARAMETERS["max_smoothing_displacement_m"] / shift) if shift else 1
        )
        result.append(
            {
                **point,
                **{
                    axis: point[axis] + ratio * (candidate[axis] - point[axis])
                    for axis in ("x", "y")
                },
            }
        )
    return result


def filter_track(points):
    rejected, usable = [], []
    half_window = PARAMETERS["hampel_window"] // 2
    max_speed = PARAMETERS["max_speed_m_s"]
    for i, point in enumerate(points):
        if point["quality"] not in (2, 4, 5):
            rejected.append(
                {
                    "index": point["index"],
                    "utc": point["utc"],
                    "reason": "unusable_position_quality",
                }
            )
            continue
        window = points[max(0, i - half_window) : i + half_window + 1]
        mid = center(window)
        residuals = [distance(p, mid) for p in window]
        mad = median(abs(r - median(residuals)) for r in residuals)
        threshold = max(
            PARAMETERS["hampel_min_threshold_m"],
            PARAMETERS["hampel_sigma"] * 1.4826 * mad,
        )
        # The bypass check prevents a real corner or a persistent shift being erased as a spike.
        spike = False
        if 0 < i < len(points) - 1:
            before, after = points[i - 1], points[i + 1]
            incoming = distance(before, point) / (point["time_s"] - before["time_s"])
            outgoing = distance(point, after) / (after["time_s"] - point["time_s"])
            bypass = distance(before, after) / (after["time_s"] - before["time_s"])
            spike = (
                distance(point, mid) > threshold
                and incoming > max_speed
                and outgoing > max_speed
                and bypass <= max_speed
            )
        if spike:
            rejected.append(
                {
                    "index": point["index"],
                    "utc": point["utc"],
                    "reason": "isolated_hampel_jump",
                }
            )
        else:
            usable.append(point)
    gaps = []
    unavailable = {p["index"] for p in points if p["quality"] not in (2, 4, 5)}

    def interrupted(a, b):
        dt = b["time_s"] - a["time_s"]
        return (
            dt > PARAMETERS["max_gap_s"]
            or distance(a, b) / dt > max_speed
            or any(a["index"] < i < b["index"] for i in unavailable)
        )

    for a, b in zip(usable, usable[1:]):
        dt = b["time_s"] - a["time_s"]
        if interrupted(a, b):
            gaps.append(
                {
                    "from_utc": a["utc"],
                    "to_utc": b["utc"],
                    "duration_s": dt,
                    "distance_m": distance(a, b),
                    "reason": "gap_or_discontinuity",
                }
            )
    chunks, chunk = [], []
    for point in usable:
        if chunk:
            dt = point["time_s"] - chunk[-1]["time_s"]
            if interrupted(chunk[-1], point):
                chunks.append(chunk)
                chunk = []
        chunk.append(point)
    if chunk:
        chunks.append(chunk)
    filtered, stops = [], []
    for chunk in chunks:
        collapsed, stationary = collapse_stops(chunk)
        stops.extend(stationary)
        filtered.append(smooth(collapsed))
    original = {p["index"]: p for p in usable}
    return filtered, {
        "rejected": rejected,
        "gaps": gaps,
        "stationary_segments": stops,
        "max_nonstationary_filter_shift_m": max(
            (
                distance(p, original[p["index"]])
                for line in filtered
                for p in line
                if not p.get("stationary")
            ),
            default=0,
        ),
        "raw_polyline_length_m": sum(
            distance(a, b) for a, b in zip(points, points[1:])
        ),
        "filtered_point_count": sum(map(len, filtered)),
    }


def projection(point, points):
    best = None
    offset = 0
    for i, (a, b) in enumerate(zip(points, points[1:])):
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        norm = dx * dx + dy * dy
        t = (
            max(
                0,
                min(
                    1, ((point["x"] - a["x"]) * dx + (point["y"] - a["y"]) * dy) / norm
                ),
            )
            if norm
            else 0
        )
        foot = {"x": a["x"] + t * dx, "y": a["y"] + t * dy}
        candidate = {
            "point": foot,
            "distance_m": distance(point, foot),
            "segment": i,
            "offset_m": offset + t * math.sqrt(norm),
        }
        if best is None or candidate["distance_m"] < best["distance_m"]:
            best = candidate
        offset += math.sqrt(norm)
    if best is None:
        raise ValueError("A polyline requires at least two points")
    return best


def resample(points, spacing=None):
    spacing = PARAMETERS["resample_spacing_m"] if spacing is None else spacing
    result = [{axis: points[0][axis] for axis in ("x", "y")}]
    next_offset, offset = spacing, 0
    for i, (a, b) in enumerate(zip(points, points[1:])):
        length = distance(a, b)
        while length and next_offset < offset + length:
            t = (next_offset - offset) / length
            result.append(
                {axis: a[axis] + t * (b[axis] - a[axis]) for axis in ("x", "y")}
            )
            next_offset += spacing
        offset += length
        if PARAMETERS["preserve_resampled_turns"] and i + 2 < len(points):
            c = points[i + 2]
            u = (b["x"] - a["x"], b["y"] - a["y"])
            v = (c["x"] - b["x"], c["y"] - b["y"])
            norm = math.hypot(*u) * math.hypot(*v)
            angle = (
                math.degrees(
                    math.acos(max(-1, min(1, (u[0] * v[0] + u[1] * v[1]) / norm)))
                )
                if norm
                else 0
            )
            if angle >= PARAMETERS["turn_degrees"] and distance(result[-1], b) > 1e-9:
                result.append({axis: b[axis] for axis in ("x", "y")})
    end = {axis: points[-1][axis] for axis in ("x", "y")}
    if distance(result[-1], end) > 1e-9:
        result.append(end)
    return result


def simplify(points, tolerance=None):
    tolerance = (
        PARAMETERS["simplification_tolerance_m"] if tolerance is None else tolerance
    )
    if len(points) <= 2:
        return points
    ranked = [
        (projection(p, [points[0], points[-1]])["distance_m"], i)
        for i, p in enumerate(points[1:-1], 1)
    ]
    deviation, index = max(ranked)
    if deviation <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[: index + 1], tolerance)[:-1] + simplify(
        points[index:], tolerance
    )


