# VIVY 桌宠（Flask + SQLite + DeepSeek）

## 你能做什么
- 第一次对话：VIVY 会随机提一个偏好问题（带选项按钮）
- 你选完：后端把偏好写入 `SQLite`，并让 VIVY 进行回应
- 每天首次对话：VIVY 会生成一段“冲浪见闻 / 灵感分享”（虚构生成，不做真实网页抓取）
- 跨会话记忆：用户偏好 `preferences(JSON)` + 互动摘要 `summary` 会保存在本地数据库
- 桌面桌宠模式：透明无边框、始终置顶、可拖拽移动

## 环境要求
- Python 3.10+（推荐 3.10～3.12；3.13+ 请自行确认 PyQt6 是否有对应 wheel）
- **Windows 11** 与 **macOS**（Intel / Apple Silicon）均可从源码运行；当前仓库未内置 macOS `.app` / 公证流程，需在 Mac 本机安装依赖后启动或自行用 PyInstaller 打包。

## 安装
### Windows（PowerShell）
```powershell
cd E:\CODEGAMES\VIVY
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### macOS（终端）
```bash
cd /path/to/VIVY
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
复制 `env.example` 为 `.env` 并填写 `DEEPSEEK_API_KEY`（与 Windows 相同）。

## 配置 DeepSeek
复制并编辑：`env.example` → `.env`（或按发布包里的 `.env.example`）

把 `DEEPSEEK_API_KEY` 填上。

也可以直接在程序里配置（推荐给普通用户）：
- 首次启动若未配置，会弹窗让你输入 API Key 并自动保存到 `.env`
- 运行中可右键桌宠，点击 `设置 API Key` 随时修改

## 运行（桌面悬浮桌宠，推荐）
### Windows
```powershell
cd E:\CODEGAMES\VIVY
.\start_desktop.ps1
```

如果要改端口：
```powershell
.\start_desktop.ps1 -Port 5001
```

### macOS
```bash
cd /path/to/VIVY
chmod +x start_desktop.sh   # 只需第一次
./start_desktop.sh
```

指定端口：
```bash
FLASK_PORT=5001 ./start_desktop.sh
```

**macOS 说明：** 代码未使用 Win32 专用 API；无边框置顶窗口在多数机器上可直接用。若首次用 PyInstaller 自组 `.app`，未签名的应用需在图标上 **右键 → 打开** 以绕过门禁；分发可考虑 Apple Developer 签名与公证（与具体打包脚本有关，需在本机 Xcode/证书环境下完成）。

**PyInstaller（在 Mac 上打包，数据分隔符为冒号）：**
```bash
source .venv/bin/activate
pyinstaller --noconfirm --windowed --name VIVY \
  --add-data "static/images:static/images" \
  --add-data "env.example:." \
  desktop_pet.py
```
产物一般在 `dist/VIVY.app`；图标在 macOS 上常用 `.icns`，需可自行从 PNG 转换后加入 `--icon`。

### 桌宠操作
- 左键拖拽：移动桌宠位置
- 点击 `今日灵感`：触发灵感分享
- 点击 `换个问题`：触发随机偏好提问
- 右键桌宠：打开菜单（重置本机 user_id / 退出）

## 说明
当前项目以“桌宠模式”为主，不再提供网页端 UI。

## 数据文件
- 数据库：`vivy.sqlite`
- 桌面版本机用户ID：`.desktop_user_id`

## 分发给朋友（方案 B：安装包）
1. 先构建 exe 和发布目录：
```powershell
cd E:\CODEGAMES\VIVY
.\build_release.ps1 -Version 1.0.0
```

2. 用 Inno Setup 打包安装器：
- 安装 Inno Setup（只需一次）
- 打开 `release\VIVY.iss`
- 点击 `Compile`

3. 产物位置：
- 便携压缩包：`dist\VIVY-release-v1.0.0.zip`
- 安装器：`dist\VIVY-Setup-1.0.0.exe`

### 图标说明
- exe 与安装器图标都使用 `static/images/VIVYstatr.png` 自动转换的 `release/vivy.ico`
