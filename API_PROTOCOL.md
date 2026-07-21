# API Protocol

入口：
- `POST /api/process-image` — 生成图片并上传 OSS，返回 OSS URL
- `POST /api/generate-image` — 生成图片，直接返回图片二进制文件
- `POST /api/understand-image` — 图片理解，接收图片返回文本描述
- `GET /health/providers` — 查询服务商熔断状态，需要访问令牌

当前约束：
- backend 支持两条链路：Gemini 和 GPT Image
- 当 `model` 以 `gpt-image` 开头时走 GPT Image 链路，否则走 Gemini 链路
- 默认模型、正式模型、preview 兼容别名和服务商模型映射由 `config/providers.json` 定义
- EasyRouter 和 OpenRouter 地址由 `config/providers.json` 定义
- 服务商 API Key 分别从服务端环境变量 `EASYROUTER_API_KEY` 和 `OPENROUTER_API_KEY` 读取，前端无需传入
- 请求来源校验依赖两个请求头：`X-Base-Signature` 和 `X-Pack-Id`
- 服务端会先对 `X-Base-Signature` 验签，再校验签名中的 `packID` 与 `X-Pack-Id` 一致
- GPT Image 链路不支持参考图（fileUrl/fileUrls/files），传入会返回 400

## 前端可传参数约定

### 通用参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `prompt` | string | 否 | `""` | 文本提示词 |
| `model` | string | 否 | `providers.json` 默认模型 | 以 `gpt-image` 开头走 GPT Image，否则走 Gemini |
| `aspectRatio` | string | 否 | 不填 | 输出图片比例 |
| `requestId` | string | 否 | `""` | 请求追踪 ID |

### Gemini 专属参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `imageSize` | string | 否 | `1K` | 输出分辨率档位（`1K` / `2K` / `4K`） |
| `fileUrl` | string | 否 | - | 单个参考图 URL |
| `fileUrls` | string[] | 否 | - | 多个参考图 URL |
| `file` / `files` | file | 否 | - | 单个或多个参考图文件 |

### GPT Image 2 参数说明

| 字段 | 请求体参数 | 值 | 说明 |
|---|---|---|---|
| `aspectRatio` | `size` | 由 aspectRatio 映射为像素尺寸，默认 `auto` | 不传时模型自动选择 |
| - | `quality` | `auto`（硬编码） | 支持 `low` / `medium` / `high` / `auto` |
| - | `moderation` | `low`（硬编码） | 内容审核级别，`auto` 或 `low` |
| - | `n` | `1`（硬编码） | 生成图片数量 |

GPT Image 2 不支持参考图（fileUrl/fileUrls/files），传入会返回 400。

### `aspectRatio` 可选值

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

GPT Image 2 的 aspectRatio 到 size 映射：

| aspectRatio | size |
|---|---|
| `1:1` | `1024x1024` |
| `3:2` / `4:3` / `5:4` | `1536x1024` |
| `16:9` / `21:9` | `2048x1152` |
| `2:3` / `3:4` / `4:5` | `1024x1536` |
| `9:16` | `1152x2048` |
| 不传 | `auto` |

### `imageSize` 可选值（仅 Gemini）

- `1K`
- `2K`
- `4K`

### 多参考图约定（仅 Gemini）

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

Gemini 示例：
```json
{
  "requestId": "req-001",
  "prompt": "生成一张极简风格的海报",
  "model": "gemini-3.1-flash-image",
  "aspectRatio": "16:9",
  "imageSize": "2K",
  "fileUrl": "https://example.com/reference-1.png",
  "fileUrls": [
    "https://example.com/reference-2.png"
  ]
}
```

GPT Image 2 示例：
```json
{
  "requestId": "req-001",
  "prompt": "生成一张极简风格的海报",
  "model": "gpt-image-2",
  "aspectRatio": "16:9"
}
```

说明：
- `fileUrl` 和 `fileUrls` 会合并成同一个 URL 列表（仅 Gemini）
- `model` 可不传，不传时使用 `providers.json` 中的默认模型
- `model` 以 `gpt-image` 开头时走 GPT Image 链路
- `aspectRatio` 不传或传空时，Gemini 保持模型默认行为，GPT Image 使用 `auto`
- `imageSize` 不传时默认 `1K`（仅 Gemini 使用）

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
- `model` 可不传，不传时使用 `providers.json` 中的默认模型
- `model` 以 `gpt-image` 开头时走 GPT Image 链路
- `aspectRatio` 不传或传空时，Gemini 保持模型默认行为，GPT Image 使用 `auto`
- `imageSize` 不传时默认 `1K`（仅 Gemini 使用）

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
    ],
    "provider": "easyrouter",
    "fallbackUsed": false
  }
}
```

### `POST /api/generate-image`

生成图片后直接返回图片二进制文件（不经过 OSS），无需鉴权头。

- 响应 `Content-Type`：图片的 MIME 类型（如 `image/png`）
- 响应 `Content-Disposition`：附带文件名
- 响应 `X-Model-Provider`：实际完成请求的服务商
- 响应 `X-Fallback-Used`：是否使用兜底服务商
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

### `POST /api/understand-image`

图片理解：接收图片 URL，调用 Gemini 返回文本描述。

请求体（仅 JSON）：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `requestId` | string | 否 | `""` | 请求追踪 ID |
| `prompt` | string | 否 | `""` | 理解提示词，如"描述图片内容" |
| `model` | string | 否 | `gemini-2.5-flash-image` | 模型名称 |
| `fileUrl` | string | 否 | - | 单个图片 URL |
| `fileUrls` | string[] | 否 | - | 多个图片 URL |

请求示例：
```json
{
  "requestId": "req-001",
  "prompt": "描述这张图片的内容",
  "model": "gemini-2.5-flash-image",
  "fileUrls": [
    "https://example.com/photo.png"
  ]
}
```

返回示例：
```json
{
  "success": true,
  "message": "Image understanding completed successfully.",
  "timestamp": "2026-05-30T00:00:00+00:00",
  "data": {
    "requestId": "req-001",
    "model": "gemini-2.5-flash-image",
    "text": "这是一张风景照片，画面中...",
    "provider": "easyrouter",
    "fallbackUsed": false
  }
}
```

说明：
- 仅支持 JSON 请求（不支持 multipart/form-data）
- 不需要 `aspectRatio` / `imageSize` 参数
- 参考图数量上限为 14
- 默认走 Gemini 链路，不支持 GPT Image
- EasyRouter 出现可恢复故障且 `FALLBACK_ENABLED=true` 时使用 OpenRouter
