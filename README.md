# QuotaLeaf · 额度叶片

一张轻巧、可定制的 Windows 桌面额度卡片，用来查看已登录 Codex 账号的剩余额度。

通过官方 Codex App Server 查询真实数据，支持 5 小时、每周及账号返回的其他额度分组。
**它显示的是 Codex 额度，不是 ChatGPT 所有模型的剩余消息数。**

## 功能

- 默认每 **3 秒**查询，可调整为 3–3600 秒；请求尚未完成时不会重复发送。
- 重置倒计时每秒更新；网络失败后自动重连，并标注旧数据。
- 剩余额度低于 **20%** 时，百分数和进度条显示红色。
- **50%–250% 等比例缩放**，文字、图片、图标与圆角同步缩放，不支持拉伸。
- 拖动、边缘吸附、锁定位置、置顶、紧凑模式、隐藏至系统托盘。
- 自定义背景图片、背景透明度、遮罩深度、主题配色、强调色和主卡片圆角。
- 菜单独立采用 10 像素圆角，悬停按钮采用圆角高亮。
- 可选开机启动，默认关闭；外观和位置自动保存在本机。

## 运行条件

- Windows 10 / 11。
- Python 3.10 或更新版本。
- 已安装 [Codex](https://github.com/openai/codex)，并使用 ChatGPT 账号登录。
- 可用的网络连接；如果 Codex 依赖代理，需让应用继承对应环境。

## 安装与启动

在项目目录中执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe quota_card.py
```

之后可双击 `launch.vbs` 启动，它会优先使用项目内 `.venv`，否则使用 PATH 中的 `pythonw.exe`，不弹出终端窗口。

如果额度未连接，先确认 Codex 已使用 ChatGPT 登录，再点击卡片刷新按钮。应用会优先寻找可直接运行的 Codex 可执行文件，并兼容 PATH 中的 Codex 启动器。

## 操作

| 操作 | 效果 |
| --- | --- |
| 拖动卡片空白处 | 移动位置 |
| 双击空白处 / 点击齿轮 | 打开个性化设置 |
| 点击图钉 | 切换置顶 |
| 点击刷新图标 | 立即查询额度 |
| 点击三个点 / 右键卡片 | 打开更多菜单 |
| 点击系统托盘图标 | 恢复隐藏的卡片 |
| 菜单 → 退出卡片 | 退出卡片并关闭自身的额度查询进程 |

## 数据与隐私

额度来自本机 Codex 的 `account/rateLimits/read` 接口，并兼容额度更新通知。
只执行初始化与只读额度查询，不发送模型对话、不触发额度重置、不充值。
登录由 Codex 自身处理；应用不复制账号密码或访问令牌。

运行时生成的 `state/` 目录包含：

- `settings.json`：窗口位置、背景图片路径和个性化选项。
- `usage-cache.json`：最近一次额度及时间，用于离线显示。
- `error.log`：仅发生程序异常时生成。

**整个 `state/` 目录已被 Git 忽略，不应上传。** 不可用的额度显示 `—`；倒计时到期不会擅自将额度设为 100%，仍等待服务端返回。

## 验证

```powershell
python -m unittest discover -s tests -v
```

可选真实连接检查（会短暂显示窗口）：

```powershell
python quota_card.py --probe
```

## 实现

Python + PyQt5 原生桌面界面；通过本地子进程与 Codex App Server 交换 JSON 消息。
默认主卡片圆角为 24 逻辑像素，可自行调整。高频刷新不是服务端的实时推送保证；遇到网络延迟或服务限流时以实际响应为准。

官方接口文档：[Codex App Server](https://learn.chatgpt.com/docs/app-server)。

这是独立的个人项目，与 OpenAI 无隶属或背书关系。
