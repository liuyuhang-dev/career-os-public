# COS 客户端骨架（腾讯云）

> 只做一件事：用 COS 的 HMAC-SHA1 签名上传/读取对象。纯标准库，不依赖 SDK。
> 凭证从环境变量读，不写进代码。

## 用法

```bash
export COS_SECRET_ID=<你的 SecretId>
export COS_SECRET_KEY=<你的 SecretKey>
export COS_BUCKET=your-bucket-1250000000
export COS_REGION=ap-hongkong

python cos_client.py ls
python cos_client.py put local.html portfolio/index.html text/html
python cos_client.py get portfolio/index.html
```

Windows PowerShell：

```powershell
$env:COS_SECRET_ID = "<你的 SecretId>"
$env:COS_SECRET_KEY = "<你的 SecretKey>"
```

## 注意

- **不要把 SecretId/SecretKey 写进代码或仓库。** 这台机器上原来的 `cos.py` 是硬编码的，换机前请把密钥存进密码管理器；如果密钥曾经外泄，直接在腾讯云控制台轮换。
- 建议用子账号密钥，只给这个桶的 `GetObject` / `PutObject` 权限，不要用主账号密钥。
- 桶是香港地域时 `COS_REGION=ap-hongkong`；其他地域按控制台填。

## 代码

见 `cos_client.py`。核心是四步：算签名时间 → 算 SignKey → 拼 HttpString → HMAC-SHA1 出 signature。
