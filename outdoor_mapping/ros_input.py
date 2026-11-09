"""Subscribe to discovered inputs and publish only map identity."""
import json
import logging
import threading

log = logging.getLogger(__name__)


class Inputs:
    def __init__(self, provider, mapping, config):
        self.provider, self.mapping, self.config = provider, mapping, config
        self.channels = []
        self.node = self.context = self.executor = self.thread = None
        self.stopping = threading.Event()

    def endpoint(self, contract, provider_id):
        from robonix_api import ATLAS
        matches = ATLAS.find_capability(contract_id=contract, provider_id=provider_id, transport="ros2")
        if len(matches) != 1:
            raise ValueError(f"Expected one provider for {contract}; configure its provider_id")
        channel = self.provider.connect_capability(matches[0], contract, "ros2")
        self.channels.append(channel)
        return channel.endpoint

    def gait_binding(self):
        # Do not inspect, resolve or import any gait configuration for wheels.
        if self.config.get("locomotion_mode", "legged") == "wheeled":
            return {}, None
        gait = self.config.get("gait", {})
        topic = (self.endpoint(gait["contract"], gait.get("provider_id", ""))
                 if gait.get("contract") else gait.get("topic"))
        if not topic:
            raise ValueError("Configure an actual gait capability or explicit external gait topic")
        return gait, topic

    def start(self):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from rosidl_runtime_py.utilities import get_message
        from rosidl_runtime_py.convert import message_to_ordereddict
        from map.msg import MapLifecycle
        try:
            fix = self.endpoint("robonix/primitive/gnss/fix", self.config.get("gnss_provider_id", ""))
            status = self.endpoint("robonix/primitive/gnss/rtk_status", self.config.get("gnss_provider_id", ""))
            gait, gait_topic = self.gait_binding()
            self.context = rclpy.Context()
            rclpy.init(context=self.context)
            self.node = rclpy.create_node("outdoor_map_inputs", context=self.context)
            log.info("GNSS input domain=%s fix=%s status=%s gait=%s", self.context.get_domain_id(), fix, status, gait_topic)
            self.executor = SingleThreadedExecutor(context=self.context)
            self.executor.add_node(self.node)
            qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)

            wheeled = self.config.get("locomotion_mode", "legged") == "wheeled"
            def callback(kind, topic):
                seen = False
                def receive(msg):
                    nonlocal seen
                    if not seen:
                        log.info("First %s input from %s", kind, topic)
                        seen = True
                    raw = message_to_ordereddict(msg)
                    value = dict(raw)
                    if kind == "rtk_status":
                        try:
                            value = json.loads(raw["data"])
                            if not isinstance(value, dict):
                                raise ValueError("RTK status must contain an object")
                            value["raw_message"] = raw
                        except (ValueError, KeyError) as error:
                            value = {"raw_message": raw, "error": str(error)}
                    elif kind == "gait":
                        try:
                            from .locomotion import feedback_value
                            code = feedback_value(raw, gait.get("field", "data.gait_state.gait"))
                            value = {"code": code, "raw_message": raw}
                        except (ValueError, TypeError, KeyError) as error:
                            value = {"code": None, "raw_message": raw, "error": str(error)}
                    self.mapping.observe(kind, value, topic)
                return receive

            for kind, topic, msg_type in (
                ("fix", fix, "sensor_msgs/msg/NavSatFix"),
                ("rtk_status", status, "std_msgs/msg/String"),
                ("gait", gait_topic, gait.get("msg_type", "drdds/msg/MotionInfo")),
            ):
                if kind == "gait" and wheeled:
                    continue
                self.node.create_subscription(get_message(msg_type), topic, callback(kind, topic), qos)
            lifecycle_topic = self.config.get("lifecycle_topic", "/robonix/outdoor_map/lifecycle")
            publisher = self.node.create_publisher(MapLifecycle, lifecycle_topic,
                QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                           durability=DurabilityPolicy.TRANSIENT_LOCAL))
            self.provider.declare_ros2_topic("robonix/service/map/lifecycle", lifecycle_topic, qos="transient_local")

            def publish(state):
                publisher.publish(MapLifecycle(**state))
            self.mapping.on_lifecycle = publish
            publish(self.mapping.lifecycle())
            def spin():
                # Bound the wait so late publishers and shutdown cannot leave the input thread asleep.
                while not self.stopping.is_set() and self.context.ok():
                    self.executor.spin_once(timeout_sec=.2)
            self.thread = threading.Thread(target=spin, daemon=True)
            self.thread.start()
        except Exception:
            self.close()
            raise

    def close(self):
        self.stopping.set()
        self.mapping.on_lifecycle = None
        if self.executor:
            self.executor.shutdown(timeout_sec=2)
        if self.thread:
            self.thread.join(timeout=2)
        if self.node:
            self.node.destroy_node()
        if self.context and self.context.ok():
            self.context.shutdown()
        for channel in self.channels:
            channel.close()
        self.channels = []
        self.node = self.executor = self.thread = self.context = None
