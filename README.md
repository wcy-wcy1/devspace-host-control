# DevSpace Host Control

<p align="center">
  <a href="https://github.com/wcy-wcy1/devspace-host-control/releases/tag/v0.2.0-beta.2"><img src="https://img.shields.io/badge/version-v0.2.0--beta.2-0f766e" alt="Version v0.2.0-beta.2" /></a>
  <a href="#permissions"><img src="https://img.shields.io/badge/MCP-files_by_default-6f42c1" alt="MCP defaults to authorized files only" /></a>
  <a href="#environments"><img src="https://img.shields.io/badge/Linux-Ubuntu_%7C_CentOS-2563eb?logo=linux&amp;logoColor=white" alt="Ubuntu and CentOS adaptation" /></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-blue" alt="Apache 2.0 license" /></a>
</p>

<p align="center">
  <img src="./docs/assets/hero.svg" alt="在 Linux 上使用 Claude Desktop，通过 MCP 访问获准的 Windows 文件，并按需开放桌面能力" width="980" />
</p>

让服务器上的 **Claude Desktop** 通过 MCP 处理你的 Windows 文件，默认只访问你允许的目录；命令与桌面能力按需单独开启，安装、配对和日常启动交给 Codex Skill。

## 🧩 能做什么

- 🧭 **自动部署**：检查两端环境，配置 Tailscale、远程桌面和 MCP。
- 🔄 **接着继续**：复用已有安装与配对，部署中断后从未完成的步骤继续。
- 🖱️ **双击启动**：打开 `Start-Claude.cmd`，连接服务器并拉起 Claude。
- 📁 **目录由你选**：只开放指定文件夹，每个目录可选择只读或读写。
- 🔐 **授权在本机**：查看权限和剩余时间，随时暂停；Claude 只获得本机允许的工具。
- 📋 **文字互通**：默认双向文本剪贴板，也可以按需关闭。
- 💬 **本地对话 · 实验性**：Windows 输入同步到远程 Claude，Enter 发送，回复直接回显到本地。
- 🤝 **Claude 调度 Codex · 实验性**：按项目授权分派测试、审查或编码任务，取回报告、截图和文件；默认关闭。

<a id="quick-start"></a>
## 🚀 快速开始

使用本页介绍的最新功能，下载 [**main 分支源码 ZIP**](https://github.com/wcy-wcy1/devspace-host-control/archive/refs/heads/main.zip)，将其中的 `skills/devspace-host-control` 安装到 Codex skills 目录。仓库已包含两端程序，无需自行编译：

```powershell
$skillBase = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'skills' } else { Join-Path $env:USERPROFILE '.codex/skills' }
Expand-Archive -LiteralPath ./devspace-host-control-main.zip -DestinationPath ./hostcontrol-source
New-Item -ItemType Directory -Force -Path $skillBase | Out-Null
Copy-Item -LiteralPath ./hostcontrol-source/devspace-host-control-main/skills/devspace-host-control -Destination $skillBase -Recurse
```

然后在 Codex 中说：

```text
使用 $devspace-host-control，自动部署 Linux Claude Desktop 控制我的 Windows。
先检查现有环境，复用已有配置；只开放我指定的文件夹，缺少必要信息或需要我登录时再问我。
```

Codex 会执行安装、策略配置、证书配对和 MCP 配置。首次使用按提示完成 Tailscale / Claude 登录、系统授权与本机限时会话开启，后续复用已有配置。

已有部署请让 Codex 复用原配对升级两端程序，顺序为 **Windows 代理 → Linux MCP 适配器**。旧代理不支持新的权限发现协议。已发布的 [v0.2.0-beta.2 安装包](https://github.com/wcy-wcy1/devspace-host-control/releases/download/v0.2.0-beta.2/devspace-host-control.zip) 早于本次权限更新。

<a id="environments"></a>
## 🧭 选择部署环境

| 环境 | 系统 | 架构 | 说明 |
| :--- | :--- | :--- | :--- |
| **推荐服务器** | Ubuntu 22.04 / 24.04 | amd64 / arm64 | Claude Desktop 的主要安装路径 |
| **原机适配** | CentOS Stream 9 | x86_64 | 项目提供兼容适配，需明确选择；非 Claude 官方支持系统 |
| **本机电脑** | Windows | amd64 | 保持桌面登录，运行 HostControl |

运行成品无需 Go SDK、Docker 或 Kubernetes。部署时会按确认的机房地区配置服务器时区。

<a id="one-click"></a>
## 🖱️ 日常启动

保留为这台设备生成的**整个启动目录**，以后双击 **`Start-Claude.cmd`** 即可：

<p align="center">
  <img src="./docs/assets/one-click-launch.svg" alt="双击启动 → 复用或启动 HostControl → 打开远程桌面 → 登录或重连 Linux → 打开 Claude Desktop" width="980" />
</p>

已有代理继续运行；新代理默认暂停，需要操作 Windows 时在本机开启限时会话。详细配置见 [双击启动指南](skills/devspace-host-control/references/launcher.md)。

<a id="permissions"></a>
## 🔐 权限由本机决定

在 Windows 代理显示的私有本机控制页，确认允许目录和读写权限，再开启限时会话：

<p align="center">
  <img src="./docs/assets/local-control-panel.png" alt="本机访问控制页：玻璃质感布局，会话状态与文件目录权限分区。图片使用模拟数据。" width="920" />
</p>

控制页采用简洁的玻璃质感布局，适配窄屏与系统深色模式；上图为模拟数据预览。

| 你来决定 | 实际行为 |
| :--- | :--- |
| **访问哪个目录** | 远端使用工作区别名和相对路径，文件工具拒绝越界访问 |
| **只读还是读写** | 只读目录拒绝写入、移动；远端不能自行扩大权限 |
| **何时开始、何时停止** | 会话默认暂停，可查看剩余时间，随时暂停或撤销配对 |
| **修改权限后怎么办** | 保存到本机配置并暂停会话，由你重新开启授权 |

默认仅启用文件能力。命令执行和桌面控制需要单独开启，其权限不受文件夹只读设置约束。文本剪贴板是独立通道，默认开启，不受目录白名单限制。详见 [访问与隐私边界](skills/devspace-host-control/references/privacy.md)。

<a id="local-input"></a>
## 💬 本地输入，远程思考，回复回到眼前

可选配置完成后，双击 **`Claude-Input.cmd`** 打开本地输入框：

**本地写字 → 自动同步草稿 → Enter 发送 → Claude 回复回显到本地。**

<p align="center">
  <img src="./docs/assets/local-input-panel.png" alt="Windows 本地对话面板：上方查看 Claude 最新回复，下方输入消息，Enter 发送、Shift+Enter 换行" width="740" />
</p>

采用轻盈的 iOS 风格：冷白底色、圆角面板、细边框与克制的蓝色操作按钮。回复区与输入区独立，长回复可滚动阅读和复制。

支持中文、多行、emoji、修改和清空。配对的**低延迟通道**在文字变化后排队同步，不再等待 0.6 秒；输入法组词提交后再同步。连续输入时合并为最新版本，不堆积过期内容。**Enter 或「发送」提交一次，Shift+Enter 换行**，确认 Claude 接收后才清空本地输入。普通打字不会发送，未知结果也不会自动重发。

回复生成期间逐次更新文字，最多显示 24000 个字符并提示截断；完成后自动读取这条回复中的**图片预览**，支持**「保存预览」**为 PNG。图片与文字共用滚动阅读区，下方可先写下一条，等当前回复完成再发送。遇到回复未显示，可以保留上一条消息文字，点击**「取回回复」**核对并读取远端最后一条匹配回复，不重复发送。

图片预览来自远程 Claude 中完整可见的图片区域，支持已识别的内嵌图片与 SVG 图形；它是**预览截图，不是原图下载或 SVG 源文件**。每条回复最多 4 张，单张最长边 1024 像素、传输上限 80 KB，必要时缩小。请保持 Claude 在远程桌面前台并完整显示图片；不可见时会提示，显示后再点「取回回复」。仅识别当前匹配回复中的图片，不镜像完整历史、附件或独立侧栏；不获取任意网址、文件路径或执行 SVG/HTML。

本地输入同样需要 Windows 代理保持运行，关闭代理控制台会中断连接。代理重启后，点击「连接空对话」即可刷新连接；本地文字保留，未知操作不会自动重发。

首次连接要求远端草稿为空；远端被修改、手动发送或识别到对话变化时会暂停。在远程新建空对话后点击**「连接空对话」**，已有草稿不会被覆盖。暂停或关闭面板不会取消 Claude 已开始的回复。

低延迟通道复用已有 **7443 / mTLS** 连接，服务器主动连接本机代理，使用独立的本地授权；不增加端口或 MCP 工具，不占用 Windows 剪贴板。远程桌面和 Claude 仍需保持可用。未配置此通道时保留剪贴板兼容模式：等待输入停顿约 0.6 秒、同步期间占用剪贴板，新的复制操作会暂停同步。

> 实验性功能：已在 Windows + CentOS Stream 9 的真实 Claude 中验证低延迟通道的连续草稿编辑、中文、多行、emoji 与清空，以及复制操作互不干扰。取消输入等待不代表零网络延迟，也不是 Claude API 流式接口。实际输入法组词、其它应用版本和 Ubuntu 桌面仍需验证。升级须同时更新 Windows 代理、本地面板和服务器接收程序；先保存旧面板里的未发送内容。

配置与验证见 [本地输入框指南](skills/devspace-host-control/references/input-bridge.md)。

## 🤝 Claude 负责思考，Codex 接手执行

让服务器上的 Claude 拆解需求，再通过 MCP 把任务交给独立的 Codex 执行器：

```text
Claude 分派任务 → 按提交编号准备代码 → 容器内 Codex 执行
       ↑                                  ↓
继续分析与修复 ← 测试结论 / 变更补丁 / 截图与文件
```

本地明确批准项目、分支、任务类型与测试方案。**测试**返回实际退出码，**审查**读取代码给出分析，**编码**修改独立副本并返回补丁。编码任务完成不代表测试通过；每份结果标明实际源提交，便于 Claude 继续判断。

图片按需以 **MCP 图片内容**返回，文字、日志、PDF 等作为登记附件取回，不需要公开图床。稳定任务 ID 防止重复启动；暂停授权会取消任务，重启后不重跑未知结果。可显式追加下一轮，默认最多三轮。

> 这是一项单独启用的 `codex` 能力，保留默认文件权限；无需开放通用命令或桌面控制。执行器需要 Docker **Linux 容器**、固定镜像和独立 Codex 登录，不接管现有桌面聊天。当前支持公开 HTTPS 仓库，不自动提交、推送或合并。容器内可访问其专用模型凭据和联网服务，不承诺匿名或日志自动脱敏。已在 Windows + CentOS 的真实 Claude Desktop 中验证：发现工具 → 调用真实 Codex → 读取浏览器截图中的随机数字 → 按图发起续轮 → 核对补丁。该验收使用固定公开 QA 仓库，其它项目仍需各自验收。

配置、边界与验收步骤见 [Claude → Codex 任务执行指南](skills/devspace-host-control/references/codex-executor.md)。 [查看真实桌面联调记录](docs/claude-codex-acceptance.md)。

<a id="mcp-tools"></a>
## 🧰 MCP 工具

下表是完整工具目录。Claude 实际看到的清单由 Windows 本地权限决定；禁用能力不公布，无法确认权限时拒绝访问。

| 能力 | 工具 | 开放方式 |
| :--- | :--- | :--- |
| **会话状态** | `host_status` · `session_info` | 已认证设备可查询 |
| **工作区文件** | `fs_list` · `fs_read` · `fs_write` · `fs_move` | 默认启用，按目录权限提供 |
| **程序任务** | `exec_start` · `exec_poll` · `exec_cancel` | 单独开启 `exec` |
| **桌面观察** | `desktop_windows` · `desktop_inspect` · `desktop_capture` | 单独开启 `desktop` |
| **鼠标键盘** | `desktop_focus` · `desktop_click` · `desktop_type` · `desktop_key` · `desktop_scroll` | 单独开启 `desktop` |
| **打开网页** | `browser_open` | 单独开启 `desktop` |
| **Codex 协作** | `codex_projects` · `codex_start` · `codex_status` · `codex_result` · `codex_continue` · `codex_cancel` · `codex_artifact_get` | 单独配置并开启 `codex` |

在默认仅文件能力下，目录全部只读时，清单只保留状态查询、`fs_list` 和 `fs_read`。即使客户端缓存了旧清单，每次调用仍由 Windows 重新检查权限。

<a id="architecture"></a>
## 📐 工作原理

<p align="center">
  <img src="./docs/assets/architecture.png" alt="Windows 通过 RDP 连接 Linux Claude，Claude 通过 MCP 和 mTLS 调用 Windows HostControl" width="980" />
</p>

两端通过 Tailscale 私网连接，分别处理远程桌面和工具调用：

| 通道 | 方向 | 用途 |
| :--- | :--- | :--- |
| **RDP · TCP 3389** | Windows → Linux | 你在远程桌面使用 Claude Desktop |
| **MCP / mTLS · TCP 7443** | Linux → Windows | Claude 调用 HostControl 执行本机操作 |

`hostagent` 运行在 Windows；`hostmcp` 由 Linux Claude Desktop 启动；`hostctl` 负责两端证书配对。Codex Skill 组织部署和维护流程。

Claude 的普通联网请求使用 Linux 服务器网络，本项目不额外配置公网代理。目录授权约束的是 Windows 文件工具，不承诺隐藏 Tailscale 等管理连接的公网端点。

<a id="guides"></a>
## 📚 使用指南

[自动化与续跑](skills/devspace-host-control/references/automation.md) · [部署与配对](skills/devspace-host-control/references/operations.md) · [双击启动](skills/devspace-host-control/references/launcher.md) · [故障诊断](skills/devspace-host-control/references/troubleshooting.md) · [时区配置](skills/devspace-host-control/references/timezone.md) · [权限与隐私](skills/devspace-host-control/references/privacy.md) · [本地输入框](skills/devspace-host-control/references/input-bridge.md)

开发入口：[Go 模块](hostcontrol/) · [Skill 源码](skills/devspace-host-control/) · [发布打包](scripts/package.py)。

## 📄 许可

[Apache-2.0](LICENSE)。源自对 [DevSpace](https://github.com/loft-sh/devspace) 的源码研究，独立实现主机控制模块；不是 DevSpace 或 Anthropic 的官方产品。出处见 [NOTICE](NOTICE)。
