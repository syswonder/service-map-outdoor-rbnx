# 验收记录

日期：2026-11-05。环境：本机 x86_64、ROS 2 Humble、uv Python 3.10。以下数据均为合成示例，没有让机器狗自主行走或切步态。

| 检查 | 实际结果 |
|---|---|
| 建图 `scripts/build.sh` | 通过：包校验、gRPC/MCP 生成、ROS map 消息构建 |
| Scene `scripts/build_annotate.sh` | 通过：轻量原生环境、客户端生成、ROS map 消息构建 |
| 核心及 Scene 回归 | 65 项通过；仅第三方测试依赖的弃用提示 |
| 完整链路 | 真实 Atlas、共享 Driver、合成 ROS 发布者、建图服务和 Scene 通过；不是直接调用核心类代替服务 |
| 正反向及支路规划 | 通过，合成支路参考路线约 5 米；两个方向长度一致 |
| 浏览器 | 四种操作保持视野、图层开关、标签不加载地图、室外标注页、暂停/继续、采集中禁加载、未确认合并禁提交通过 |
| 运动输出 | 独立 ROS 测试域中未发现速度、姿态或步态指令话题 |

完整链路覆盖采集、三种 GNSS 质量、实际反馈步态、补采连接确认、不可变保存、新版重载、标注继承和双向规划。测试原始记录和运行结果保留在 `rbnx-build/integration/<时间>/`。

最终独立运行记录：`20261105-134844/result.json`，合成地图 `synthetic-v2`，路线 5.0606 米。采样时序会使合成几何略有差别；测试要求可达且反向结果一致，不声称绝对精度。

本机默认 FastDDS 曾间歇出现“已发现话题但未收到消息”，仅改 UDP 仍有复现。本机示例现使用已安装的 CycloneDDS 和 `config/example-dds.xml` 回环发现，两个独立测试域完整运行通过。该 XML 只用于电脑示例；山猫继续使用现有 ROS 传输配置，其实际数据流尚待联调。

## 复现

先按 README 构建及安装测试依赖，在本仓库执行：

```bash
ROBONIX_ROOT="$(rbnx path root)"
PYTHONPATH=".:$ROBONIX_ROOT/system/scene:$(rbnx path robonix-api)" \
  .venv/bin/python -m pytest -q tests \
  "$ROBONIX_ROOT/system/scene/tests/test_outdoor.py" \
  "$ROBONIX_ROOT/system/scene/tests/test_map_epoch.py" \
  "$ROBONIX_ROOT/system/scene/tests/test_annotations.py" \
  "$ROBONIX_ROOT/system/scene/tests/test_map_meta.py" \
  "$ROBONIX_ROOT/system/scene/tests/test_map_binding.py"
```

完整服务测试使用 README 的 `scripts/integration.py` 命令，省略 `--serve` 自动退出。浏览器检查为 `tests/browser.cjs`，需要 Node.js、`@playwright/test` 和 Chromium；设置 `SCENE_URL` 指向合成示例。脚本拒绝测试真实地图。设置 `SCENE_FIXTURE_CONTROL` 为该次示例的 `fixture.json` 后，还会操作网页检查暂停/继续、采集中禁加载和未确认合并禁提交；原始记录保留，最后放弃草稿。

## 山猫只读核对及待验收项

山猫上已完成本轮真实室内运行检查，更新结论如下：

- ARM64 原生执行建图和 Scene 两个构建脚本成功；两服务均经共享 Driver 初始化/激活，接入现网 Atlas `127.0.0.1:51551`。
- ROS 域 `80`，复用运行中的 `g70_gnss` 和实际 `/MOTION_INFO`；真实消息类型及字段为 `drdds/msg/MotionInfo`、`data.gait_state.gait`。
- 室内 GNSS 质量码 `1`（单点解），实际步态码 `0`（未知）；输入均新鲜，`position_valid=false`，界面正确显示。
- 真实采集约 1.5 秒，session `87724cbb31e7415882099fd761467155`：45 条事件、30,277 字节，保留位置、RTK、步态原消息。结束采集后明确拒绝生成无有效定位的地图；服务继续运行。
- 浏览器经 SSH 转发访问真实 Scene，质量码、未知步态、无地图及采集面板正确显示，无脚本异常。
- 用户最终决定：新地图用首个有效录制 GNSS 点为原点。山猫建图配置不预设杭州坐标；定位服务以后接入导航时须读取已加载地图的原点。杭州/北京两组坐标采集、保存重载、双向规划和补采测试通过。

正式数据位于山猫 `/home/nvidia/outdoor-map-check/data`，日志及室内检查结果位于 `run`；部署目录独立于现有 `/home/nvidia/robot-outdoor-rbnx`。启动和下楼步骤见 [实机测试](field-test.md)。

仍需操作员到室外人工采集、切换步态和补采支路，检查真实区段及定位质量。自动导航尚未接到 Scene；同事执行服务尚未接收步态和地图身份。实际行走、自动切步态和避障未在本轮验证。本次没有发送运动指令。


## 页面与取消录制复核

室外侧栏的道路、标注、地图库、Camera、日志跳转保持室外标签，跳转不改变当前地图绑定；室外不显示室内 3D/2D 入口。山猫浏览器实测通过。

录制和暂停均提供“取消录制（保留原始数据）”；取消不生成草稿，保留 JSONL，并可再次开始采集。页面遇到已开始的录制自动进入采集面板。核心取消测试通过；浏览器取消按钮使用拦截的虚拟响应验证，没有再开启机器狗真实采集。此前残留 session `0dd439d0db2f4b10b804af6bf38ee85a` 已按用户要求结束，原始数据保留。

Camera 已通过 Scene 同一网址转发现有四路相机查看服务，未重复启动驱动。当前尚未取得可解码画面：前、后、右 RTSP 地址响应正常，但前路 ffprobe 在 10 秒内未完成，左路返回 404；查看服务将四路均标为不可用。不能将入口和状态接口通过等同于已验证视频实时性。支持调节更新间隔和画质，建议从 0.2 秒一张试起；不保证严格实时。

再次只读检查：`/MOTION_INFO` 三秒内收到 55 条，约 20 Hz，`data.gait_state.gait=0`，Scene 同步显示未知。用户确认机器狗趴着；未要求站起或执行步态切换，实际切换仍待人工录制验证。新地图采集期间位置圆点被地图绑定判断隐藏的问题已修复，采用独立采集位置显示，不改变当前地图和语义绑定；Scene 与建图核心 18 项测试通过，修复已部署。

用户确认遥控器有画面，设备只读状态 `DevEnable.Video=1`。直接 RTSP 检查中，前/后/右三路 DESCRIBE、SETUP、PLAY 均成功，但各读取两秒后收到的字节数分别为 0/0/32，左路 DESCRIBE 为 404；FFmpeg 前路八秒内未解出图片。故障尚未恢复，不能归因于缺少相机原语；新克隆的相机原语使用相同 RTSP 源，本身不控制机身视频开关。
