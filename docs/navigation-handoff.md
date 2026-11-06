# 室外建图与导航交接

Scene 已把建图规划接入 `service-navigation-outdoor-rbnx` 的 `follow_reference`。建图服务不发布运动指令；导航服务执行路线、确认步态、处理停车。隔离联调通过，实机上下楼梯仍待验收。

## 地图和坐标

新图以录制中的首个有效 GNSS 为原点，东向 X、北向 Y，单位米。原始经纬度保留；补采保持原点，保存独立 ID 和父版本。Scene 加载地图后，通过现有定位 Driver 同步 `origin/map_id/map_version`，导航核对实时原点、身份和参考点后放行。未加载地图时定位不发布 odom/TF。不要使用山猫 `/ODOM`。

采集位置是 GNSS 天线；当前导航部署采用零天线偏移、零地图偏移，按 `base_link` 使用，是现场演示取舍。真实安装偏差与楼梯定位误差仍需测量；不要宣称已校准到厘米。

`network.roads` 保存实际步态 `gait/gait_code`、定位 `grade/low_accuracy`、采集 `sources`。共享端点决定连通；未确认的交叉和缺口不能穿越。终点在道路 2 米内贴路。起点离道路超过 2 米时，先接回最近的开放平地路段，不设起点到道路的距离上限。接回后仍沿确认路网规划，旧校园文件不参与运行。

接回段不是采集道路，不写入地图。响应增加 `approach`（起点、入路点、长度、`requires_sensor_validation=true`），并把接回段放在 `reference_path` 开头。其区段标记 `source_kind=sensor_approach`、`sources=[]`、`grade=unknown`；`gait=flat/gait_code=12290` 是接回时的执行要求，并非测得的地形。原道路步态累计距离相应后移。接回段使用 NavFn/DWB 和实时雷达避障，不能关闭避障或直接执行预览直线；无法通行时停车报错。不能直接接入楼梯，也不能通过接回绕过路网内部的断路。

`Select a goal within 2 m...` 是建图服务的贴路规则；`No traversable path in the sensor costmap` 是执行端 NavFn 无法在传感器代价图上规划当前短路段。后者可能涉及障碍、起终点处的代价或传感器/地图状态，需结合当时的代价图确认；增加接回段不能保证它消失。

## 接口

通过 Atlas 明确选择 `outdoor_mapping`，调用 `robonix/service/map_outdoor/plan_route`（gRPC/MCP），请求 JSON：

```json
{"map_id":"beijing-field-v2","version":"实际地图版本","start":{"x":0,"y":0},"goal":{"x":20,"y":0},"blocked_road_ids":[]}
```

省略 start 使用新鲜 GNSS；示例坐标须换成实际道路点。响应 `ok/detail/result_json`，result 含 `map_id/version/origin_wgs84/frame_id/reference_path/goal/intervals/length_m/low_accuracy`。`intervals` 是本条路线的累计米数，反向时随方向重排；从楼梯中间出发时第一区段从 0 开始。

Scene 向导航传递以下字段，不直接发送整个建图响应。32 位小写十六进制 request_id 用于幂等重试；goal 必须是末点。以下仅示意完整形状：

```json
{
  "request_id":"1234567890abcdef1234567890abcdef",
  "frame_id":"map",
  "map_id":"beijing-field-v2",
  "version":"6b1e2c30f49d758a",
  "origin_wgs84":{"latitude":39.9042,"longitude":116.4074},
  "reference_path":[{"x":0,"y":0},{"x":5,"y":0},{"x":10,"y":0},{"x":20,"y":0}],
  "goal":{"x":20,"y":0},
  "localization_grade":"fixed",
  "arrival_tolerance_m":3.0,
  "intervals":[
    {"start_m":0,"end_m":5,"gait":"flat","gait_code":4097},
    {"start_m":5,"end_m":10,"gait":"stairs","gait_code":4099},
    {"start_m":10,"end_m":20,"gait":"flat","gait_code":4097}
  ]
}
```

区段可额外保留 road_id、grade、low_accuracy、sources。差分解可建图和规划，执行目前只接受 fixed/float；unknown 步态拒绝自动执行，不把缺失反馈沿用成平地。

全局参考线是 RPC 数据，不是新增 ROS 路径命令话题。导航另发布局部执行 Path 和状态；请不要绕过 follow_reference 的地图和步态校验。

## 步态执行

| 实际反馈 | 含义 | 导航目标 |
|---|---|---|
| 0x1001 / 0x3002 | 平地 | 0x3002 |
| 0x1003 / 0x3003 | 楼梯 | 0x3003 |
| 其他或过期 | 未知 | 拒绝猜测 |

记录的是 `/MOTION_INFO` 成功反馈变化，不是遥控器按钮日志。导航按原路线有序进度，在楼梯前后各扩展 1 米；缓冲重叠则合并。

切换顺序：阻断速度 → 根据新鲜实测速度确认停稳 → 每秒请求步态 → 收到新鲜目标步态反馈才继续。默认总等待 10 秒，超时失败并保持停车；Scene 显示等待或失败原因。定位/反馈中断也停车，不能在旧反馈上继续前进。

按操作人确认，所有楼梯及缓冲区段暂停局部避障与 NavFn 绕行，使用 RPP 沿参考线跟踪；回到平地恢复 DWB 避障。急停、定位和底盘保护仍生效。导航不会自动调用 posture、站起或使用模式切换；遥控器须先设置可导航状态。

到达仍要求路线剩余及直线距离均在 3 米内，释放控制器并确认停稳后才报告 succeeded。测试终点应放在楼梯后足够远处，避免 3 米容差在楼梯结束前就满足。

## 现场仍需确认

- 手动楼梯记录边界、实际定位偏差与 1 米缓冲是否足够；是否进一步要求固定解。
- 上下楼真实步态切换时长、停稳阈值及 10 秒超时是否合适。
- 零天线偏移、航向参考与现场坐标方向；迁移到北京须核对当地磁北修正配置。
- 外部 posture、人工接管及急停优先级。不要在任务执行时再发送姿态指令。
- 平地绕障大幅偏离密集参考线时的有序进度仍有已知限制；本轮不验证绕障与楼梯同时发生。
