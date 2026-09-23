from __future__ import annotations

import asyncio
import time
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

import httpx


class MajsoulApiError(RuntimeError):
    pass


def extract_paipu_id(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            return parse_qs(parsed.query).get("paipu", [""])[0]
    except ValueError:
        pass
    return value


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

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "astrbot-plugin-mahjong-helper/0.2.6",
        }
        token = (self.token_getter() or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _get(self, path: str, params: dict[str, Any] | None = None):
        last_error: Exception | None = None
        headers = self._headers()
        for host in self.hosts:
            await self._limit()
            try:
                async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
                    response = await client.get(f"https://{host}/api/v2{path}", params=params)
                if response.status_code == 404:
                    return None
                if response.status_code == 429:
                    if "x-cap-token-required" in response.text:
                        raise MajsoulApiError(
                            "牌谱屋对局接口要求验证码或官方授权密钥，当前无法读取对局；"
                            "基本、顺位、立直等统计仍可使用。"
                        )
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

    async def player_stats(
        self, uid: str, mode: int, room_modes: str | None = None,
        since_ms: int | None = None,
    ):
        now = int(time.time() * 1000)
        modes = room_modes or self.MODE_PARAMS[mode]
        return await self._get(
            f"/pl{mode}/player_stats/{uid}/{since_ms or self.START_TIMESTAMP}/{now}",
            {"mode": modes},
        )

    async def extended_stats(
        self, uid: str, mode: int, room_modes: str | None = None,
        since_ms: int | None = None,
    ):
        now = int(time.time() * 1000)
        modes = room_modes or self.MODE_PARAMS[mode]
        return await self._get(
            f"/pl{mode}/player_extended_stats/{uid}/{since_ms or self.START_TIMESTAMP}/{now}",
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

    async def records_page(
        self, uid: str, mode: int, page: int = 1, page_size: int = 5,
        room_modes: str | None = None, since_ms: int | None = None,
    ) -> list[dict]:
        """Read a page via Koromo's timestamp cursor (newest first)."""
        if not 1 <= page <= 20 or not 1 <= page_size <= 100:
            raise ValueError("页码只能填写 1～20。")
        stats = await self.player_stats(uid, mode, room_modes, since_ms)
        if not stats:
            return []
        cursor = int(time.time() * 1000)
        modes = room_modes or self.MODE_PARAMS[mode]
        skip = (page - 1) * page_size
        result = []
        tag = stats.get("count", "all")
        while len(result) < page_size:
            requested = min(100, skip + page_size - len(result))
            batch = await self._get(
                f"/pl{mode}/player_records/{uid}/{cursor}/{since_ms or self.START_TIMESTAMP}",
                {"limit": requested, "mode": modes,
                 "descending": "true", "tag": tag},
            )
            tag = ""
            if not isinstance(batch, list) or not batch:
                break
            if skip >= len(batch):
                skip -= len(batch)
            else:
                result.extend(batch[skip:][:page_size - len(result)])
                skip = 0
            last_time = batch[-1].get("startTime", batch[-1].get("start_time"))
            if last_time is None or len(batch) < requested:
                break
            last_time = int(last_time)
            cursor = (last_time * 1000 if last_time < 10_000_000_000 else last_time) - 1
        return result


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

    async def player_brief(self, account_id: str):
        return await self._request("GET", f"/api/players/{quote(str(account_id), safe='')}")

    async def player_statistics(self, account_id: str):
        account_id = quote(str(account_id), safe="")
        return await self._request("GET", f"/api/players/{account_id}/statistics")

    async def fetch_record(self, paipu: str):
        return await self._request(
            "POST",
            "/api/records/fetch",
            json={
                "paipu": paipu,
                "downloadAvatars": False,
                "exportFiles": False,
                "includeDataBase64": True,
            },
        )
