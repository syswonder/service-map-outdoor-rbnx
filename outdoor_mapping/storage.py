"""Atomic map snapshots and append-only recording storage."""
import json
import os
import re
from pathlib import Path


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value) or value.endswith(".") or re.search(r"__s\d+$", value):
        raise ValueError("Use a map id containing 1..96 letters, digits, dots, dashes or underscores")
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class MapStore:
    def __init__(self, directory):
        self.root = Path(directory).expanduser().resolve()
        self.maps = self.root / "maps"
        self.recordings = self.root / "recordings"
        self.maps.mkdir(parents=True, exist_ok=True)
        self.recordings.mkdir(parents=True, exist_ok=True)

    def path(self, map_id):
        return self.maps / identifier(map_id) / "map.json"

    def load(self, map_id):
        value = json.loads(self.path(map_id).read_text())
        if value.get("map_id") != map_id or value.get("map_type") != "outdoor":
            raise ValueError("Map artifact identity or type is invalid")
        return value

    def save(self, value):
        destination = self.path(value["map_id"])
        if destination.exists() or (self.root / "retired" / (value["map_id"]+".json")).exists():
            raise ValueError("A saved map is immutable; choose a new map id")
        write_json(destination, value)

    def list(self):
        result = []
        for path in sorted(self.maps.glob("*/map.json")):
            try:
                value = self.load(path.parent.name)
                result.append(dict(map_id=value["map_id"], map_type="outdoor",
                                   version=value["version"], parent_map_id=value.get("parent_map_id"),
                                   recordings=value.get("recordings", []),
                                   has_spatial_artifact=True, spatial_ok=True, has_preview=True,
                                   updated=path.stat().st_mtime, artifact_size=path.stat().st_size,
                                   meta={"note": value.get("note", ""), "origin_wgs84": value["origin_wgs84"]}))
            except (ValueError, KeyError, OSError) as error:
                result.append(dict(map_id=path.parent.name, map_type="outdoor",
                                   has_spatial_artifact=True, spatial_ok=False, artifact_detail=str(error)))
        return result

    def delete(self, map_id):
        path = self.path(map_id)
        saved = self.load(map_id)
        write_json(self.root / "retired" / (map_id+".json"), dict(map_id=map_id, version=saved["version"]))
        path.unlink()
        path.parent.rmdir()

    def recover_recordings(self):
        for path in self.recordings.glob("*/session.json"):
            value = json.loads(path.read_text())
            if value["state"] in ("recording", "paused"):
                value["state"] = "interrupted"
                write_json(path, value)


def read_events(path):
    """Recover only complete events; never alter crash evidence."""
    lines = Path(path).read_bytes().splitlines(keepends=True)
    events = []
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except (ValueError, UnicodeDecodeError):
            if index == len(lines)-1 and not line.endswith(b"\n"):
                return events, "Incomplete final event retained in original events.jsonl"
            raise ValueError("Recording contains a corrupt complete event")
    return events, None
