# maibao-field-shortcut-backend

一个最小可运行的 Flask demo，用来承接后续这条链路：

1. 调用模型生成图片
2. 上传图片到 OSS
3. 返回图片 URL 给字段捷径

当前版本先把服务骨架和接口跑通，还没有接入真实的 Maibao 和 OSS。

## 本地运行

```powershell
uv run python main.py
```

默认监听：

- `http://127.0.0.1:5000`

可选环境变量：

- `FLASK_HOST`
- `FLASK_PORT`
- `FLASK_DEBUG`

示例：

```powershell
$env:FLASK_PORT="5050"
uv run python main.py
```

## 接口

### 健康检查

```powershell
Invoke-WebRequest http://127.0.0.1:5000/health
```

### 图片生成占位接口

```powershell
$body = @{
  prompt = "生成一张极简风格的产品海报"
  model = "gemini-3.1-flash-image-preview"
  attachments = @(
    "https://example.com/reference-1.png"
  )
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/v1/generate-image `
  -ContentType "application/json" `
  -Body $body
```

当前返回的是 demo 数据，便于先联调调用链和返回格式。

## OSS 上传 Demo

项目里额外提供了一个独立的 OSS SDK V2 上传脚本：

- `oss_upload_demo.py`

它会从项目根目录的 `.env` 读取这些变量：

- `OSS_ENDPOINT`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_BUCKET_NAME`
- `OSS_BUCKET_FOLDER_PREFIX`

默认上传一段内存文本：

```powershell
uv run python oss_upload_demo.py
```

也支持上传本地文件：

```powershell
uv run python oss_upload_demo.py --file .\README.md
```
