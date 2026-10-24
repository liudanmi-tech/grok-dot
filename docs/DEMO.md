# WhatsApp 经销商助理 · 演示手册

演示链路：经销商在 WhatsApp 提问 → 经销商助理按**该经销商自己的**档案和对外知识库起草回复 → 自动预审 →
渠道经理在控制台“批准并发送” → 回复送达手机。全程留审计。

## 一、前一晚准备（约 30–60 分钟）

### 1. Meta WhatsApp Cloud API 测试号
1. 登录 <https://developers.facebook.com> → My Apps → Create App → 类型选 **Business** → 添加产品 **WhatsApp**。
2. WhatsApp → **API Setup**：
   - 记下 **Phone number ID**；
   - 生成 **Temporary access token**（24 小时有效，**演示当天早上再生成一次**）；
   - “To” 一栏添加你和老板的手机号为测试收件人，并完成短信验证（测试号最多 5 个收件人）。
3. App Settings → Basic → 记下 **App Secret**。

### 2. 模型 Key
通义千问国际版（DashScope International）控制台创建 API Key。不填也能演示，但回复会是模拟文本。

### 3. 填写本机配置（不要发到聊天或提交到 Git）
```bash
cd ~/grok-dot/backend
cp .env.example .env
# 编辑 .env：QWEN_API_KEY、WA_PHONE_NUMBER_ID、WA_ACCESS_TOKEN、WA_APP_SECRET；WA_VERIFY_TOKEN 保持默认或自定
```

### 4. 把演示手机号登记为经销商
编辑 `backend/config/dealers.yaml`，把老板的号码（国家码开头、不带 +，如 `2348031234567` 或中国号码 `8613800000000`）
放到 `D1023 Ikeja Phones` 的 `phones` 里。未登记的号码只会收到“请联系渠道经理登记”的通用话术。

### 5. ngrok 账号（首次）
在 <https://dashboard.ngrok.com> 注册并复制 authtoken，在终端执行 `ngrok config add-authtoken <你的令牌>`。

## 二、演示当天

```bash
# 终端 1：后端（8100）
cd ~/grok-dot/backend && .venv/bin/uvicorn app.main:app --port 8100
# 终端 2：控制台（5173）
cd ~/grok-dot/frontend && npm run dev
# 终端 3：公网隧道（只放行 /webhooks/whatsapp）
~/grok-dot/scripts/tunnel.sh
```

在 Meta 后台 WhatsApp → Configuration → Webhook：
- Callback URL：`https://<ngrok 显示的地址>/webhooks/whatsapp`
- Verify token：与 `.env` 里的 `WA_VERIFY_TOKEN` 相同
- 点 Verify and save，然后在 Webhook fields 里订阅 **messages**

控制台 <http://localhost:5173>，左下角用户切到 **Channel Manager · Lagos**（只有渠道经理能批准经销商回复）。
页面顶部显示“已接入 Meta Cloud API”即为真实模式。

## 三、演示脚本（约 5 分钟）

1. 老板用手机给测试号发：`How much is the TECNO Spark X6851 and do I have stock?`
   → 控制台 WhatsApp 页几秒内出现会话和**待审批草稿**（带“低风险”标记和依据）。
2. 点“批准并发送” → 老板手机收到回复。
3. 发 `Abeg wetin be warranty for Infinix Hot 50?` → Bot 用皮钦语回答。
4. 发 `Can you give me 20% discount and deliver tomorrow?` → Bot 不承诺，转由渠道经理跟进；
   若草稿出现无出处的数字或承诺措辞，自动预审会退回并重写。
5. 发 `agent` → 转人工：手机收到“渠道经理稍后回复”，控制台可人工回复。
6. 点右上角“一键熔断” → 所有对外发送立即停止。
7. 切到“审计”页：哈希链完整，整段对话每一步都可追溯。

## 四、注意

- 测试号只能发给已验证的收件人；经销商发来消息后的 24 小时内可以自由回复（控制台显示窗口状态）。
- 免费版 ngrok 每次启动地址会变，需重新填 Callback URL。
- 隧道只暴露 `/webhooks/whatsapp`；控制台接口是 POC 模拟登录，不能对公网开放。
- 正式给真实经销商使用前需要：Meta 企业认证、正式号码与显示名称审核、法务确认数据存放区域。
