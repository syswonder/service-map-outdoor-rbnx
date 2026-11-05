# 山猫部署与人工验收

## 上机前只读核对

```bash
ssh -p 6003 nvidia@svr.gddg08.top
rbnx caps
printenv ROS_DISTRO RMW_IMPLEMENTATION ROS_DOMAIN_ID
ros2 topic info /gps/fix
ros2 topic info /gps/rtk_status
ros2 topic type /MOTION_INFO
ros2 interface show drdds/msg/MotionInfo
ros2 topic echo /MOTION_INFO --once
```

先 source 现有 ROS 环境和自定义消息工作区。确认 G70 原语实例同时提供 `robonix/primitive/gnss/fix`、`rtk_status`，不要把话题存在误当作定位有效。检查实际反馈的步态字段，更新配置中的 `msg_type` 和 `field`，不能订阅指令话题代替反馈。

## 构建与接入

1. 将新建图仓库和修改后的 Robonix 放到山猫，更新 rbnx 指向的源码；构建输入全部随仓库交付。
2. 在建图仓库执行 `bash scripts/build.sh`。如 ROS 不在 `/opt/ros/humble`，设置 `ROS_SETUP`；自定义反馈消息设置 `GAIT_ROS_SETUP=/实际路径/install/setup.bash`。
3. 在 Scene 仓库执行 `bash scripts/build_annotate.sh`，部署选择 `package_manifest.annotate-native.yaml`。
4. 按 `config/deployment.example.yaml` 加入现有部署，填真实本地路径、GNSS 实例和数据目录。`data_dir` 必须持久化且可写；Scene 的标注在其 `rbnx-build/data`，也需保留。删除地图不删除采集原记录。
5. 保持原有 GNSS 实例运行；由已有部署管理器通过共享 Driver 的 INIT/ACTIVATE 启动建图与 Scene。不要另起一个 GNSS 驱动。

网页默认绑定山猫回环地址。电脑访问可用 SSH 转发：

```bash
ssh -p 6003 -L 50108:127.0.0.1:50108 nvidia@svr.gddg08.top
```

然后访问电脑 `http://127.0.0.1:50108/outdoor`。室内外使用同一个 Scene 端口。若已运行 Scene，修改该实例配置并重启该实例，避免再起第二个 Scene。

## 人工实机验收

由操作员遥控一段有平地和楼梯步态变化的安全路线，结束后检查步态边界、差分解黄色提示和原始记录；保存为 `outdoor-field-v1` 并加载。再补采支路，逐项确认连接/冲突，保存 `outdoor-field-v2`。检查旧版仍在、新版原点一致、地点标注继承、重载后可正向及反向预览。

同时检查暂停和短时断流后没有自动连路。记录实际定位等级、接口类型、采集 session、地图 ID/版本及异常；这部分必须实机完成，合成测试不能替代。导航执行前按统一部署 README 切换 field，先完成平地停车验证，再测试楼梯；check 模式不放行运动。
