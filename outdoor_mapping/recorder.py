"""Timestamped GNSS and actual gait feedback; no hardware control."""
import json
import math
import os
import time
import uuid

from .storage import write_json
from .reference import reference, body_sample

GRADES = {2: "dgps", 4: "fixed", 5: "float"}
from .locomotion import profile, gait_label


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
        self.profile = profile(config.get("locomotion_mode", "legged"), config.get("gait_code_map"))
        self.required_inputs = ("fix", "rtk_status") if config.get("locomotion_mode") == "wheeled" else ("fix", "rtk_status", "gait")
        self.reference_point = reference(config)
        if self.reference_point == "base_link":
            self.required_inputs += ("localization",)
        self.latest = {}
        self.session = None
        self.stream = None
        self.points = []
        self.segment = 0
        self.bytes_written = 0
        self.last_sync = 0
        self.recorded_gait = None
        self.last_localization_state = None

    def fresh(self, kind, now):
        value = self.latest.get(kind)
        return value is not None and 0 <= now - value["received"] <= self.config["input_timeout_s"]

    def status(self, now=None):
        now = time.monotonic() if now is None else now
        block_reason = next((f'{role}_missing_or_stale' for role in self.required_inputs
                             if not self.fresh(role, now)), None)
        if block_reason is None and self.reference_point == 'base_link':
            message = self.latest['localization']['message']
            if self.body_sample(message, now) is None:
                block_reason = message.get('capture_block_reason') or 'base_link_pose_unavailable'
        return {"session": self.session, "inputs": {
            k: {"fresh": self.fresh(k, now),
                "age_s": max(0, now-v["received"]), "source": v["source"]}
            for k, v in self.latest.items()},
            "ready": block_reason is None, "block_reason": block_reason,
            "locomotion_mode": self.config.get("locomotion_mode", "legged"),
            "point_count": len(self.points)}

    def start(self, parent_map_id, now=None):
        if self.stream is not None:
            raise ValueError("Finish the current recording first")
        status = self.status(now)
        if not status["ready"]:
            raise ValueError("Fresh configured inputs are required (" + status["block_reason"] + ")")
        if len(list(self.store.recordings.iterdir())) >= self.config["max_recordings"]:
            raise ValueError("Recording limit reached; archive old recordings first")
        sid = uuid.uuid4().hex
        directory = self.store.recordings / sid
        directory.mkdir()
        self.session = dict(session_id=sid, state="recording", parent_map_id=parent_map_id,
                            started_at=time.time(), error=None, reference_point=self.reference_point,
                            origin_wgs84=self.config.get("origin_wgs84"), **self.profile)
        self.points, self.segment, self.bytes_written = [], 0, 0
        self.recorded_gait = None
        self.last_localization_state = None
        self.stream = (directory / "events.jsonl").open("a", encoding="utf-8")
        self._save_state()
        # Seed the recording with the actual inputs already received.
        for kind in ("rtk_status", "gait", "fix", "localization"):
            if self.stream is None:
                raise ValueError("Recording resource limit reached while preserving initial messages")
            if kind not in self.required_inputs:
                continue
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
        if kind not in ("fix", "rtk_status", "gait", "localization"):
            raise ValueError("Unknown input")
        if kind == "gait" and "gait" not in self.required_inputs:
            return
        received = time.monotonic() if received is None else received
        message = clean_json(message)
        self.latest[kind] = dict(message=message, received=received, source=source)
        if self.stream is not None:
            self._append(kind, message, received, source)

    def body_sample(self, message, received):
        sample = body_sample(message, self.config['origin_wgs84'], self.config['input_timeout_s'])
        if sample is None:
            return None
        code = self.latest.get('gait', {}).get('message', {}).get('code') if 'gait' in self.required_inputs and self.fresh('gait', received) else None
        return dict(**sample, time_s=received, utc=str(received), segment=self.segment,
                    gait_code=code, gait=gait_label(self.profile['gait_code_map'], code))

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
        code = gait.get("code") if "gait" in self.required_inputs and self.fresh("gait", received) else None
        return dict(latitude=lat, longitude=lon, altitude=message.get("altitude"),
                    quality=quality, grade=GRADES[quality], gait_code=code,
                    gait=gait_label(self.profile["gait_code_map"], code), time_s=received,
                    utc=str(received), segment=self.segment,
                    quality_association="latest_fresh_status")

    def _append(self, kind, message, received, source, initial=False):
        state = self.session["state"]
        recording = state == "recording"
        sample = None
        if kind == "localization":
            sample = self.body_sample(message, received) if recording else None
            if sample is not None and self.points and sample["pose_stamp_s"] <= self.points[-1]["pose_stamp_s"]:
                return
            health = (state, sample is not None, message.get("grade"),
                      message.get("capture_block_reason"), message.get("fault"), message.get("error"))
            if sample is None and health == self.last_localization_state:
                return
            if (sample is not None and self.points
                    and self.points[-1]["segment"] == self.segment
                    and received - self.points[-1]["time_s"] > self.config["input_timeout_s"]):
                self.segment += 1
                sample["segment"] = self.segment
            self.last_localization_state = health
            message = {key: value for key, value in message.items() if key != "raw_message"}
        event = dict(kind=kind, message=message, source=source,
                     received_monotonic=received, recorded_at=time.time(),
                     state=state, initial=initial)
        if kind == "gait":
            code = message.get("code")
            event["gait_changed"] = initial or code != self.recorded_gait
            self.recorded_gait = code
        if kind == ('localization' if self.reference_point == 'base_link' else 'fix') and recording:
            if kind == "fix":
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
        separators = (",", ":") if kind == "localization" else None
        line = json.dumps(event, ensure_ascii=False, allow_nan=False, separators=separators) + "\n"
        size = len(line.encode("utf-8"))
        byte_limit = self.bytes_written + size > self.config["max_recording_bytes"]
        if byte_limit or len(self.points) > self.config["max_points"]:
            if "sample" in event:
                self.points.pop()
            limit = "max_recording_bytes" if byte_limit else "max_points"
            self.session.update(state="interrupted", ended_at=time.time(),
                                error=f"Recording limit reached: {limit}")
            self._save_state()
            self.stream.close()
            self.stream = None
            return
        self.stream.write(line)
        self.stream.flush()
        self.bytes_written += size
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
