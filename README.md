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

非敏感的服务商地址、主备顺序、默认模型、模型别名、能力和服务商模型映射统一存放在 `config/providers.json`。环境变量只保存密钥、运行开关和部署参数。

### 支持模型

| 公共模型 ID | 兼容别名 | EasyRouter 模型 ID | OpenRouter 模型 ID | 能力 |
|---|---|---|---|---|
| `gemini-3.1-flash-image` | `gemini-3.1-flash-image-preview` | `gemini-3.1-flash-image` | `google/gemini-3.1-flash-image` | 图片生成、图片理解、参考图 |
| `gemini-3-pro-image` | `gemini-3-pro-image-preview` | `gemini-3-pro-image` | `google/gemini-3-pro-image` | 图片生成、图片理解、参考图 |
| `gemini-2.5-flash-image`（Nano Banana） | `gemini-2.5-flash-image-preview` | `gemini-2.5-flash-image` | `google/gemini-2.5-flash-image` | 图片生成、图片理解、参考图 |
| `gpt-image-2` | - | `gpt-image-2` | `openai/gpt-image-2` | 图片生成 |

默认模型为 `gemini-3.1-flash-image`。客户端传入 preview 兼容别名时，会解析为对应的公共模型 ID，再映射到实际服务商模型 ID。

推荐生产环境配置：

```dotenv
APP_ENV=production
LOG_LEVEL=INFO
AUTH_SERVICE_URL=http://kocotree-skills-auth:5050

EASYROUTER_API_KEY=
OPENROUTER_API_KEY=
FALLBACK_ENABLED=true

OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_BUCKET_NAME=
OSS_BUCKET_FOLDER_PREFIX=images

FEISHU_ALERT_ENABLED=true
FEISHU_ALERT_WEBHOOK_URL=
FEISHU_ALERT_SECRET=
```

使用 STS 临时凭证访问 OSS 时增加 `OSS_SESSION_TOKEN`。`FEISHU_ALERT_ENABLED=true` 时必须同时配置 Webhook 和签名密钥。

熔断、兜底告警计数和通知冷却使用应用进程内的线程安全内存。Gunicorn 以单 worker、多线程方式运行，不需要额外的状态中间件；应用进程重启后状态会清空。

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
  model = "gemini-3.1-flash-image"
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
