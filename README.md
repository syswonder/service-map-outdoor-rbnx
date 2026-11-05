# 室外建图服务

人工遥控山猫采集 GNSS 路线和实际步态，在同一个 Scene 网页中建图、补采合并、标注和预览双向路线。本服务不控制行走，不发布速度或切步态指令，不使用山猫 `/ODOM`。

## 构建

需要 ROS 2 Humble、uv、colcon 和更新后的 Robonix。`rbnx path root` 必须指向含 `capabilities/service/map_outdoor` 的源码。先初始化该源码的 `common_interfaces`、`rcl_interfaces`、`unique_identifier_msgs` 子模块。

```bash
bash scripts/build.sh
```

脚本使用兼容系统 ROS 的 Python 创建 `.venv`，安装本地 Robonix API、生成 gRPC/MCP 客户端并构建 `map` ROS 消息。所有新增跨仓库契约均在 Robonix；不提交生成代码。G70 原语的已有包内能力由其自身提供给 Atlas。

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

## 山猫部署

先读 [部署步骤](docs/deployment.md)，将 [配置示例](config/deployment.example.yaml) 合入现有部署。复用已激活的 GNSS 原语，不能重新打开同一串口。没有步态能力时，显式订阅 `/MOTION_INFO`；消息类型和字段必须在山猫只读核对，示例不是硬件保证。

Scene 的轻量原生清单为 Robonix 中的 `system/scene/package_manifest.annotate-native.yaml`；它只做人工标注，无需视觉模型。室内地图服务运行时再配置 `map_providers.indoor`。室内外地图 ID 必须全局不同，Scene 会拒绝同名歧义；切换标签不会加载地图。

## 操作

1. 在“采集”检查三个输入均新鲜，再新建或补采；定位有效性另由 RTK 状态判断。支持暂停、继续；恢复另起轨迹段。
2. 人工遥控行走和切步态。“结束”后保留原始数据，再滤波生成草稿。
3. 在“合并”逐项确认连接和重叠记录。接近或交叉只提出候选，不自动连通。“保持分开”保留独立道路；重叠区域沿用旧几何，步态由所选记录决定。
4. 保存为**新 ID**，再显式加载。旧版不会改变，删除后的 ID 也不复用；补采沿用固定原点并继承地点/区域。
5. “规划”在道路上点终点，可用当前位置或手选起点，仅预览参考线，不会行走。无法到达会明确报错。

原始数据：`recordings/<session>/events.jsonl`、`session.json`。滤波轨迹及报告：同目录 `processed.json`。最终路网：`maps/<map_id>/map.json`。异常退出保留原记录；通过 `capture {"action":"recover","session_id":"..."}` 恢复完整事件，截断尾行不参与处理。

固定解、浮点解和差分解参与建图；差分路段标为低精度。无解、过期和跳点保留原记录，禁止跨缺口连路。未知/过期步态明确为 unknown。滤波不证明绝对定位精度。当前位置代表 GNSS 天线，当前不绘制朝向箭头。

大地图合并有 200 万线段比较的计算上限，超限会提示拆分采集，原数据保留。地图原点不可直接编辑；跨原点标注迁移尚未提供。

## 接口与验证

标准 `robonix/service/map` 提供保存、列表、加载、删除、模式和生命周期。室外 `robonix/service/map_outdoor` 提供 `get_map`、`capture`、`status`、`preview_merge`、`apply_merge`、`plan_route`，六个接口均支持 gRPC 和 MCP；请求为 `request_json`，响应为 `ok/detail/result_json`。

完整数据和对接示例见 [导航交接](docs/navigation-handoff.md)。本轮没有实际导航按钮、自动切步态、卫星底图、空地点选或道路编辑器。

```bash
uv pip install --python .venv/bin/python pytest
PYTHONPATH=. .venv/bin/python -m pytest -q tests
```

实际运行结果及尚待实机操作的项目见 [验收记录](docs/validation.md)。原校园数据保持原样。
