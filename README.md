# text-image-field-shortcut-backend

一个 Flask backend，用于承接字段捷径后的处理链路：

1. 接收字段捷径请求
2. 调用 Gemini 模型生成图片
3. 上传图片到 OSS
4. 返回 OSS URL 给字段捷径

当前已接入：
- HTTP 接口骨架
- JSON / multipart 两种输入解析
- Gemini-only 请求规划
- 真实 Gemini API 调用
- 真实 OSS 上传

## 配置

后端代码统一通过环境变量读取配置。

常用变量：
- `DEFAULT_API_URL`
- `API_KEY`
- `NANO_BANANA_2_MODEL_ID`
- `NANO_BANANA_PRO_MODEL_ID`
- `OSS_ENDPOINT`
- `OSS_BUCKET_NAME`
- `OSS_BUCKET_FOLDER_PREFIX`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `LOG_LEVEL`

说明：
- API Key 从环境变量 `API_KEY` 读取，无需在请求头传入

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

### 图片处理接口

```powershell
$body = @{
  requestId = "req-001"
  prompt = "生成一张极简风格的海报"
  model = "gemini-3.1-flash-image-preview"
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
