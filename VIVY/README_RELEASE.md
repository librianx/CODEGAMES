# VIVY 快速使用说明（给朋友）

## 1) 启动
双击 `VIVY.exe`。

## 2) 首次配置 API Key
在程序同目录创建 `.env`（可复制 `.env.example` 并重命名）。

至少填写：
`DEEPSEEK_API_KEY=你的Key`

可选：
- `DEEPSEEK_BASE_URL=https://api.deepseek.com`
- `DEEPSEEK_MODEL=deepseek-chat`
- `FLASK_PORT=5000`

## 3) 桌宠操作
- 左键拖拽移动
- 输入框回车发送
- 右键可退出

## 4) 常见问题
- 启动后无回复：检查 `.env` 的 `DEEPSEEK_API_KEY`
- 首次慢：首次运行会初始化本地缓存
- 如杀软拦截：将程序目录加入白名单
