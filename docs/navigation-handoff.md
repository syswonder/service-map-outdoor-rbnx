# 室外导航执行交接

本轮建图服务已经提供路网和参考路线；自动行走、雷达避障、自动切步态由 `service-navigation-outdoor-rbnx` 实现。Scene 本轮只有“预览路线”，不会调用执行服务。

## 数据归属与坐标

建图服务保存原始采集、滤波结果和路网；Scene 保存地点、区域及对象语义。所有位置以 GNSS 天线为参考点，WGS84 经纬度经固定原点转换为东向 X、北向 Y，单位米，`frame_id="map"`。导航定位必须使用同一个原点和参考点，不能拿山猫 `/ODOM` 直接替代。

每个已保存地图有独立 `map_id`、内容哈希 `version`、`parent_map_id` 和 `origin_wgs84`。补采沿用原点；旧版不可覆盖。`generation` 单独表示地图坐标生命周期，不是内容哈希；加载固定原点路网不表示 SLAM 重定位完成。

`network.roads` 的每条道路含 `from/to/points/length_m`、`gait/gait_code`、`grade/quality/low_accuracy`、`sources`、`bidirectional=true`。道路端点是否共用节点决定连通，坐标接近不代表连通。`sources` 可追溯到采集 session；原始步态消息及变化事件在该 session 的 JSONL 中。

质量：`4=fixed`、`5=float`、`2=dgps`，差分解也允许建图和规划，但必须向操作员显示低精度区段。无解及过期位置不用于自动连路，未知步态保留为 `unknown`、原码为 `null` 或未识别值。当前没有航向估计。

## 已实现的接口

通过 Atlas **显式指定 outdoor_mapping 实例**查找 `robonix/service/map_outdoor/plan_route`，连接 gRPC 后调用生成的 `RobonixServiceMapOutdoorPlanRouteStub.PlanRoute`。请求类型 `outdoor_map_pb2.PlanRoute_Request`：

```python
request_json = json.dumps({
    "map_id": "outdoor-field-v2",
    "version": "从 get_map 读取的实际版本",
    "start": {"x": 0.0, "y": 0.0},
    "goal": {"x": 12.0, "y": 3.0},
    "blocked_road_ids": []
})
response = stub.PlanRoute(
    outdoor_map_pb2.PlanRoute_Request(request_json=request_json), timeout=10
)
if not response.ok:
    raise ValueError(response.detail)
route = json.loads(response.result_json)
```

坐标是请求形状示例，必须替换成实际道路上的坐标；起终点须在道路 2 米内，否则拒绝。省略 `start` 使用新鲜 GNSS 位置；没有位置时拒绝。

完整结果形状（以下仅为示例值）：

```json
{
  "map_id": "outdoor-field-v2",
  "version": "6b1e2c30f49d758a",
  "frame_id": "map",
  "origin_wgs84": {"latitude": 30.0, "longitude": 120.0},
  "reference_path": [{"x": 0.0, "y": 0.0}, {"x": 2.0, "y": 0.0}],
  "segment_roads": ["session-r0"],
  "length_m": 2.0,
  "intervals": [{
    "road_id": "session-r0", "start_m": 0.0, "end_m": 2.0,
    "gait": "stairs", "gait_code": 4099,
    "grade": "dgps", "low_accuracy": true, "sources": ["session"]
  }],
  "low_accuracy": true,
  "execution_enabled": false
}
```

`intervals` 按本次行进方向和累计路程排列。相邻线段可能产生多个同属性 interval，可自行合并；不可按道路原始采集方向重新排序。反向通行时区段顺序随路线反转。起点在楼梯区段中间时，第一个区段从 `start_m=0` 开始，执行前就需要确认楼梯步态。

## 与同事服务的差异

已读 `outdoor_nav/requests.py` 和 `client.py`：现有 `follow_reference` 严格限制字段，目前只接受下列请求；不能把整个规划结果直接传入：

```json
{
  "request_id": "1234567890abcdef1234567890abcdef",
  "frame_id": "map",
  "reference_path": [{"x": 0.0, "y": 0.0}, {"x": 2.0, "y": 0.0}],
  "goal": {"x": 2.0, "y": 0.0},
  "arrival_tolerance_m": 0.3,
  "localization_grade": "fixed"
}
```

调用 `ExecutionClient.follow_reference(payload)` 或其 Atlas 发现后的 gRPC 接口。`request_id` 为 32 位小写十六进制；`goal` 必须与参考线末点一致。现服务尚不接受地图 ID/版本/原点、步态区段，也不接受 `localization_grade="dgps"`。请先扩展其契约、校验及执行逻辑；不能删掉这些区段后宣称完成自动切步态。

执行端还需验证当前定位原点/地图版本，接收 `intervals`，跟踪沿路线进度，并在步态反馈确认后推进。现有导航行走及雷达避障继续复用，不在建图仓库重写。

## 步态映射

| 实际反馈原码 | 采集含义 | 导航模式目标候选 |
|---|---|---|
| `0x1001` | 手动基础/平地 | `0x3002` |
| `0x1003` | 手动楼梯 | `0x3003` |
| `0x3002` | 导航平地 | `0x3002` |
| `0x3003` | 导航楼梯 | `0x3003` |
| 其他、缺失、过期 | unknown | 不自动猜测 |

这是记录含义和候选对应，不代表已验证所有动作条件。切换必须看 `/MOTION_INFO` 的真实反馈，不能只凭发布成功。`posture` 可能重新设置平地步态；与自动步态策略存在冲突，需由执行服务统一处理。

## 待一起拍板

- 切换前是否必须停车、停稳的判定；提前多少米发起切换。
- 是否要求 GNSS 固定解才能精确切换；浮点/差分及定位中断时停车、等待还是人工接管。
- 步态反馈过期、未知或不匹配时的动作；确认超时、重试次数和失败退出。
- 反向经过楼梯是否需要其他方向约束；起点已在楼梯区段中的初始化流程。
- 人工抢占、紧急停机和 `posture` 冲突的优先级。
- GNSS 天线与机器人身体/脚落点的偏置，楼梯边界标注误差如何留出余量。

这些执行行为尚未实现。本轮验收只检查记录、区段、合并和路线预览；实机自动切换必须单独验收。
