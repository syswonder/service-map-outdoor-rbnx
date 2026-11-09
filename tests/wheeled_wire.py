"""Isolated real Atlas/ROS/Driver/gRPC wheeled survey; no robot hardware."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from integration import port, wait_for


def main():
    source = Path(subprocess.check_output(["rbnx", "path", "root"], text=True).strip())
    out = ROOT / "rbnx-build/wheeled-wire" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    env = {**os.environ, "ROS_DOMAIN_ID": "223", "ROS_LOCALHOST_ONLY": "1",
           "RMW_IMPLEMENTATION": "rmw_fastrtps_cpp", "ROBONIX_ATLAS": f"127.0.0.1:{port()}",
           "ROBONIX_PROVIDER_BIND_HOST": "127.0.0.1", "ROBONIX_PROVIDER_ADVERTISE_HOST": "127.0.0.1"}
    os.environ.update(env)
    import grpc
    from robonix_api import ATLAS
    import lifecycle_pb2
    import outdoor_map_pb2
    import map_pb2
    import robonix_contracts_pb2_grpc as stubs
    processes, logs = [], []

    def launch(name, command):
        log = (out / (name + ".log")).open("w")
        logs.append(log)
        processes.append(subprocess.Popen(command, env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT))

    def rpc(provider, contract, method, request):
        wait_for(lambda: ATLAS.find_capability(contract_id=contract, provider_id=provider, transport="grpc"))
        ref = ATLAS.connect_capability(consumer_id="wheel_test", provider_id=provider, contract_id=contract, transport="grpc")
        try:
            with grpc.insecure_channel(ref.endpoint) as channel:
                stub = getattr(stubs, "".join(x.title().replace("_", "") for x in contract.split("/")) + "Stub")(channel)
                result = getattr(stub, method)(request, timeout=20)
                assert result.ok, str(result)
                return result
        finally:
            ref.close()

    def driver(provider, command, cfg=None):
        return rpc(provider, "robonix/lifecycle/driver", "Driver", lifecycle_pb2.Driver_Request(command=command, config_json=json.dumps(cfg or {})))

    def call(name, cls, payload=None):
        result = rpc("outdoor_mapping", "robonix/service/map_outdoor/" + name, cls,
                     getattr(outdoor_map_pb2, cls + "_Request")(request_json=json.dumps(payload or {})))
        return json.loads(result.result_json)

    control = out / "fixture.json"
    control.write_text(json.dumps({"x": 0}))
    try:
        gnss = ROOT.parent / "robot-agilex-ranger_mini_v3-outdoor/primitives/g70_gnss/capabilities"
        launch("atlas", ["robonix-atlas", "--listen", env["ROBONIX_ATLAS"], "--capabilities", ",".join(map(str, [source / "capabilities", ROOT / "capabilities", gnss]))])
        launch("fixture", [sys.executable, str(ROOT / "scripts/integration.py"), "--fixture", str(control)])
        driver("synthetic_gnss", 0)
        driver("synthetic_gnss", 1)
        launch("mapping", [sys.executable, "-m", "outdoor_mapping.main"])
        driver("outdoor_mapping", 0, {"data_dir": str(out / "maps"), "example_data": True,
                                      "gnss_provider_id": "synthetic_gnss", "locomotion_mode": "wheeled",
                                      "gait": {"contract": "missing/gait/provider", "msg_type": "missing/msg/Type", "code_map": "ignored"}})
        driver("outdoor_mapping", 1)
        wait_for(lambda: call("status", "Status").get("ready"))
        assert "gait" not in call("status", "Status")["inputs"]
        call("capture", "Capture", {"action": "start"})
        control.write_text(json.dumps({"x": 3}))
        time.sleep(4)
        call("capture", "Capture", {"action": "stop"})
        preview = call("preview_merge", "PreviewMerge")
        call("apply_merge", "ApplyMerge", {"preview_id": preview["preview_id"],
            "decisions": {c["id"]: "connect" for c in preview["candidates"]}, "choices": {c["id"]: "old" for c in preview["conflicts"]}})
        rpc("outdoor_mapping", "robonix/service/map/save_map", "SaveMap", map_pb2.SaveMap_Request(map_id="synthetic-wheel"))
        rpc("outdoor_mapping", "robonix/service/map/load_map", "LoadMap", map_pb2.LoadMap_Request(map_id="synthetic-wheel"))
        value = call("get_map", "GetMap")
        roads = value["network"]["roads"]
        route = call("plan_route", "PlanRoute", {"map_id": value["map_id"], "version": value["version"],
              "start": roads[0]["points"][0], "goal": roads[-1]["points"][-1]})
        assert route["reference_path"] and not route["intervals"] and not route["gait_annotations_available"]
        report = {"passed": True, "synthetic": True, "robot_tested": False,
                  "checks": ["Atlas discovery", "shared Driver init/activate", "ROS GNSS subscriptions", "no gait subscription", "capture", "save/reload", "gRPC plan_route"], "map_id": value["map_id"]}
        (out / "result.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
    finally:
        for process in reversed(processes):
            process.terminate()
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        for log in logs: log.close()


if __name__ == "__main__": main()
