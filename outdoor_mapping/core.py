"""Mapping operations shared by gRPC, MCP and the local example."""
import copy
import hashlib
import json
import math
import threading
import time
import uuid

from . import network
from .coordinates import enu
from .recorder import Recorder
from .locomotion import profile, stored_profile, require_compatible, gait_label
from .routing import validate_point
from .measured import plan
from .storage import MapStore, identifier, write_json, read_events

DEFAULTS = dict(input_timeout_s=2., join_radius_m=2., max_recording_bytes=64*1024*1024,
                max_points=100000, max_recordings=100, max_storage_bytes=4*1024**3)


class Mapping:
    def __init__(self, directory, config=None):
        self.config = {**DEFAULTS, **(config or {})}
        for key in DEFAULTS:
            v = self.config[key]
            if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0:
                raise ValueError(f"{key} must be a positive finite number")
        self.config.update(profile(self.config.get("locomotion_mode", "legged"), self.config.get("gait_code_map")))
        self.store = MapStore(directory)
        self.store.recover_recordings()
        self.recorder = Recorder(self.store, self.config)
        self.lock = threading.RLock()
        self.current = None
        self.draft = None
        self.merge = None
        lifecycle_file = self.store.root / "lifecycle.json"
        previous = json.loads(lifecycle_file.read_text())["generation"] if lifecycle_file.exists() else 0
        self.generation = previous + 1
        write_json(lifecycle_file, dict(generation=self.generation))
        self.on_lifecycle = None

    def _quota(self):
        if sum(p.stat().st_size for p in self.store.root.rglob("*") if p.is_file()) >= self.config["max_storage_bytes"]:
            raise ValueError("Storage limit reached; archive maps and recordings first")

    def observe(self, kind, message, source, received=None):
        with self.lock:
            self.recorder.observe(kind, message, source, received)

    def status(self):
        with self.lock:
            result = self.recorder.status()
            result.update(map_id=self.current["map_id"] if self.current else "",
                          version=self.current["version"] if self.current else "",
                          map_type="outdoor", generation=self.generation,
                          mode="mapping" if self.draft or self.recorder.stream else "localization",
                          draft_ready=self.draft is not None, merge_ready=self.merge is not None,
                          pose=None, position_reference="gnss_antenna", heading=None)
            result["gait_code"] = (self.recorder.latest.get("gait", {}).get("message", {}).get("code")
                                   if self.recorder.fresh("gait", time.monotonic()) else None)
            result["gait"] = gait_label(self.config["gait_code_map"], result["gait_code"])
            telemetry = self.recorder.latest.get("rtk_status", {}).get("message", {})
            result["receiver_quality"] = (telemetry.get("receiver_quality")
                                          if self.recorder.fresh("rtk_status", time.monotonic()) else None)
            fix = self.recorder.latest.get("fix")
            sample = (self.recorder.sample(fix["message"], time.monotonic())
                      if fix and self.recorder.fresh("fix", time.monotonic()) else None)
            result["position_valid"] = sample is not None
            result["position_grade"] = sample["grade"] if sample else "unknown"
            origin = (self.current or self.draft or {}).get("origin_wgs84") or self.config.get("origin_wgs84")
            if origin is None and self.recorder.points:
                origin = {k: self.recorder.points[0][k] for k in ("latitude", "longitude")}
            result["origin_wgs84"] = origin
            result["live_track"] = []
            if origin and self.recorder.stream:
                result["live_track"] = [dict(x=xy[0], y=xy[1], segment=p["segment"])
                    for p in self.recorder.points[-2000:]
                    for xy in [enu(p["latitude"], p["longitude"], origin)]]
            if fix and origin and self.recorder.fresh("fix", time.monotonic()):
                if sample:
                    x, y = enu(sample["latitude"], sample["longitude"], origin)
                    result["pose"] = dict(x=x, y=y, age_s=time.monotonic()-fix["received"],
                                          reference="gnss_antenna", grade=sample["grade"])
            return copy.deepcopy(result)

    def lifecycle(self):
        state = self.status()
        return {k: state[k] for k in ("map_id", "generation", "mode")}

    def _publish(self):
        if self.on_lifecycle:
            self.on_lifecycle(self.lifecycle())

    def capture(self, payload):
        with self.lock:
            action = payload.get("action")
            if action == "start":
                self._quota()
                if self.draft:
                    raise ValueError("Save or discard the existing draft before recording again")
                new_map = bool(payload.get("new_map", False))
                self._idle()
                if self.current and not new_map:
                    require_compatible(self.current, self.config, "append recording")
                result = self.recorder.start(self.current["map_id"] if self.current and not new_map else None)
                if new_map:
                    try:
                        write_json(self.store.root / "lifecycle.json", dict(generation=self.generation+1))
                    except OSError:
                        self.recorder.close()
                        raise
                    self.current = None
                    self.generation += 1
            elif action in ("pause", "resume", "stop", "cancel"):
                result = self.recorder.control(action)
                if action == "stop":
                    self._build_recording()
            elif action == "recover":
                self._idle()
                sid = identifier(payload["session_id"])
                folder = self.store.recordings / sid
                session = json.loads((folder / "session.json").read_text())
                if self.draft:
                    raise ValueError("Save or discard the existing draft before recovery")
                if "locomotion_mode" not in session:
                    if "legacy_locomotion_mode" not in payload:
                        raise ValueError("Legacy recording has no locomotion_mode; explicitly supply legacy_locomotion_mode after checking its source")
                    session.update(profile(payload["legacy_locomotion_mode"], payload.get("legacy_gait_code_map")))
                restored_profile = stored_profile(session)
                parent = session.get("parent_map_id")
                restored_parent = self.store.load(parent) if parent else None
                if restored_parent:
                    require_compatible(restored_parent, restored_profile, "recording recovery")
                events, warning = read_events(folder / "events.jsonl")
                self.recorder.points = [e["sample"] for e in events if e.get("sample")]
                session["recovery_warning"] = warning
                self.recorder.session = session
                parent = session.get("parent_map_id")
                self.current = restored_parent
                self._build_recording()
                result = self.recorder.status()
            elif action == "discard":
                self._idle()
                self.draft = self.merge = None
                result = self.status()
            else:
                raise ValueError("Unknown capture action")
            self._publish()
            return result

    def _build_recording(self):
        sid = self.recorder.session["session_id"]
        built = network.build(self.recorder.points, sid,
                              self.current["origin_wgs84"] if self.current else self.config.get("origin_wgs84"))
        events, warning = read_events(self.store.recordings / sid / "events.jsonl")
        associations = []
        for event in events:
            if event["kind"] != "gait" or not event.get("gait_changed", event.get("initial")):
                continue
            nearest = min((p for p in self.recorder.points
                           if p["time_s"] >= event["received_monotonic"]
                           and p["gait_code"] == event["message"].get("code")),
                          key=lambda p: p["time_s"], default=None)
            associated = (nearest is not None and event["state"] == "recording"
                          and abs(nearest["time_s"]-event["received_monotonic"]) <= self.config["input_timeout_s"])
            associations.append(dict(time_s=event["received_monotonic"], code=event["message"].get("code"),
                                     sample_index=nearest["index"] if associated else None))
        built["report"].update(gait_events=associations, recovery_warning=warning,
                              unassociated_gait_events=sum(a["sample_index"] is None for a in associations))
        self.draft = dict(**stored_profile(self.recorder.session), schema_version=1, example_data=bool(self.config.get("example_data", False)), map_type="outdoor", frame_id="map", units="meters",
                          map_id="", parent_map_id=self.current["map_id"] if self.current else None,
                          **built, recordings=list(dict.fromkeys((self.current or {}).get("recordings", [])+[sid])))
        write_json(self.store.recordings / sid / "processed.json", built)
        self.preview_merge({})

    def _idle(self):
        if self.recorder.stream is not None:
            raise ValueError("Finish the recording before changing maps")

    def get_map(self, payload=None):
        with self.lock:
            payload = payload or {}
            if payload.get("map_id"):
                result = self.store.load(payload["map_id"])
            elif payload.get("draft"):
                result = self.draft
            else:
                result = self.current
            if result is None:
                raise ValueError("No route map is loaded")
            return copy.deepcopy(result)

    def preview_merge(self, payload):
        with self.lock:
            self._idle()
            if self.draft is None:
                raise ValueError("Finish a recording first")
            if self.current:
                require_compatible(self.current, self.draft, "merge")
            base = copy.deepcopy(self.current["network"]) if self.current else None
            value = network.preview(base,self.draft["network"],self.config["join_radius_m"])
            value.update(preview_id=uuid.uuid4().hex, base_version=(self.current or {}).get("version"),
                         draft_version=network.version(self.draft["network"]))
            self.merge = value
            return copy.deepcopy(value)

    def apply_merge(self, payload):
        with self.lock:
            self._idle()
            if self.merge is None or payload.get("preview_id") != self.merge["preview_id"]:
                raise ValueError("Merge preview changed; review it again")
            if (self.current or {}).get("version") != self.merge["base_version"]:
                raise ValueError("Loaded map changed since preview")
            if self.current:
                require_compatible(self.current, self.draft, "merge")
            result = network.apply(self.merge, payload.get("decisions", {}), payload.get("choices", {}))
            self.draft["network"] = result
            content = {k:self.draft[k] for k in ("network","origin_wgs84","locomotion_mode","gait_code_map")}
            self.draft["version"] = hashlib.sha256(json.dumps(content,sort_keys=True,allow_nan=False).encode()).hexdigest()[:16]
            self.draft["merge_reviewed"] = True
            self.draft["merge_review"] = dict(preview_id=self.merge["preview_id"],
                base_version=self.merge["base_version"], decisions=copy.deepcopy(payload.get("decisions", {})),
                choices=copy.deepcopy(payload.get("choices", {})), conflicts=copy.deepcopy(self.merge["conflicts"]))
            self.merge = None
            return copy.deepcopy(self.draft)

    def save_map(self, map_id, note=""):
        with self.lock:
            self._idle()
            self._quota()
            if self.draft is None or not self.draft.get("merge_reviewed"):
                raise ValueError("Review and apply the merge before saving")
            value = copy.deepcopy(self.draft)
            value.update(map_id=identifier(map_id), note=note, saved_at=time.time())
            self.store.save(value)
            # Publishing a snapshot does not move the active map or semantic binding.
            self.draft = None
            self._publish()
            return dict(ok=True, map_id=map_id, detail="Saved immutable outdoor route map")

    def load_map(self, map_id, mode="localization", has_initial_pose=False, **unused):
        with self.lock:
            self._idle()
            if self.draft:
                raise ValueError("Save or discard the draft before loading another map")
            if has_initial_pose or mode not in ("", "localization"):
                raise ValueError("Outdoor maps load read-only; GNSS initial-pose seeding is unsupported")
            self.current = self.store.load(map_id)
            self.merge = None
            self._publish()
            return dict(ok=True, detail="Route map and fixed GNSS origin loaded; no SLAM relocalization performed")

    def delete_map(self, map_id):
        with self.lock:
            self._idle()
            if self.current and self.current["map_id"] == map_id:
                raise ValueError("Load another map before deleting the active map")
            self.store.delete(map_id)
            return dict(ok=True, detail="Deleted saved route map; source recordings retained")

    def plan_route(self, payload):
        with self.lock:
            value = self.get_map()
            require_compatible(value, self.config, "route planning")
            if payload.get("version") != value["version"] or payload.get("map_id") != value["map_id"]:
                raise ValueError("Map identity changed; refresh before planning")
            start = payload.get("start") or self.status()["pose"]
            if start is None:
                raise ValueError("Select a start or wait for a fresh GNSS position")
            start, goal = validate_point(start), validate_point(payload["goal"])
            result = plan(value,start,goal,payload.get("blocked_road_ids",[]))
            return dict(**result,map_id=value["map_id"],version=value["version"],
                        frame_id="map",origin_wgs84=value["origin_wgs84"],execution_enabled=False)

    def close(self):
        with self.lock:
            self.recorder.close()
