from __future__ import annotations

import httpx


class ReviewGateway:
    """Optional adapter for a separately deployed Majsoul review service.

    The upstream Yunzai project performs protobuf decoding and image rendering
    inside Node.js.  This AstrBot port keeps that heavy pipeline behind a small
    HTTP contract so deployments can attach a compatible service without
    coupling the normal query features to Node.js.
    """

    def __init__(self, base_url: str, token: str = "", timeout: float = 300):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    async def _post(self, path: str, payload: dict):
        if not self.base_url:
            raise RuntimeError(
                "本地 API 只能登录和取回原始牌谱，不能单独完成 Mortal 分析。"
                "请另行配置 review_gateway_url；玩家查询和订阅不受影响。"
            )
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            response = await client.post(self.base_url + path, json=payload)
        response.raise_for_status()
        return response.json()

    async def review(self, paipu_url: str, seat: str = ""):
        return await self._post("/review", {"paipu_url": paipu_url, "seat": seat})

    async def scene(self, paipu_url: str, round_number: int, turn: int | None):
        return await self._post(
            "/scene",
            {"paipu_url": paipu_url, "round": round_number, "turn": turn},
        )
