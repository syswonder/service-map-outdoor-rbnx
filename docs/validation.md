# 验收记录

日期：2026-11-05。环境：本机 x86_64、ROS 2 Humble、uv Python 3.10。以下数据均为合成示例，没有让机器狗自主行走或切步态。

| 检查 | 实际结果 |
|---|---|
| 建图 `scripts/build.sh` | 通过：包校验、gRPC/MCP 生成、ROS map 消息构建 |
| Scene `scripts/build_annotate.sh` | 通过：轻量原生环境、客户端生成、ROS map 消息构建 |
| 核心及 Scene 回归 | 63 项通过；仅第三方测试依赖的弃用提示 |
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

SSH 已连接并确认 `aarch64`、`/opt/ros/humble` 存在，DDS 发现 `/MOTION_INFO [drdds/msg/MotionInfo]`。普通登录环境中 `drdds` 尚未加载；默认 `localhost:50051` Atlas 查询返回连接拒绝。本次没有启动实机驱动或部署服务。

因此仍需在山猫加载实际消息工作区、启动/复用正确部署中的 Atlas 和 GNSS 原语，核对真实能力实例、步态字段及 GNSS 质量。操作员人工走一段带步态变化的路线，再补采支路，记录地图 ID/版本，检查保存、重载、区段和双向规划。步骤见 [部署说明](deployment.md)。这部分尚未完成，不能用上述合成结果替代。
