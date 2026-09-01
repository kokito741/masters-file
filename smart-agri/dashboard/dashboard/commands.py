import queue
import threading
import time

import paho.mqtt.client as mqtt


class MQTTCommandClient:
    def __init__(self, config):
        self.host = config.MQTT_HOST
        self.port = config.MQTT_PORT
        self.api_key = config.FIWARE_API_KEY
        self.ack_timeout = config.MQTT_ACK_TIMEOUT
        self.client = mqtt.Client()
        self._pending = {}
        self._lock = threading.Lock()

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def start(self):
        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(f"/{self.api_key}/+/cmdexe")

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        # format: {device_id}@{command}|{result}
        if "@" not in payload or "|" not in payload:
            return

        left, result = payload.split("|", 1)
        device_id, command = left.split("@", 1)
        key = (device_id, command)

        with self._lock:
            waiters = self._pending.get(key, [])
            if waiters:
                waiter = waiters.pop(0)
                if not waiters:
                    self._pending.pop(key, None)
                waiter.put({"ok": True, "ack": result, "payload": payload})

    def publish_command(self, device_id, command, value):
        topic = f"/{self.api_key}/{device_id}/cmd"
        payload = f"{device_id}@{command}|{value}"
        key = (device_id, command)
        waiter = queue.Queue(maxsize=1)

        with self._lock:
            self._pending.setdefault(key, []).append(waiter)

        info = self.client.publish(topic, payload)
        info.wait_for_publish(timeout=2)

        if not info.is_published():
            with self._lock:
                if key in self._pending and waiter in self._pending[key]:
                    self._pending[key].remove(waiter)
                    if not self._pending[key]:
                        self._pending.pop(key, None)
            return {
                "ok": False,
                "error": "Publish failed",
                "topic": topic,
                "payload": payload,
            }

        try:
            ack = waiter.get(timeout=self.ack_timeout)
            ack.update({"topic": topic, "payload": payload})
            return ack
        except queue.Empty:
            with self._lock:
                if key in self._pending and waiter in self._pending[key]:
                    self._pending[key].remove(waiter)
                    if not self._pending[key]:
                        self._pending.pop(key, None)
            return {
                "ok": False,
                "error": f"No cmdexe ack within {int(self.ack_timeout)}s",
                "topic": topic,
                "payload": payload,
            }


def normalize_value(command, value):
    if command == "valve":
        if value not in {"open", "close"}:
            raise ValueError("valve value must be 'open' or 'close'")
        return value

    if command in {"setmin", "setmax"}:
        try:
            threshold = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("threshold must be numeric") from exc
        if threshold <= 0 or threshold > 100:
            raise ValueError("threshold must be in range (0, 100]")
        return f"{threshold:g}"

    if command == "setauto":
        val = str(value)
        if val not in {"0", "1"}:
            raise ValueError("setauto value must be '1' or '0'")
        return val

    raise ValueError("Unsupported command")
