"""Run mapping and Scene against already-running robot primitives; no motion commands."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'rbnx-build/codegen/proto_gen'), str(ROOT/'rbnx-build/codegen/robonix_mcp_types')]


def wait_for(fn, timeout=20):
    deadline = time.monotonic()+timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = fn()
            if result:
                return result
        except Exception as error:
            last = error
        time.sleep(.1)
    raise RuntimeError(f'Service startup timed out: {last}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path, help='JSON object containing mapping and scene config')
    parser.add_argument('--scene-root', required=True, type=Path)
    parser.add_argument('--run-dir', type=Path, default=ROOT/'rbnx-build/live')
    parser.add_argument('--status', action='store_true', help='Read the running map service status and exit')
    args = parser.parse_args()
    import grpc
    from robonix_api import ATLAS
    import lifecycle_pb2
    import outdoor_map_pb2
    import robonix_contracts_pb2_grpc as contracts
    config = json.loads(args.config.read_text())
    map_provider = config.get('mapping_provider', 'outdoor_mapping')
    scene_provider = config.get('scene_provider', 'scene')

    def endpoint(provider, contract):
        caps = ATLAS.find_capability(provider_id=provider, contract_id=contract, transport='grpc')
        return caps[0] if len(caps)==1 else None

    def rpc(provider, contract, stub_type, method, request):
        wait_for(lambda: endpoint(provider, contract))
        ref = ATLAS.connect_capability(consumer_id='outdoor_map_operator', provider_id=provider,
                                       contract_id=contract, transport='grpc')
        try:
            with grpc.insecure_channel(ref.endpoint, options=[('grpc.enable_http_proxy',0)]) as channel:
                return getattr(stub_type(channel),method)(request,timeout=25)
        finally:
            ref.close()

    def driver(provider, command, settings=None):
        response = rpc(provider,'robonix/lifecycle/driver',contracts.RobonixLifecycleDriverStub,'Driver',
                       lifecycle_pb2.Driver_Request(command=command,config_json=json.dumps(settings or {})))
        if not response.ok:
            raise RuntimeError(f'{provider}: {response}')

    def status():
        response = rpc(map_provider,'robonix/service/map_outdoor/status',contracts.RobonixServiceMapOutdoorStatusStub,
                       'Status',outdoor_map_pb2.Status_Request(request_json='{}'))
        if not response.ok:
            raise RuntimeError(response.detail)
        return json.loads(response.result_json)

    if args.status:
        print(json.dumps(status(),ensure_ascii=False,indent=2))
        return
    for provider in (map_provider,scene_provider):
        if endpoint(provider,'robonix/lifecycle/driver'):
            raise RuntimeError(f'{provider} is already registered; use the existing instance or stop it first')
    run_dir=args.run_dir.resolve()
    run_dir.mkdir(parents=True,exist_ok=True)
    children=[]
    logs=[]
    stop=threading.Event()
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    signal.signal(signal.SIGTERM,lambda *_:stop.set())

    def launch(provider, root, script):
        log=(run_dir/(provider+'.log')).open('a',buffering=1)
        logs.append(log)
        env={**os.environ,'RBNX_INSTANCE_NAME':provider,'RBNX_PACKAGE_ROOT':str(root)}
        children.append(subprocess.Popen(['bash',str(root/script)],cwd=root,env=env,
                                         stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT))

    try:
        launch(map_provider,ROOT,Path('scripts/start.sh'))
        driver(map_provider,0,config['mapping'])
        driver(map_provider,1)
        launch(scene_provider,args.scene_root.resolve(),Path('scripts/start_annotate.sh'))
        driver(scene_provider,0,config['scene'])
        driver(scene_provider,1)
        # Indoor GNSS may have no fix; activation requires interfaces, not a valid position.
        state=wait_for(lambda: (value if len(value.get('inputs',{}))==3 else None) if (value:=status()) else None,timeout=12)
        (run_dir/'startup-status.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
        print(json.dumps(dict(ok=True,atlas=os.environ.get('ROBONIX_ATLAS'),status=state),ensure_ascii=False),flush=True)
        print('Mapping and Scene are running; Ctrl-C stops only these two services.',flush=True)
        while not stop.wait(1):
            if any(child.poll() is not None for child in children):
                raise RuntimeError(f'A service exited; inspect {run_dir}')
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for log in logs:
            log.close()


if __name__=='__main__':
    main()
