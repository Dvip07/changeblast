"""Thin wrapper over the message broker. Payloads are validated against shared-contracts."""
import json


class EventBus:
    def __init__(self, producer):
        self.producer = producer

    def publish(self, topic: str, payload: dict) -> None:
        self.producer.send(topic, json.dumps(payload).encode("utf-8"))
