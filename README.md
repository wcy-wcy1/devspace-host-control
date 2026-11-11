# DevSpace Host Control

<p align="center">
  <a href="https://github.com/wcy-wcy1/devspace-host-control/releases/tag/v0.2.0-beta.2"><img src="https://img.shields.io/badge/version-v0.2.0--beta.2-0f766e" alt="Version v0.2.0-beta.2" /></a>
  <a href="#mcp-tools"><img src="https://img.shields.io/badge/MCP-18_tools-6f42c1" alt="18 MCP tools" /></a>
  <a href="#environments"><img src="https://img.shields.io/badge/Linux-Ubuntu_%7C_CentOS-2563eb?logo=linux&amp;logoColor=white" alt="Ubuntu and CentOS adaptation" /></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-blue" alt="Apache 2.0 license" /></a>
</p>

<p align="center">
  <img src="./docs/assets/hero.svg" alt="在 Linux 上使用 Claude Desktop，通过 MCP 操作真实 Windows 桌面" width="980" />
</p>

让 Linux 上的 **Claude Desktop** 操作你的真实 Windows 桌面。用自然语言处理文件、运行命令、查看窗口和操作鼠标键盘，安装与配对交给 Codex Skill。

## 🧩 能做什么

- 🧭 **自动部署**：检查两端环境，配置 Tailscale、远程桌面和 MCP。
- 🔄 **接着继续**：复用已有安装与配对，部署中断后从未完成的步骤继续。
- 🖱️ **双击启动**：打开 `Start-Claude.cmd`，连接服务器并拉起 Claude。
- 📋 **文字互通**：默认双向文本剪贴板，也可以按需关闭。

<a id="quick-start"></a>
## 🚀 快速开始

下载 [**devspace-host-control.zip**](https://github.com/wcy-wcy1/devspace-host-control/releases/download/v0.2.0-beta.2/devspace-host-control.zip)，把其中的同名目录解压到 Codex skills 目录：

```powershell
$skillBase = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'skills' } else { Join-Path $env:USERPROFILE '.codex/skills' }
Expand-Archive -LiteralPath ./devspace-host-control.zip -DestinationPath $skillBase
```

然后在 Codex 中说：

```text
使用 $devspace-host-control，自动部署 Linux Claude Desktop 控制我的 Windows。
先检查现有环境，复用已有配置；缺少必要信息或需要我登录时再问我。
```

Codex 会执行安装、策略配置、证书配对和 MCP 配置。首次使用按提示完成 Tailscale / Claude 登录、系统授权与本机限时会话开启，后续复用已有配置。

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

<a id="mcp-tools"></a>
## 🧰 MCP 工具

| 能力 | 工具 |
| :--- | :--- |
| **会话状态** | `host_status` · `session_info` |
| **工作区文件** | `fs_list` · `fs_read` · `fs_write` · `fs_move` |
| **程序任务** | `exec_start` · `exec_poll` · `exec_cancel` |
| **桌面观察** | `desktop_windows` · `desktop_inspect` · `desktop_capture` |
| **鼠标键盘** | `desktop_focus` · `desktop_click` · `desktop_type` · `desktop_key` · `desktop_scroll` |
| **打开网页** | `browser_open` |

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

<a id="guides"></a>
## 📚 使用指南

[自动化与续跑](skills/devspace-host-control/references/automation.md) · [部署与配对](skills/devspace-host-control/references/operations.md) · [双击启动](skills/devspace-host-control/references/launcher.md) · [故障诊断](skills/devspace-host-control/references/troubleshooting.md) · [时区配置](skills/devspace-host-control/references/timezone.md)

开发入口：[Go 模块](hostcontrol/) · [Skill 源码](skills/devspace-host-control/) · [发布打包](scripts/package.py)。

## 📄 许可

[Apache-2.0](LICENSE)。源自对 [DevSpace](https://github.com/loft-sh/devspace) 的源码研究，独立实现主机控制模块；不是 DevSpace 或 Anthropic 的官方产品。出处见 [NOTICE](NOTICE)。
