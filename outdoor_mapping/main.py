"""Robonix lifecycle and generated gRPC/MCP capability bindings."""
import json
import os

from robonix_api import Deferred, Err, Ok, Service
import map_pb2
import map_mcp
import outdoor_map_pb2
import outdoor_map_mcp

from .core import Mapping
from .ros_input import Inputs

service = Service(id=os.environ.get("RBNX_INSTANCE_NAME", "outdoor_mapping"), namespace="robonix/service")
mapping = None
inputs = None
settings = {}
active = False


@service.on_init
def init(config):
    global mapping, settings
    if active:
        return Err("Deactivate before changing configuration")
    try:
        if mapping:
            mapping.close()
        settings = dict(config)
        mapping = Mapping(config.get("data_dir", "rbnx-build/data"), {**(config.get("limits") or {}), "example_data": config.get("example_data", False)})
        return Ok()
    except (OSError, ValueError) as error:
        return Err(str(error))


@service.on_activate
def activate():
    global inputs, active
    if mapping is None:
        return Err("Initialize first")
    if active:
        return Ok()
    try:
        inputs = Inputs(service, mapping, settings)
        inputs.start()
        active = True
        return Ok()
    except (ImportError, ValueError, RuntimeError) as error:
        return Deferred(str(error))


@service.on_deactivate
def deactivate():
    global inputs, active
    if inputs:
        inputs.close()
        inputs = None
    if mapping:
        mapping.close()
    active = False
    return Ok()


@service.on_shutdown
def shutdown():
    return deactivate()


def invoke(name, payload):
    if mapping is None:
        raise ValueError("Service is not initialized")
    if name == "capture" and payload.get("action") in ("start", "resume") and not active:
        raise ValueError("Activate the service before recording")
    if name == "status":
        return {**mapping.status(), "active": active, "example_data": bool(settings.get("example_data", False))}
    if name == "list_maps":
        return dict(ok=True, detail="", maps_json=json.dumps(mapping.store.list(), ensure_ascii=False))
    if name == "get_mode":
        return dict(ok=True, mode=mapping.status()["mode"], detail="")
    return getattr(mapping, name)(**payload) if name in ("save_map", "load_map", "delete_map") else getattr(mapping, name)(payload)


def bind(name, typename, namespace, messages, mcp_types):
    response_type = getattr(messages, typename+"_Response")
    request_type = getattr(mcp_types, typename+"_Request")
    mcp_response_type = getattr(mcp_types, typename+"_Response")

    def grpc_handler(req):
        try:
            if namespace.endswith("map_outdoor"):
                payload = json.loads(req.request_json or "{}")
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object")
                result = invoke(name, payload)
                return response_type(ok=True, result_json=json.dumps(result, ensure_ascii=False, allow_nan=False))
            payload = {field.name: getattr(req, field.name) for field in req.DESCRIPTOR.fields}
            return response_type(**invoke(name, payload))
        except (OSError, ValueError, KeyError, TypeError) as error:
            return response_type(ok=False, detail=str(error))

    def mcp_handler(req):
        pb_request = getattr(messages, typename+"_Request")(**req.to_dict())
        result = grpc_handler(pb_request)
        return mcp_response_type(**{field.name: getattr(result, field.name) for field in result.DESCRIPTOR.fields})
    mcp_handler.__annotations__ = {"req": request_type, "return": mcp_response_type}
    mcp_handler.__doc__ = f"{name.replace('_', ' ').capitalize()} for outdoor route mapping; never moves the robot."
    contract = namespace+"/"+name
    service.grpc(contract)(grpc_handler)
    service.mcp(contract)(mcp_handler)


for name, typename in (("save_map", "SaveMap"), ("load_map", "LoadMap"), ("list_maps", "ListMaps"),
                       ("delete_map", "DeleteMap"), ("get_mode", "GetMode")):
    bind(name, typename, "robonix/service/map", map_pb2, map_mcp)
for name, typename in (("get_map", "GetMap"), ("capture", "Capture"), ("status", "Status"),
                       ("preview_merge", "PreviewMerge"), ("apply_merge", "ApplyMerge"), ("plan_route", "PlanRoute")):
    bind(name, typename, "robonix/service/map_outdoor", outdoor_map_pb2, outdoor_map_mcp)

if __name__ == "__main__":
    service.run()
