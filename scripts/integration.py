"""Synthetic ROS → Atlas → mapping → Scene. Never connects to a robot."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT/'rbnx-build/codegen/proto_gen', ROOT/'rbnx-build/codegen/robonix_mcp_types'):
    sys.path.insert(0, str(directory))


def fixture(control):
    from robonix_api import Primitive, Ok
    import rclpy
    from sensor_msgs.msg import NavSatFix
    from std_msgs.msg import String, UInt32
    provider = Primitive(id='synthetic_gnss', namespace='robonix/primitive/gnss', pkg_root=ROOT)
    resources = {}

    @provider.on_init
    def init(config):
        return Ok()

    @provider.on_activate
    def activate():
        import threading
        from rclpy.executors import SingleThreadedExecutor
        context = rclpy.Context()
        rclpy.init(context=context)
        node = rclpy.create_node('synthetic_survey_fixture',context=context)
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)
        fix = node.create_publisher(NavSatFix, '/outdoor_test/fix', 10)
        status = node.create_publisher(String, '/outdoor_test/status', 10)
        gait = node.create_publisher(UInt32, '/outdoor_test/actual_gait', 10)
        provider.declare_ros2_topic('robonix/primitive/gnss/fix', '/outdoor_test/fix')
        provider.declare_ros2_topic('robonix/primitive/gnss/rtk_status', '/outdoor_test/status')

        actual = [0., 0.]
        published = [0]
        def publish():
            import math
            try:
                value = json.loads(control.read_text())
            except (FileNotFoundError, ValueError):
                return
            if not published[0]:
                print("Synthetic fixture publishing ROS positions",flush=True)
            published[0] += 1
            dx, dy = value.get('x',0)-actual[0], value.get('y',0)-actual[1]
            scale = min(1., .06/max(.00001, math.hypot(dx,dy)))
            actual[0] += dx*scale
            actual[1] += dy*scale
            msg = NavSatFix(latitude=30.+actual[1]/110852., longitude=120.+actual[0]/96486., altitude=0.)
            msg.header.stamp = node.get_clock().now().to_msg()
            msg.status.status = 0
            status.publish(String(data=json.dumps(dict(receiver_quality=value.get('quality',4),fix_age_s=0.,measurement_age_s=0.))))
            gait.publish(UInt32(data=value.get('gait',0x1001)))
            fix.publish(msg)
        node.create_timer(.05, publish)
        thread = threading.Thread(target=executor.spin,daemon=True)
        thread.start()
        resources.update(node=node,thread=thread,executor=executor,context=context)
        return Ok()

    @provider.on_shutdown
    def close():
        if resources:
            resources['executor'].shutdown(timeout_sec=2)
            resources['thread'].join(2)
            resources['node'].destroy_node()
            if resources['context'].ok():
                resources['context'].shutdown()
        return Ok()
    provider.run()


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def wait_for(fn, timeout=15):
    deadline=time.monotonic()+timeout
    last=None
    while time.monotonic()<deadline:
        try:
            value=fn()
            if value:
                return value
        except Exception as error:
            last=error
        time.sleep(.1)
    raise RuntimeError(f'Timed out: {last}')


def run(args):
    import grpc
    import httpx
    output=Path(args.output).resolve()/time.strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True,exist_ok=True)
    atlas_port=port()
    web_port=args.port or port()
    env={**os.environ,'ROBONIX_ATLAS':f'127.0.0.1:{atlas_port}',
         'ROBONIX_PROVIDER_BIND_HOST':'127.0.0.1','ROBONIX_PROVIDER_ADVERTISE_HOST':'127.0.0.1',
         'ROS_DOMAIN_ID':str(args.domain),'ROS_LOCALHOST_ONLY':'1',
         'RMW_IMPLEMENTATION':'rmw_cyclonedds_cpp','CYCLONEDDS_URI':str(ROOT/'config/example-dds.xml'),
         'SCENE_PROFILE':'annotate','SCENE_WEB_VIEWER':'off','SCENE_OBJECT_MEMORY_ENABLED':'false',
         'SCENE_MAP_BINDING_WAIT_S':'0','SCENE_GRAPH_ENABLED':'false','SCENE_OBJECT_WATCHDOG':'0','SCENE_ANNOTATIONS_DIR':str(output/'annotations'),
         'SCENE_MAP_META_DIR':str(output/'meta'),'SCENE_GRAPH_CACHE_DIR':str(output/'graph'),
         'ROS_LOG_DIR':str(output/'ros_logs')}
    env['PYTHONPATH']=os.pathsep.join([str(ROOT),str(Path(args.robonix)/'pylib/robonix-api'),
        str(Path(args.robonix)/'system/scene'),str(ROOT/'rbnx-build/codegen/proto_gen'),
        str(ROOT/'rbnx-build/codegen/robonix_mcp_types'),env.get('PYTHONPATH','')])
    os.environ.update(env)
    sys.path[:0] = env["PYTHONPATH"].split(os.pathsep)
    from robonix_api import ATLAS
    import lifecycle_pb2
    import robonix_contracts_pb2_grpc as contracts
    from scene_service.map_clients import MapClients
    processes=[]
    logs=[]

    def launch(label,command,extra=None):
        log=(output/(label+'.log')).open('w')
        logs.append(log)
        processes.append(subprocess.Popen(command,env={**env,**(extra or {})},stdout=log,stderr=subprocess.STDOUT,cwd=ROOT))

    def driver(provider,command,config=None):
        caps=wait_for(lambda: ATLAS.find_capability(contract_id='robonix/lifecycle/driver',provider_id=provider,transport='grpc'))
        ref=ATLAS.connect_capability(consumer_id='integration',provider_id=provider,contract_id='robonix/lifecycle/driver',transport='grpc')
        try:
            with grpc.insecure_channel(ref.endpoint,options=[('grpc.enable_http_proxy',0)]) as channel:
                result=contracts.RobonixLifecycleDriverStub(channel).Driver(lifecycle_pb2.Driver_Request(command=command,config_json=json.dumps(config or {})),timeout=20)
                if not result.ok:
                    raise RuntimeError(f'{provider}: {result}')
        finally:
            ref.close()

    control=output/'fixture.json'
    def position(x,y=0,**kw):
        temporary=control.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(x=x,y=y,**kw)))
        temporary.replace(control)
        time.sleep(.2)

    try:
        launch('atlas',['robonix-atlas','--listen',env['ROBONIX_ATLAS'],'--capabilities',
            str(Path(args.robonix)/'capabilities')+','+str(Path(args.gnss)/'capabilities')])
        position(0)
        launch('fixture',[sys.executable,__file__,'--fixture',str(control)])
        driver('synthetic_gnss',0)
        driver('synthetic_gnss',1)
        launch('mapping',[sys.executable,'-m','outdoor_mapping.main'])
        driver('outdoor_mapping',0,dict(data_dir=str(output/'maps'),example_data=True,gnss_provider_id='synthetic_gnss',
            gait=dict(topic='/outdoor_test/actual_gait',msg_type='std_msgs/msg/UInt32',field='data')))
        driver('outdoor_mapping',1)
        launch('scene',[sys.executable,'-m','scene_service.service'])
        driver('scene',0,dict(perception={'profile':'annotate'},web_host='127.0.0.1',web_port=web_port,web_viewer='off',map_providers={'outdoor':'outdoor_mapping'}))
        driver('scene',1)
        base=f'http://127.0.0.1:{web_port}'
        client=httpx.Client(base_url=base,timeout=15,trust_env=False)
        wait_for(lambda:client.get('/api/outdoor/state').status_code==200)
        maps=MapClients({'outdoor':'outdoor_mapping'})
        try:
            wait_for(lambda:maps.outdoor('status').get('ready'), timeout=12)
        except RuntimeError:
            print(maps.outdoor('status'),flush=True)
            raise
        def post(path,body):
            response=client.post(path,json=body)
            value=response.json()
            assert response.is_success and value.get('ok'), (path,response.status_code,value)
            return value
        def finish(name):
            post('/api/outdoor/capture',dict(action='stop'))
            merge=post('/api/outdoor/preview_merge',{})
            bad=client.post('/api/outdoor/apply_merge',json={'preview_id':merge['preview_id']})
            if merge['candidates'] or merge['conflicts']:
                assert bad.status_code==409
            post('/api/outdoor/apply_merge',dict(preview_id=merge['preview_id'],
                decisions={c['id']:('connect' if c['distance_m']<.15 else 'separate') for c in merge['candidates']},choices={c['id']:'old' for c in merge['conflicts']}))
            post('/api/maps/save',dict(map_id=name,map_type='outdoor'))
            post('/api/maps/load',dict(map_id=name,map_type='outdoor'))
            return client.get('/api/outdoor/state').json()['map']
        post('/api/outdoor/capture',dict(action='start',new_map=True))
        for i in range(1,21):
            position(i*.2,gait=0x1003 if 7<=i<14 else 0x1001,quality=2 if i>15 else 5 if i>10 else 4)
        assert client.post('/api/maps/load',json=dict(map_id='blocked',map_type='outdoor')).status_code==409
        first=finish('synthetic-v1')
        post('/api/regions',dict(kind='poi',name='SYNTHETIC example point',points=[[1.,0.]],map_id=first['map_id'],version=first['version']))
        position(2)
        time.sleep(2)
        post('/api/outdoor/capture',dict(action='start'))
        for i in range(1,16):
            position(2,i*.2)
        second=finish('synthetic-v2')
        assert first['origin_wgs84']==second['origin_wgs84']
        state=client.get('/api/outdoor/state').json()
        assert state['is_active'] and state['annotations'][0]['name']=='SYNTHETIC example point'
        roads=second['network']['roads']
        start=first['network']['roads'][0]['points'][0]
        goal=max((p for r in roads for p in r['points']),key=lambda p:p['y'])
        payload=dict(map_id=second['map_id'],version=second['version'],start=start,goal=goal)
        forward=post('/api/outdoor/plan_route',payload)
        reverse=post('/api/outdoor/plan_route',{**payload,'start':goal,'goal':start})
        assert abs(forward['length_m']-reverse['length_m'])<1e-6
        assert forward['length_m']>4
        assert not forward['execution_enabled']
        # Only the fixture, lifecycle and TF publishers may exist in this isolated domain.
        import rclpy
        rclpy.init()
        node=rclpy.create_node('mapping_output_audit')
        topics=node.get_topic_names_and_types()
        forbidden=[name for name,types in topics if any(s in name.lower() for s in ('cmd_vel','motion_command','posture','twist_in'))]
        node.destroy_node()
        rclpy.shutdown()
        assert not forbidden,forbidden
        for path in ('/','/outdoor','/maps?map_type=outdoor','/regions?map_type=outdoor'):
            assert client.get(path).status_code==200
        report=dict(synthetic=True,robot_tested=False,checks=['shared lifecycle','Atlas GNSS discovery','ROS raw capture','gait and three GNSS qualities',
            'explicit merge','immutable save/reload','annotation inheritance','forward/reverse planning','recording load guard','no motion topics'],
            scene_url=base,map_id=second['map_id'],version=second['version'],length_m=forward['length_m'])
        (output/'result.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)
        if args.serve:
            print('SYNTHETIC DATA ONLY. Ctrl-C to stop all example processes.',flush=True)
            while True:
                time.sleep(1)
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path)
    parser.add_argument('--robonix',type=Path)
    parser.add_argument('--gnss',type=Path)
    parser.add_argument('--output',default=str(ROOT/'rbnx-build/integration'))
    parser.add_argument('--domain',type=int,default=179)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--serve',action='store_true')
    args=parser.parse_args()
    if args.fixture:
        fixture(args.fixture)
    elif args.robonix and args.gnss:
        try:
            run(args)
        except KeyboardInterrupt:
            pass
    else:
        parser.error('--robonix and --gnss are required')
