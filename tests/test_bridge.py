from __future__ import annotations

import json
from typing import Any

import pytest

from chess_trainer.bridge import BridgeServer


class FakeWebSocket:
    def __init__(self, first_message: dict[str, Any]) -> None:
        self.first_message = json.dumps(first_message)
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int, str] | None = None

    async def recv(self) -> str:
        return self.first_message

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)

    def __aiter__(self) -> FakeWebSocket:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_bridge_accepts_versioned_local_extension_hello() -> None:
    events: list[str] = []

    async def handler(message: dict[str, Any], _websocket: Any) -> None:
        events.append(str(message["type"]))

    bridge = BridgeServer("127.0.0.1", 8765, handler)
    socket = FakeWebSocket({"v": 1, "type": "hello"})

    await bridge._connection(socket)  # Exercise the handshake without opening a real port.

    assert socket.closed is None
    assert socket.sent == [{"v": 1, "type": "hello.ack"}]
    assert events == ["bridge.connected", "bridge.disconnected"]
