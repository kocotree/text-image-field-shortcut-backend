# API Protocol

入口：
- `POST /api/process-image` — 生成图片并上传 OSS，返回 OSS URL
- `POST /api/generate-image` — 生成图片，直接返回图片二进制文件

当前约束：
- backend 只保留 Gemini 链路
- backend 当前只面向这两个模型的共同参数集：
  - `gemini-3.1-flash-image-preview`
  - `gemini-3-pro-image-preview`
- 模型默认从 `.env` 中的 `NANO_BANANA_2_MODEL_ID` / `NANO_BANANA_PRO_MODEL_ID` 读取
- API key 从服务端环境变量 `API_KEY` 读取，前端无需传入
- 请求来源校验依赖两个请求头：`X-Base-Signature` 和 `X-Pack-Id`
- 服务端会先对 `X-Base-Signature` 验签，再校验签名中的 `packID` 与 `X-Pack-Id` 一致

## 前端可传参数约定

当前前后端统一约定这 4 类参数：

- `prompt`
- `aspectRatio`
- `imageSize`
- 多张参考图

这份协议只取 `gemini-3.1-flash-image-preview` 和 `gemini-3-pro-image-preview` 的交集。

### 交集参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `prompt` | string | 否 | `""` | 文本提示词 |
| `aspectRatio` | string | 否 | 不填 | 输出图片比例；不传或传空时不下发该字段，保持模型默认比例 |
| `imageSize` | string | 否 | `1K` | 输出分辨率档位 |
| `fileUrl` | string | 否 | - | 单个参考图 URL |
| `fileUrls` | string[] | 否 | - | 多个参考图 URL |
| `file` / `files` | file | 否 | - | 单个或多个参考图文件 |
| `model` | string | 否 | `.env` 默认模型 | 可显式指定 `flash` 或 `pro` |
| `requestId` | string | 否 | `""` | 请求追踪 ID |

### `aspectRatio` 可选值

两个模型的交集只允许：

- `1:1`
- `2:3`
- `3:2`
- `3:4`
- `4:3`
- `4:5`
- `5:4`
- `9:16`
- `16:9`
- `21:9`

### `imageSize` 可选值

两个模型的交集只允许：

- `1K`
- `2K`
- `4K`

说明：

- 不接受 `512`
- 不接受任意像素值，比如 `1024x1024`
- 前端如果传 `2k` / `4k`，后端会标准化为 `2K` / `4K`

### 多参考图约定

- 支持多张参考图
- URL 和上传文件可以混用
- 总参考图数量上限为 `14`
- 服务端会把参考图统一转换成 Gemini 原生 `inline_data`

## 请求格式

### 方式 1：JSON + 文件 URL

请求头：
- `Content-Type: application/json`
- `X-Base-Signature: <context.baseSignature>`
- `X-Pack-Id: <context.packID>`

请求体：

```json
{
  "requestId": "req-001",
  "prompt": "生成一张极简风格的海报",
  "model": "gemini-model-id-from-env",
  "aspectRatio": "16:9",
  "imageSize": "2K",
  "fileUrl": "https://example.com/reference-1.png",
  "fileUrls": [
    "https://example.com/reference-2.png"
  ]
}
```

说明：
- `fileUrl` 和 `fileUrls` 会合并成同一个 URL 列表
- `model` 可不传，不传时走 `.env` 默认模型
- `aspectRatio` 不传或传空时，后端不会向 Gemini 请求体写入该字段，保持模型默认行为
- `imageSize` 不传时默认 `1K`

### 方式 2：multipart/form-data + 文件流

请求头：
- `X-Base-Signature: <context.baseSignature>`
- `X-Pack-Id: <context.packID>`

表单字段：
- `requestId`
- `prompt`
- `model`
- `aspectRatio`
- `imageSize`
- `file`
- `files`

说明：
- `file` 适合单文件
- `files` 适合多文件
- `model` 可不传，不传时走 `.env` 默认模型
- `aspectRatio` 不传或传空时，后端不会向 Gemini 请求体写入该字段，保持模型默认行为
- `imageSize` 不传时默认 `1K`

## 返回格式

两个接口的请求参数完全一致，区别在于返回方式。

### `POST /api/process-image`

生成图片后上传到 OSS，返回 JSON：

```json
{
  "success": true,
  "message": "Image generated and uploaded successfully.",
  "timestamp": "2026-04-23T00:00:00+00:00",
  "data": {
    "requestId": "req-001",
    "model": "gemini-model-id-from-env",
    "ossUrl": "https://your-bucket.oss-cn-hangzhou.aliyuncs.com/path/to/file.png",
    "ossUrls": [
      "https://your-bucket.oss-cn-hangzhou.aliyuncs.com/path/to/file-1.png"
    ]
  }
}
```

### `POST /api/generate-image`

生成图片后直接返回图片二进制文件（不经过 OSS），无需鉴权头。

- 响应 `Content-Type`：图片的 MIME 类型（如 `image/png`）
- 响应 `Content-Disposition`：附带文件名
- 响应体：图片二进制数据

成功时直接返回图片文件流；失败时返回 JSON 错误信息：

```json
{
  "success": false,
  "message": "错误描述",
  "timestamp": "2026-04-23T00:00:00+00:00",
  "data": {}
}
```
