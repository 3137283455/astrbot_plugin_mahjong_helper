from __future__ import annotations

import asyncio
import time
from typing import Any, Callable
from urllib.parse import quote

import httpx


class MajsoulApiError(RuntimeError):
    pass


class KoromoClient:
    MODE_PARAMS = {4: "16.12.9.15.11.8", 3: "22.24.26.21.23.25"}
    START_TIMESTAMP = 1262304000000

    def __init__(
        self,
        token_getter: Callable[[], str | None],
        hosts: list[str] | None = None,
        timeout: float = 15,
    ):
        self.token_getter = token_getter
        self.hosts = hosts or [
            "5-data.amae-koromo.com",
            "1.data.amae-koromo.com",
            "4.data.amae-koromo.com",
            "ak-data-1.sapk.ch",
        ]
        self.timeout = timeout
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0

    async def _limit(self) -> None:
        async with self._rate_lock:
            wait = 1.05 - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _get(self, path: str, params: dict[str, Any] | None = None):
        token = (self.token_getter() or "").strip()
        if not token:
            raise MajsoulApiError(
                "尚未配置牌谱屋 Token。请管理员私聊机器人发送：/设置牌谱屋Token TOKEN"
            )
        last_error: Exception | None = None
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "astrbot-plugin-mahjong-helper/0.2",
        }
        for host in self.hosts:
            await self._limit()
            try:
                async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
                    response = await client.get(f"https://{host}/api/v2{path}", params=params)
                if response.status_code == 404:
                    return None
                if response.status_code == 429:
                    retry_after = min(int(response.headers.get("Retry-After", "5")), 30)
                    await asyncio.sleep(retry_after)
                    last_error = MajsoulApiError("牌谱屋请求过于频繁，请稍后再试")
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
        raise MajsoulApiError(f"牌谱屋接口暂时不可用：{last_error}")

    async def search_player(self, name: str, mode: int) -> list[dict]:
        data = await self._get(
            f"/pl{mode}/search_player/{quote(name, safe='')}",
            {"limit": 10, "tag": "all"},
        )
        if data is None:
            return []
        if isinstance(data, list):
            return data
        for key in ("players", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]
        return []

    async def player_stats(self, uid: str, mode: int, room_modes: str | None = None):
        now = int(time.time() * 1000)
        modes = room_modes or self.MODE_PARAMS[mode]
        return await self._get(
            f"/pl{mode}/player_stats/{uid}/{self.START_TIMESTAMP}/{now}",
            {"mode": modes},
        )

    async def extended_stats(self, uid: str, mode: int, room_modes: str | None = None):
        now = int(time.time() * 1000)
        modes = room_modes or self.MODE_PARAMS[mode]
        return await self._get(
            f"/pl{mode}/player_extended_stats/{uid}/{self.START_TIMESTAMP}/{now}",
            {"mode": modes},
        )

    async def recent_records(
        self, uid: str, mode: int, limit: int = 5, room_modes: str | None = None
    ) -> list[dict]:
        stats = await self.player_stats(uid, mode, room_modes)
        if not stats:
            return []
        now = int(time.time() * 1000)
        modes = room_modes or self.MODE_PARAMS[mode]
        data = await self._get(
            f"/pl{mode}/player_records/{uid}/{now}/{self.START_TIMESTAMP}",
            {
                "limit": max(1, min(limit, 20)),
                "mode": modes,
                "descending": "true",
                "tag": stats.get("count", "all"),
            },
        )
        if data is None:
            return []
        if isinstance(data, list):
            return data
        for key in ("records", "games", "matches", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data] if isinstance(data, dict) else []


class ProtocolClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 20):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key} if self.api_key else {}

    async def _request(self, method: str, path: str, **kwargs):
        async with httpx.AsyncClient(
            timeout=self.timeout, headers=self._headers()
        ) as client:
            response = await client.request(method, self.base_url + path, **kwargs)
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    async def health(self) -> bool:
        for path in ("/api/profiles", "/health", "/"):
            try:
                await self._request("GET", path)
                return True
            except Exception:
                continue
        return False

    async def profiles(self):
        return await self._request("GET", "/api/profiles")

    async def login(self, account: str, password: str):
        return await self._request(
            "POST",
            "/api/auth/login",
            json={"account": account, "password": password, "saveProfile": True},
        )

    async def resolve_friend_id(self, friend_id: str):
        return await self._request(
            "GET", "/api/players/resolve", params={"friendId": friend_id}
        )

