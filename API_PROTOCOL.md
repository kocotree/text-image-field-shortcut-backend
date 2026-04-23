# API Protocol

统一入口：
- `POST /api/process-image`

当前约束：
- backend 只保留 Gemini 链路
- 模型默认从 `.env` 中的 `NANO_BANANA_2_MODEL_ID` / `NANO_BANANA_PRO_MODEL_ID` 读取
- Maibao API key 只从请求头 `Authorization: Bearer <key>` 读取
- 请求来源校验依赖两个请求头：`X-Base-Signature` 和 `X-Pack-Id`
- 服务端会先对 `X-Base-Signature` 验签，再校验签名中的 `packID` 与 `X-Pack-Id` 一致

## 请求格式

### 方式 1：JSON + 文件 URL

请求头：
- `Content-Type: application/json`
- `X-Base-Signature: <context.baseSignature>`
- `X-Pack-Id: <context.packID>`
- `Authorization: Bearer <maibao-api-key>`

请求体：

```json
{
  "requestId": "req-001",
  "prompt": "生成一张极简风格的海报",
  "model": "gemini-model-id-from-env",
  "fileUrl": "https://example.com/reference-1.png",
  "fileUrls": [
    "https://example.com/reference-2.png"
  ]
}
```

说明：
- `fileUrl` 和 `fileUrls` 会合并成同一个 URL 列表
- `model` 可不传，不传时走 `.env` 默认模型

### 方式 2：multipart/form-data + 文件流

请求头：
- `X-Base-Signature: <context.baseSignature>`
- `X-Pack-Id: <context.packID>`
- `Authorization: Bearer <maibao-api-key>`

表单字段：
- `requestId`
- `prompt`
- `model`
- `file`
- `files`

说明：
- `file` 适合单文件
- `files` 适合多文件
- `model` 可不传，不传时走 `.env` 默认模型

## 返回格式

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
