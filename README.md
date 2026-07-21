# text-image-field-shortcut-backend

一个 Flask backend，用于承接字段捷径后的处理链路：

1. 接收字段捷径请求
2. 根据 model 自动路由至 Gemini 或 GPT Image 生成图片
3. 上传图片到 OSS，返回 OSS URL（`/api/process-image`）
4. 或直接返回图片文件（`/api/generate-image`）
5. 图片理解：接收图片，调用 Gemini 返回文本描述（`/api/understand-image`）

当前已接入：
- HTTP 接口骨架
- JSON / multipart 两种输入解析
- Gemini 生图（支持参考图）
- GPT Image 2 生图（size/quality/moderation）
- Gemini 图片理解（图片→文本）
- 真实 OSS 上传
- 直接返回图片文件（无需 OSS）
- EasyRouter 主服务商与 OpenRouter 顺序兜底
- 可配置的同服务商重试、请求总时限和路由结果标识

## 配置

后端代码统一通过环境变量读取配置。

常用变量：
- `DEFAULT_API_URL`
- `API_KEY`
- `NANO_BANANA_2_MODEL_ID`（Gemini 模型）
- `NANO_BANANA_PRO_MODEL_ID`（Gemini 模型）
- `GPT_IMAGE_MODEL_ID`（默认 `gpt-image-2`）
- `OSS_ENDPOINT`
- `OSS_BUCKET_NAME`
- `OSS_BUCKET_FOLDER_PREFIX`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `LOG_LEVEL`
- `PROVIDER_CONNECT_TIMEOUT_SECONDS`（默认 `10`）
- `PROVIDER_READ_TIMEOUT_SECONDS`（默认 `300`）
- `PROVIDER_WRITE_TIMEOUT_SECONDS`（默认 `60`）
- `PROVIDER_POOL_TIMEOUT_SECONDS`（默认 `5`）
- `HTTP_MAX_CONNECTIONS`（每个客户端默认 `16`）
- `HTTP_MAX_KEEPALIVE_CONNECTIONS`（每个客户端默认 `8`）
- `HTTP_TRUST_ENV`（是否读取系统代理配置，默认 `false`）
- `REFERENCE_CONNECT_TIMEOUT_SECONDS`（默认 `10`）
- `REFERENCE_READ_TIMEOUT_SECONDS`（默认 `120`）
- `REFERENCE_MAX_BYTES`（默认 `52428800`）
- `REFERENCE_MAX_REDIRECTS`（默认 `3`）
- `PROVIDER_CONFIG_PATH`（默认 `config/providers.json`）
- `FALLBACK_ENABLED`（默认 `false`）
- `OPENROUTER_API_URL`（默认 `https://openrouter.ai/api/v1`）
- `OPENROUTER_API_KEY`
- `MODEL_REQUEST_DEADLINE_SECONDS`（默认 `390`，应小于 Gunicorn 超时）
- `PRIMARY_MAX_ATTEMPTS`（默认 `1`）
- `FALLBACK_MAX_ATTEMPTS`（默认 `1`）
- `PRIMARY_EMPTY_RESPONSE_RETRY_COUNT`（默认 `1`）
- `REDIS_URL`（未配置时关闭跨 worker 熔断状态）
- `REDIS_KEY_NAMESPACE`（默认 `text_image_field_shortcut`）
- `REDIS_SOCKET_TIMEOUT_SECONDS`（默认 `1`）
- `CIRCUIT_FAILURE_THRESHOLD`（默认 `3`）
- `CIRCUIT_OPEN_SECONDS`（默认 `60`）
- `CIRCUIT_MAX_OPEN_SECONDS`（默认 `900`）
- `CIRCUIT_STATE_TTL_SECONDS`（默认 `86400`）

说明：
- API Key 从环境变量 `API_KEY` 读取，无需在请求头传入
- Gemini 正式版使用 `gemini-3.1-flash-image` 和 `gemini-3-pro-image`；旧请求或环境变量中的对应 preview 模型会通过兼容映射路由到正式版

## Docker
构建
```bash
cd <path-to-workspace>/text-image-field-shortcut-backend

docker build -t text-image-field-shortcut .
```

启动
```bash
docker run --name text-image-field-shortcut --env-file .env -p 5000:5000 text-image-field-shortcut
```

compose
```bash
docker compose up --build
```

## 接口

### 健康检查

```powershell
Invoke-WebRequest http://127.0.0.1:5000/health
```

### 图片处理接口（返回 OSS URL）

```powershell
$body = @{
  requestId = "req-001"
  prompt = "生成一张极简风格的海报"
  model = "gemini-3.1-flash-image"
  aspectRatio = "16:9"
  imageSize = "2K"
  fileUrls = @(
    "https://example.com/reference-1.png"
  )
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/process-image `
  -ContentType "application/json" `
  -Body $body
```

### 图片生成接口（直接返回图片文件）

Gemini：
```powershell
$body = @{
  prompt = "a cute cat"
  model = "gemini-3.1-flash-image"
  aspectRatio = "1:1"
  imageSize = "1K"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/generate-image `
  -ContentType "application/json" `
  -Body $body `
  -OutFile "output.png"
```

GPT Image 2：
```powershell
$body = @{
  prompt = "a cute cat"
  model = "gpt-image-2"
  aspectRatio = "1:1"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/generate-image `
  -ContentType "application/json" `
  -Body $body `
  -OutFile "output.png"
```

### 图片理解接口（返回文本）

```powershell
$body = @{
  requestId = "req-001"
  prompt = "描述这张图片的内容"
  model = "gemini-2.5-flash-image"
  fileUrls = @(
    "https://example.com/photo.png"
  )
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/understand-image `
  -ContentType "application/json" `
  -Body $body
```
