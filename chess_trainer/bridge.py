from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed


LOGGER = logging.getLogger(__name__)
MessageHandler = Callable[[dict[str, Any], ServerConnection], Awaitable[None]]


class BridgeServer:
    def __init__(self, host: str, port: int, handler: MessageHandler) -> None:
        if host != "127.0.0.1":
            raise ValueError("The bridge must bind to loopback only")
        self.host = host
        self.port = port
        self.handler = handler
        self.server: Server | None = None
        self.clients: set[ServerConnection] = set()

    async def start(self) -> None:
        self.server = await serve(
            self._connection,
            self.host,
            self.port,
            max_size=256 * 1024,
            ping_interval=20,
            ping_timeout=20,
            origins=[None, re.compile(r"^chrome-extension://[a-p]{32}$")],
            subprotocols=["local-chess-training-v1"],
        )

    async def _connection(self, websocket: ServerConnection) -> None:
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            hello = _decode(raw)
            if hello.get("type") != "hello":
                await websocket.close(code=4001, reason="Expected extension hello")
                return
            if int(hello.get("v", 0)) != 1:
                await websocket.close(code=4002, reason="Unsupported protocol version")
                return

            self.clients.add(websocket)
            await websocket.send(json.dumps({"v": 1, "type": "hello.ack"}))
            await self.handler({"type": "bridge.connected"}, websocket)
            async for raw_message in websocket:
                message = _decode(raw_message)
                if int(message.get("v", 1)) != 1:
                    continue
                await self.handler(message, websocket)
        except (ConnectionClosed, asyncio.TimeoutError, ValueError, json.JSONDecodeError):
            pass
        finally:
            self.clients.discard(websocket)
            await self.handler({"type": "bridge.disconnected"}, websocket)

    async def send(self, websocket: ServerConnection, message: dict[str, Any]) -> bool:
        if websocket not in self.clients:
            return False
        try:
            await websocket.send(json.dumps({"v": 1, **message}, separators=(",", ":")))
            return True
        except ConnectionClosed:
            self.clients.discard(websocket)
            return False

    async def stop(self) -> None:
        for client in tuple(self.clients):
            await client.close(code=1001, reason="Control centre stopping")
        self.clients.clear()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None


def _decode(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Message must be an object")
    return value
