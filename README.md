# 室外建图服务

人工遥控机器人采集 GNSS 路线，足式底盘可同时记录实际步态。本服务只负责采集、建图和参考路线规划，不控制底盘运动。

通用底盘适配位于 `feat/configurable-locomotion-mapping` 分支。部署使用此版本时，请在 manifest 中选择该分支。

## 底盘反馈与地图兼容

部署设置 `locomotion_mode: wheeled`。仅要求新鲜 GNSS/RTK 输入，不订阅步态，
不生成山猫步态编号；地图记录 locomotion_mode，规划保留 quality_intervals，
intervals 为空且 gait_annotations_available=false。旧 legged 模式仍要求真实步态。

`legged` 的 `gait.topic` / `contract`、`msg_type`、`field` 可以替换。
通过 `gait.code_map` 指定实际反馈编号的语义，例如 `{"7": "flat", "9": "stairs"}`。
键接受十进制或 `0x` 十六进制 uint32 编号，值为 `flat/stairs/unknown`；未列出的编号为 unknown。
自定义映射整体替换默认映射。省略时保留旧山猫映射以兼容现有部署；轮式模式完全忽略 gait 配置，
不发现步态能力、不导入步态消息、不订阅或记录步态输入。步态原始编号仍保留，不能直接当作另一底盘的控制命令；导航执行端需适配自己的编号约定。

新 session.json 和 map.json 都保存 `locomotion_mode` 与规范化 `gait_code_map`。
恢复录制使用保存的模式和映射，不使用当前部署覆盖。旧录制缺失模式时拒绝猜测，需核对来源后显式传入
`capture {"action":"recover","session_id":"...","legacy_locomotion_mode":"wheeled"}`；旧足式录制可指定
`legacy_locomotion_mode: legged` 和可选 `legacy_gait_code_map`。

允许加载不同模式地图进行查看；补采和路线规划要求地图与当前部署的模式、完整编号映射一致，
合并要求父地图和录制元数据一致。不满足时明确报错，不能通过补采自动抹去楼梯信息。
跨模式请新建地图（`new_map: true`），或切换匹配的部署后操作。
旧地图缺失模式时按历史 legged/山猫映射解释，已有 wheeled 地图仍按轮式处理。
新地图版本哈希包含模式和编号映射，避免同一路网的不同语义共享版本。

### 参数配置示例

Ranger 等轮式底盘只需设置：

```yaml
locomotion_mode: wheeled
```

足式底盘按实测反馈配置，例如：

```yaml
locomotion_mode: legged
gait:
  topic: /your_chassis/status
  msg_type: your_msgs/msg/Status
  field: state.gait
  code_map:
    "7": flat
    "9": stairs
```

这只是 mapping 的反馈解释配置。自定义编号不会自动修改 navigation 或底盘驱动的控制指令映射；
足式导航执行端必须与该编号约定匹配。轮式模式不生成步态切换区段。

### 旧录制恢复示例

新版录制会自动保存模式与映射，恢复时只需 `action` 和 `session_id`。
只有旧录制缺失模式时，才需要核对原采集底盘并补充参数。恢复旧 Ranger 录制：

```json
{"action":"recover","session_id":"录制ID","legacy_locomotion_mode":"wheeled"}
```

恢复采用自定义编号的旧足式录制：

```json
{"action":"recover","session_id":"录制ID","legacy_locomotion_mode":"legged","legacy_gait_code_map":{"7":"flat","9":"stairs"}}
```

上述 JSON 作为 `capture` 能力的 `request_json` 内容传入。未指定自定义映射的旧足式录制使用历史山猫映射。
不要为了通过恢复检查而猜测底盘模式或编号含义。

## 构建

需要 ROS 2 Humble、colcon 和 Robonix；uv 可选，未安装时使用系统 ROS Python 及已有依赖。六个 map_outdoor 契约和 IDL 已按当前源码的请求/响应补齐在本包 capabilities/，无需修改共享 Robonix。先初始化该源码的 `common_interfaces`、`rcl_interfaces`、`unique_identifier_msgs` 子模块。

```bash
bash scripts/build.sh
```

脚本使用兼容系统 ROS 的 Python 创建 `.venv`，安装本地 Robonix API、生成 gRPC/MCP 客户端并构建 `map` ROS 消息。本地兼容声明位于包内 capabilities/；不提交生成代码。G70 原语的已有包内能力由其自身提供给 Atlas。

## 先在电脑上看界面

这是明确标记的**合成数据示例**，会运行真实 Atlas、合成 ROS 发布者、建图服务和现有 Scene；不连接机器人。需要 ROS 的 `rmw_cyclonedds_cpp` 包，并另装测试和 Scene 的轻量依赖：

```bash
uv pip install --python .venv/bin/python httpx scipy Pillow uvicorn 'starlette<1'
source /opt/ros/humble/setup.bash
source rbnx-build/codegen/ros2_idl/install/setup.bash
export PYTHONPATH="$(rbnx path robonix-api):${PYTHONPATH:-}"
.venv/bin/python scripts/integration.py \
  --robonix /绝对路径/robonix --gnss /绝对路径/primitive-g70-gnss-rbnx \
  --serve --port 50109
```

完成自动示例后，打开 **http://127.0.0.1:50109/outdoor**。左上角切换室内/室外；四种操作共用同一张画布。示例地图 ID 以 `synthetic-` 开头，地点名也注明示例；Ctrl-C 停止整套示例。省略 `--serve` 会完成检查后自动退出。测试专用 `config/example-dds.xml` 不用于山猫部署。日志、原始数据和 `result.json` 位于 `rbnx-build/integration/时间/`。示例使用独立 ROS 域 179 和CycloneDDS 回环传输；如有冲突用 `--domain` 更换。

## 机器人部署（含原山猫示例）

山猫部署配置及操作见机器人仓库 `outdoor/README.md`，当前验证结果见 [测试前检查](docs/field-readiness-2026-11-06.md)；到北京等场地复用见 [换场地说明](docs/new-site.md)。

先读 [部署步骤](docs/deployment.md)，将 [配置示例](config/deployment.example.yaml) 合入现有部署。复用已激活的 GNSS 原语，不能重新打开同一串口。没有步态能力时，显式订阅 `/MOTION_INFO`；消息类型和字段必须在山猫只读核对，示例不是硬件保证。

Scene 的轻量原生清单为 Robonix 中的 `system/scene/package_manifest.annotate-native.yaml`；它只做人工标注，无需视觉模型。室内地图服务运行时再配置 `map_providers.indoor`。室内外地图 ID 必须全局不同，Scene 会拒绝同名歧义；切换标签不会加载地图。

## 操作

1. 在“采集”检查所需输入均新鲜（轮式为 GNSS/RTK，足式另需实际步态），再新建或补采；定位有效性另由 RTK 状态判断。支持暂停、继续；恢复另起轨迹段。“取消录制”保留原始数据并停止，不生成地图。录制中打开网页会自动显示采集面板。
2. 人工遥控移动；足式采集按需切换步态。“结束”后保留原始数据，再滤波生成草稿。
3. 在“合并”逐项确认连接和重叠记录。接近或交叉只提出候选，不自动连通。“保持分开”保留独立道路；重叠区域沿用旧几何，步态由所选记录决定。
4. 保存为**新 ID**，再显式加载。旧版不会改变，删除后的 ID 也不复用；补采沿用固定原点并继承地点/区域。
5. “规划”在道路上点终点，可用当前位置或手选起点，仅预览参考线，不会行走。无法到达会明确报错。

原始数据：`recordings/<session>/events.jsonl`、`session.json`。滤波轨迹及报告：同目录 `processed.json`。最终路网：`maps/<map_id>/map.json`。异常退出保留原记录；通过 `capture {"action":"recover","session_id":"..."}` 恢复完整事件，截断尾行不参与处理。

固定解、浮点解和差分解参与建图；差分路段标为低精度。无解、过期和跳点保留原记录，禁止跨缺口连路。未知/过期步态明确为 unknown。滤波不证明绝对定位精度。当前位置代表 GNSS 天线，当前不绘制朝向箭头。

大地图合并有 200 万线段比较的计算上限，超限会提示拆分采集，原数据保留。新建地图自动用录制中的首个有效 GNSS 点固定原点，补采沿用旧地图原点。地图不依赖杭州或其他指定地点；网页按路线范围外扩显示全图。导航执行前，定位服务需读取所加载地图的原点。旧地图原点不可修改；跨原点标注迁移尚未提供。

## 接口与验证

标准 `robonix/service/map` 提供保存、列表、加载、删除、模式和生命周期。室外 `robonix/service/map_outdoor` 提供 `get_map`、`capture`、`status`、`preview_merge`、`apply_merge`、`plan_route`，六个接口均支持 gRPC 和 MCP；请求为 `request_json`，响应为 `ok/detail/result_json`。

完整数据和对接示例见 [导航交接](docs/navigation-handoff.md)。Scene 已接入规划、导航、取消和步态状态；执行由独立导航服务负责。卫星底图和道路编辑器尚未提供。

```bash
uv pip install --python .venv/bin/python pytest
PYTHONPATH=. .venv/bin/python -m pytest -q tests
```

实际运行结果及尚待实机操作的项目见 [验收记录](docs/validation.md)。原校园数据保持原样。

## 导航接入更新（步态切换仅适用于足式部署）

新采及补采地图沿已确认的双向路网规划，返回完整途径点和按行进方向排列的步态、质量区段。起终点须在道路 2 米内并贴路；不会跨过未确认连接，也不会用路外捷径跳过楼梯。超过 4096 个参考点时要求拆分任务。

所有地图均使用实测路网规划；已移除旧校园加权规划、道路宽度默认值和旧底图读取。运行不依赖旧 outdoorNav 工程或校园资源。

新图原点取录制中的首个有效 GNSS；补采沿用加载地图原点。Scene 加载已保存地图时，通过现有定位 Driver 更新地图 ID、版本和原点，定位状态匹配后才允许导航。部署不预载杭州图；导航传感器代价图随机器人滚动。

导航先停稳，再请求步态，确认真实反馈成功后才放行；默认 10 秒失败退出并回显 Scene。所有楼梯区段及前后 1 米缓冲暂停局部避障，回到平地后恢复。到点容差仍为 3 米。这些行为已做隔离联调，真实上下楼梯须现场验收。

路外接近保留上游的传感器验证要求，仅允许接入已标注 flat 的道路，并沿用入口道路的原始步态编号。轮式 GNSS 路网没有步态/地形标注时，不自动将未知道路认定为平地；可在已测路网上选择起点进行规划。
