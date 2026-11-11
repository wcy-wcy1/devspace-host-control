# 安装实测后的可靠性改进

日期：2026-11-11。基于一次 Windows + CentOS Stream 9 原机部署的故障与验收结果。
这里不记录设备地址、账号、密码、登录链接、控制令牌、证书或用户截图。

## 最终行为

- **默认开启双向文字剪贴板**。服务器与生成的 `.rdp` 文件采用同一默认值；可用 `--clipboard disabled` 关闭。剪贴板文件/图片、磁盘、设备和音频共享仍关闭。文字模式要求支持格式限制的 xrdp（本项目保守要求 >=0.9.24），不会在旧版本上静默开放所有格式。
- 修改 xrdp 配置时保留注释、换行风格、其它设置，以及 `[Xorg]`、`[Xvnc]` 中每一行重复 `param=` 的顺序。检测到目标设置歧义或 Xorg 参数已损坏时拒绝写入。
- 先预览再显式应用；保存备份，保留权限、所有者及支持的文件元数据，原子替换单个文件。第二个文件写入失败时尝试回滚自己的第一处写入，遇到外部修改则保留备份并报告人工处理。不会自动重启服务或注销桌面。
- 增加 CentOS Stream 9 x86_64 安装适配。核对官方 Claude 签名密钥、仓库签名、索引及包 SHA256；补齐图形、中文字体、URL 处理和密钥环 PAM 依赖。Claude 官方仍不支持 CentOS，此脚本是兼容适配。
- CentOS 认证使用 `auth substack password-auth`，使成功认证继续执行密钥环步骤。正确创建用户配置目录的中间层，避免 root 所有的 `.config`、`.local` 导致 Claude 无法取得用户数据目录。
- 提供可见的 Windows 前台启动脚本，默认暂停。显式本机限时启用受配置上限约束；PowerShell 7 的模块路径不再传给 Windows PowerShell UIA 子进程。
- 增加只读故障分类：区分认证失败、Xorg 参数损坏、X server 启动失败及剪贴板设置问题，不把旧失败当作最新故障，不返回原始日志中的潜在凭据。

## 为什么必须保留重复参数

现场修改剪贴板后，通用 INI 写入器将 `[Xorg]` 的多行 `param=` 合并为最后一个值。
认证虽然成功，程序却把 `.xorgxrdp.%s.log` 当成可执行文件，因此无法创建桌面。
恢复全部启动参数后，真实日志确认 Xorg 和 XFCE 会话重新启动成功。

`desktop_config.py` 现在只替换明确指定的行；测试夹具包含真实格式的八个 Xorg 参数
和多个 Xvnc 参数。一次剪贴板切换必须保持整个后续参数段逐字节不变，才算通过。

## 工具入口

| 功能 | 文件 |
| --- | --- |
| 保留参数的配置、诊断及 `.rdp` 生成 | `skills/devspace-host-control/scripts/desktop_config.py` |
| Ubuntu 安装 | `skills/devspace-host-control/scripts/ubuntu-desktop.sh` |
| CentOS 原机适配 | `skills/devspace-host-control/scripts/centos-desktop.sh` |
| XFCE 与密钥环初始化 | `skills/devspace-host-control/scripts/linux_session.py` |
| Windows 代理前台启动 | `skills/devspace-host-control/scripts/start-windows-agent.ps1` |
| 安装排错、剪贴板切换、Claude 访问测试 | `skills/devspace-host-control/references/troubleshooting.md` |

## 验证边界

此前现场确认过：真实 RDP 登录、Claude 欢迎页面及用户账号登录、Claude MCP 连接日志、
全部 18 个适配器工具、Windows 文件新建/读取/哈希更新/备份、越界拒绝、命令执行和
窗口截图。实际输入遇到 Windows 焦点保护或帧过期而被拒绝；不将它计为键鼠输入成功。

本次新增自动化覆盖：参数保留、文字默认值、双端重定向、无副作用预览、幂等更新、
备份/部分失败回滚、并发修改拒绝、故障分类、PAM 顺序和 Windows 启动器的授权/环境行为。
这些测试不控制用户桌面，也不需要账号密码。

本次实际检查结果：

| 环境/检查 | 结果 |
| --- | --- |
| Windows，Python 3.14 | 28 项测试全部通过，包括 3 项假代理启动器测试 |
| CentOS Stream 9，Python 3.9，隔离目录 | 25 项通过，3 项 Windows 专属测试跳过，无失败 |
| 两个 Bash 安装器 | 语法和不传剪贴板参数的默认预览通过，均显示 `Clipboard=text` |
| PowerShell 启动器 | 语法解析通过 |
| 原有运行程序与源码镜像 | 6 个程序的哈希/架构通过，32 个源码文件镜像一致 |
| 运行中桌面，只读检查 | Xorg 参数有效、无已分类故障，版本门槛及内存转换保留参数检查通过 |

新脚本在现有 CentOS 主机的隔离临时目录内运行离线测试、Bash 语法与预览检查；
针对实际运行配置只做只读诊断、版本检查和纯内存参数保留检查，不重新安装或重启桌面。
完整 Ubuntu/CentOS 全新安装、下次登录的密钥环行为、剪贴板格式拒绝和实际键鼠操作
仍需专门现场验收。没有修改 Go 模块或六个运行程序；未重新运行 Go 编译/测试。

原始打包记录保留于 `assets/build-evidence.md`，不把后续现场结果写成原始构建时已通过。
