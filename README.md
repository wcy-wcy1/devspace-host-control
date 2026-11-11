<div align="center">

<img src="docs/assets/hero.svg" alt="DevSpace Host Control — Linux Claude Desktop × 真实 Windows 桌面" width="100%" />

# DevSpace Host Control

让云端 Claude，连接你的真实 Windows 桌面。

**自动部署 · 断点续跑 · 双击启动 · 18 个 MCP 工具**

[![Version](https://img.shields.io/badge/v0.2.0--beta.2-beta-0f766e)](https://github.com/wcy-wcy1/devspace-host-control/releases/tag/v0.2.0-beta.2) [![MCP](https://img.shields.io/badge/MCP-18_tools-2563eb)](#mcp-tools) [![License](https://img.shields.io/badge/License-Apache_2.0-c28a36)](LICENSE)

[**下载 Skill**](https://github.com/wcy-wcy1/devspace-host-control/releases/download/v0.2.0-beta.2/devspace-host-control.zip) · [快速开始](#quick-start) · [双击启动](#one-click) · [系统架构](#architecture) · [使用指南](#guides)

</div>

---

在 Linux 远程桌面中使用 **Claude Desktop**，通过 MCP 读写 Windows 文件、执行命令、截图并操作鼠标键盘。配套 Codex Skill 负责安装、配对和日常维护。

- **自动部署**：发现两端环境，复用已有安装，缺少信息时直接询问。
- **断点续跑**：保留部署进度，中断后从未完成的步骤继续。
- **双击启动**：打开远程桌面，登录或重连后自动拉起 Claude。
- **文本互通**：默认双向文本剪贴板，支持按需关闭。

<a id="quick-start"></a>
## 快速开始

### 1. 下载并安装 Skill

下载 [devspace-host-control.zip](https://github.com/wcy-wcy1/devspace-host-control/releases/download/v0.2.0-beta.2/devspace-host-control.zip)，将其中的同名目录放入 Codex skills 目录。Windows 可执行：

```powershell
$skillBase = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'skills' } else { Join-Path $env:USERPROFILE '.codex/skills' }
Expand-Archive -LiteralPath ./devspace-host-control.zip -DestinationPath $skillBase
```

### 2. 告诉 Codex 你的目标

```text
使用 $devspace-host-control，自动部署 Linux Claude Desktop 控制我的 Windows。
先检查现有环境，复用已有配置；缺少必要信息或需要我登录时再问我。
```

Codex 处理安装、Tailscale 策略、证书配对和 MCP 配置。按提示完成 Tailscale / Claude 登录、系统授权与本机限时会话开启即可。

### 3. 选择服务器环境

| 设备 | 支持范围 |
| :--- | :--- |
| **Linux 服务器** | Ubuntu 22.04 / 24.04，amd64 / arm64 |
| **CentOS 原机适配** | CentOS Stream 9，x86_64；需明确选择适配方案 |
| **本机电脑** | Windows amd64，已登录桌面会话 |

Ubuntu 是主要安装路径；Claude Desktop 官方不支持 CentOS，本项目提供兼容适配。运行成品无需 Go SDK、Docker 或 Kubernetes。部署时会按确认的机房地区配置服务器时区。

<a id="one-click"></a>
## 下一次，只需双击

保留配置后生成的**整个启动目录**，双击 **`Start-Claude.cmd`**：

<p align="center">
  <img src="docs/assets/one-click-launch.svg" alt="双击 Start-Claude.cmd → 启动或复用 HostControl → 打开远程桌面 → Linux 登录或重连 → 启动或复用 Claude Desktop" width="100%" />
</p>

已有代理会继续运行；新代理默认暂停，需要操作 Windows 时在本机开启限时会话。启动与移除方法见 [双击启动指南](skills/devspace-host-control/references/launcher.md)。

<a id="architecture"></a>
## 系统架构

[![Windows 经远程桌面使用 Linux Claude，Claude 经 MCP 调用 Windows 工具](docs/assets/architecture.png)](docs/assets/architecture.svg)

| 连接 | 方向 | 用途 |
| :--- | :--- | :--- |
| **远程桌面 · 3389** | Windows → Linux | 打开和使用 Claude Desktop |
| **MCP 工具 · 7443** | Linux → Windows | 通过 HostControl 执行本机操作 |

两条连接使用 Tailscale 私网。`hostagent` 运行在 Windows，`hostmcp` 由 Linux 上的 Claude Desktop 启动，`hostctl` 负责证书配对。

<a id="mcp-tools"></a>
## 18 个 MCP 工具

| 能力 | 可以做什么 |
| :--- | :--- |
| **会话状态** | 查看连接、能力和剩余会话时间 |
| **工作区文件** | 列出、读取、写入和移动文件 |
| **程序任务** | 执行命令、查看结果、取消任务 |
| **桌面观察** | 列出窗口、检查 UI 元素、截图 |
| **鼠标键盘** | 聚焦、点击、输入文字、按键和滚动 |
| **浏览器** | 打开网页，配合桌面工具继续操作 |

<a id="guides"></a>
## 使用指南

[自动化与续跑](skills/devspace-host-control/references/automation.md) · [部署与配对](skills/devspace-host-control/references/operations.md) · [双击启动](skills/devspace-host-control/references/launcher.md) · [故障诊断](skills/devspace-host-control/references/troubleshooting.md) · [时区配置](skills/devspace-host-control/references/timezone.md)

开发与构建入口：[Go 模块](hostcontrol/) · [Skill 源码](skills/devspace-host-control/) · [发布打包](scripts/package.py)。

## 许可

采用 [Apache-2.0](LICENSE)。项目源自对 [DevSpace](https://github.com/loft-sh/devspace) 的源码研究，独立实现主机控制模块；不是 DevSpace 或 Anthropic 的官方产品。出处见 [NOTICE](NOTICE)。
