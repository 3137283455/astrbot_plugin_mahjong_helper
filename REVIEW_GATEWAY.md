# 牌谱分析网关合同

网关供需要 Mortal Review 或场况图的部署使用。它独立于插件主进程，避免
Node.js protobuf、雀魂协议版本和图片渲染依赖影响普通查询。

## 鉴权

填写 `review_gateway_token` 后，插件发送：

```text
Authorization: Bearer <token>
```

## Review

`POST /review`

```json
{"paipu_url": "https://game.maj-soul.com/1/?paipu=...", "seat": "0"}
```

## 场况

`POST /scene`

```json
{"paipu_url": "https://game.maj-soul.com/1/?paipu=...", "round": 1, "turn": 8}
```

两个接口都返回 JSON。插件识别以下可选字段：

```json
{
  "message": "文字摘要",
  "summary": "message 不存在时使用",
  "report_url": "https://example/report/123",
  "image_url": "https://example/image.png",
  "image_urls": ["https://example/1.png", "https://example/2.png"]
}
```
