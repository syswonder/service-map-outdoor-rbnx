"""Timestamped GNSS and actual gait feedback; no hardware control."""
import json
import math
import os
import time
import uuid

from .storage import write_json

GRADES = {2: "dgps", 4: "fixed", 5: "float"}
GAITS = {0x1001: "flat", 0x3002: "flat", 0x1003: "stairs", 0x3003: "stairs"}


def clean_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean_json(v) for v in value]
    return value


class Recorder:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.latest = {}
        self.session = None
        self.stream = None
        self.points = []
        self.segment = 0
        self.bytes_written = 0
        self.last_sync = 0
        self.recorded_gait = None

    def fresh(self, kind, now):
        value = self.latest.get(kind)
        return value is not None and 0 <= now - value["received"] <= self.config["input_timeout_s"]

    def status(self, now=None):
        now = time.monotonic() if now is None else now
        return {"session": self.session, "inputs": {
            k: {"fresh": self.fresh(k, now),
                "age_s": max(0, now-v["received"]), "source": v["source"]}
            for k, v in self.latest.items()},
            "ready": all(self.fresh(k, now) for k in ("fix", "rtk_status", "gait")),
            "point_count": len(self.points)}

    def start(self, parent_map_id, now=None):
        if self.stream is not None:
            raise ValueError("Finish the current recording first")
        if not self.status(now)["ready"]:
            raise ValueError("Fresh GNSS position, RTK status and actual gait feedback are required")
        if len(list(self.store.recordings.iterdir())) >= self.config["max_recordings"]:
            raise ValueError("Recording limit reached; archive old recordings first")
        sid = uuid.uuid4().hex
        directory = self.store.recordings / sid
        directory.mkdir()
        self.session = dict(session_id=sid, state="recording", parent_map_id=parent_map_id,
                            started_at=time.time(), error=None)
        self.points, self.segment, self.bytes_written = [], 0, 0
        self.recorded_gait = None
        self.stream = (directory / "events.jsonl").open("a", encoding="utf-8")
        self._save_state()
        # Seed the recording with the actual inputs already received.
        for kind in ("rtk_status", "gait", "fix"):
            if self.stream is None:
                raise ValueError("Recording resource limit reached while preserving initial messages")
            item = self.latest[kind]
            self._append(kind, item["message"], item["received"], item["source"], initial=True)
        if self.stream is None:
            raise ValueError("Recording resource limit reached while preserving initial messages")
        return self.status(now)

    def _save_state(self):
        write_json(self.store.recordings / self.session["session_id"] / "session.json", self.session)

    def control(self, action):
        if self.stream is None:
            raise ValueError("No recording is open")
        state = self.session["state"]
        if action == "pause" and state == "recording":
            self.session["state"] = "paused"
        elif action == "resume" and state == "paused":
            self.segment += 1
            self.session["state"] = "recording"
        elif action == "cancel":
            self.session.update(state="interrupted", ended_at=time.time(),
                                error="Recording cancelled by operator; raw data retained")
        elif action == "stop":
            self.session["state"] = "finished"
            self.session["ended_at"] = time.time()
        else:
            raise ValueError("Invalid recording transition")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self._save_state()
        if action in ("stop", "cancel"):
            self.stream.close()
            self.stream = None
        return self.status()

    def observe(self, kind, message, source, received=None):
        if kind not in ("fix", "rtk_status", "gait"):
            raise ValueError("Unknown input")
        received = time.monotonic() if received is None else received
        message = clean_json(message)
        self.latest[kind] = dict(message=message, received=received, source=source)
        if self.stream is not None:
            self._append(kind, message, received, source)

    def sample(self, message, received):
        status = self.latest.get("rtk_status", {}).get("message", {})
        gait = self.latest.get("gait", {}).get("message", {})
        quality = status.get("receiver_quality") if self.fresh("rtk_status", received) else None
        ages = (status.get("fix_age_s"), status.get("measurement_age_s"))
        valid_ages = all(isinstance(a, (int, float)) and not isinstance(a, bool)
                         and 0 <= a <= self.config["input_timeout_s"] for a in ages)
        lat, lon = message.get("latitude"), message.get("longitude")
        coordinate_ok = (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                         and -90 <= lat <= 90 and -180 <= lon <= 180)
        nav_status = message.get("status", {}).get("status", 0)
        if not valid_ages or quality not in GRADES or not coordinate_ok or nav_status < 0:
            return None
        code = gait.get("code") if self.fresh("gait", received) else None
        return dict(latitude=lat, longitude=lon, altitude=message.get("altitude"),
                    quality=quality, grade=GRADES[quality], gait_code=code,
                    gait=GAITS.get(code, "unknown"), time_s=received,
                    utc=str(received), segment=self.segment,
                    quality_association="latest_fresh_status")

    def _append(self, kind, message, received, source, initial=False):
        event = dict(kind=kind, message=message, source=source,
                     received_monotonic=received, recorded_at=time.time(),
                     state=self.session["state"], initial=initial)
        if kind == "gait":
            code = message.get("code")
            event["gait_changed"] = initial or code != self.recorded_gait
            self.recorded_gait = code
        if kind == "fix" and self.session["state"] == "recording":
            sample = self.sample(message, received)
            if sample is not None and self.points and received <= self.points[-1]["time_s"]:
                sample = None
                event["rejection"] = "duplicate_or_out_of_order_time"
            if sample is not None:
                sample["index"] = len(self.points)
                self.points.append(sample)
                event["sample"] = sample
            else:
                self.segment += 1
        line = json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
        if self.bytes_written + len(line.encode()) > self.config["max_recording_bytes"] or len(self.points) > self.config["max_points"]:
            if "sample" in event:
                self.points.pop()
            self.session.update(state="interrupted", error="Recording resource limit reached")
            self._save_state()
            self.stream.close()
            self.stream = None
            return
        self.stream.write(line)
        self.stream.flush()
        self.bytes_written += len(line.encode())
        if time.monotonic() - self.last_sync >= 1:
            os.fsync(self.stream.fileno())
            self.last_sync = time.monotonic()

    def close(self):
        if self.stream is not None:
            self.session.update(state="interrupted", error="Service stopped during recording")
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()
            self.stream = None
            self._save_state()
