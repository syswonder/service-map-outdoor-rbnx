# 密集测量点与方向提示

地图保留全部测量点和有序连接关系，路线响应中的 planning_policy 增加：

- reference_role: ordered_hint_pool
- tracking_required: false
- hint_spacing_min_m: 6.0
- preserve_corners: true
- intermediate_arrival_side_m: 3.0

这些字段声明导航可把路线作为有序方向提示池；mapping 本身不执行运动，不删减原始录制点。由支持该策略的 navigation 在收到终点后构造稀疏队列，保留显著拐角，其他段至少间隔 6 米。旧版本 navigation 不会因为收到这些元数据自动改变其跟踪行为。

继续保留 reference_point。base_link 录制需要有效 localization 状态及真实标定；gnss_antenna 录制不可通过改标签伪装为本体轨迹。定位消费、去重录制和已有地图兼容规则保持生效。
