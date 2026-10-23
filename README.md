# Clash for Linux Panel

在 VS Code 中管理 Linux 上的 Clash / Mihomo，支持订阅、节点选择、延迟测试、内核服务、终端默认代理和 TUN。

![Clash Panel](ui/media/preview.png)

Linux 上的 clashctl / Mihomo 负责代理运行，VS Code 插件提供管理界面。

## 安装方式

### 服务器尚未安装 clashctl

先用 VS Code 的 **Remote - SSH** 连接服务器，在远程终端执行：

```bash
cd "$HOME"
git clone https://github.com/zkyqust/clash-for-linux-panel.git
cd clash-for-linux-panel
bash setup.sh
```

脚本会下载发布好的 VSIX，安装 clashctl / Mihomo，并尝试将插件安装到当前 VS Code 远程环境，无需编译界面。首次安装需要联网，服务器需有 Git、Bash、Python 3.9+、curl 或 wget，以及 tar、gzip、unzip、sha256sum。

完成后按 `Ctrl+Shift+P`，执行 **Developer: Reload Window（重新加载窗口）**，打开左侧 Clash Panel。

如果脚本提示手动安装插件，按下一节操作，VSIX 路径会在终端中显示。

### 已有 clashctl，或手动安装界面

1. 打开[下载页面](https://github.com/zkyqust/clash-for-linux-panel/releases/latest)，下载 `.vsix` 安装包。
2. 在 VS Code 的 Remote SSH 窗口，进入 **扩展 → … → 从 VSIX 安装**，选择安装包，安装到 **SSH 主机**。文件选择器只显示服务器文件时，先将 VSIX 上传到服务器。
3. 执行 **Developer: Reload Window（重新加载窗口）**，打开 Clash Panel。
4. 在 **连接设置** 中选择“当前 Linux / Remote SSH 窗口”，填写 clashctl 的实际安装目录，默认是 `~/clashctl`。

已有 clashctl 时只需安装界面；脚本已自动安装插件时，也无需重复安装。需要 VS Code 1.96+，目标 Linux 需要 Bash 和 Python 3.9+。

Windows 本地窗口也可以安装同一个 VSIX，通过面板的 SSH 连接管理服务器，需要预先配置 SSH 密钥或 SSH Agent。

## 安装目录

| 目录 | 用途 |
| --- | --- |
| `~/clash-for-linux-panel` | 下载的源码和安装脚本；确认安装正常后可以删除 |
| `~/clashctl` | 默认运行目录，保存管理脚本、内核、订阅和配置；面板连接此目录 |

`~` 和 `$HOME` 表示当前 Linux 用户的主目录。

指定其他运行目录：

```bash
bash setup.sh --home "$HOME/my-clash"
```

面板的连接目录也填写 `~/my-clash`。已有安装时可指定原目录，脚本会复用其中的 clashctl、订阅和配置，目录不会改名或搬迁。目录选择优先使用 `--home`，其次是环境变量 `CLASHCTL_HOME`，均未设置时使用 `~/clashctl`。

已下载 VSIX 时，可以上传到服务器并命名为 `clashctl-panel.vsix`，再指定本地安装包：

```bash
bash setup.sh --vsix "$HOME/clashctl-panel.vsix"
```

该参数跳过插件包下载；首次安装需要的 Mihomo 内核和工具仍由安装器下载。

## 服务器无法直接访问 GitHub

先在可以访问 GitHub 的电脑上，从[下载页面](https://github.com/zkyqust/clash-for-linux-panel/releases/latest)下载 VSIX，上传到服务器的主目录，并命名为 `clashctl-panel.vsix`。

然后在服务器终端执行：

```bash
export GH_PROXY="https://gh-proxy.com"
cd "$HOME"
git clone "$GH_PROXY/https://github.com/zkyqust/clash-for-linux-panel.git"
cd clash-for-linux-panel
bash setup.sh --vsix "$HOME/clashctl-panel.vsix"
```

源码和内核所需的 GitHub 下载使用加速地址，插件使用已上传的文件。`GH_PROXY` 只用于下载，与终端默认代理和 TUN 开关独立。

如果服务器也无法访问加速地址，可在电脑上下载[源码 ZIP](https://github.com/zkyqust/clash-for-linux-panel/archive/refs/heads/main.zip)和 VSIX，上传并解压源码，进入包含 `setup.sh` 的目录后执行安装命令。首次安装所需的内核和工具仍需有可用的下载来源。

## 开始使用

1. **连接设置**：选择当前 Linux / Remote SSH 窗口，填写 clashctl 运行目录。
2. **订阅管理**：添加自己的订阅链接或导入 YAML，保存后点击“启用”。
3. **概览**：启动内核，选择路由模式，按需开启终端默认代理或 TUN。
4. **代理节点**：选择策略组和节点，可对单个节点或整组测延迟；测延迟不会切换节点。

订阅、节点选择、路由模式、端口和 TUN 设置会保存。订阅链接无法下载时，可先在其他电脑保存 YAML，再上传导入。本项目不提供订阅或节点。

## 内核、终端默认代理和 TUN

| 控制 | 作用 |
| --- | --- |
| 内核服务 | 启动或停止 Mihomo，提供代理端口和流量转发 |
| 终端默认代理 | 为当前 Linux 用户保存默认设置，新开的 Bash/Zsh 终端中支持代理环境变量的程序使用相应代理端口 |
| TUN 虚拟网卡 | 通过虚拟网卡和路由接管流量 |

三个开关独立控制。终端默认代理不会自动启停内核；代理转发和 TUN 实际生效需要内核运行。

TUN 需要 `/dev/net/tun`、iproute2 和网络管理权限。在 Docker 中，需要管理员为容器提供 TUN 设备和 `CAP_NET_ADMIN` 权限。

## 路由模式

在 **概览 → 路由模式** 中选择模式。它决定已经进入 Clash 的请求如何访问目标。

| 模式 | 处理方式 | 如何选择出口 |
| --- | --- | --- |
| 规则 | 按订阅配置的规则分流，可直连、走代理或拒绝连接 | 在“代理节点”为规则使用的策略组选择节点 |
| 全局 | 使用 GLOBAL 策略组当前选择的出口 | 选择 GLOBAL 组，再选择节点或其他出口；选择其他策略组时继续使用该组的选择结果 |
| 直连 | 使用 DIRECT，通过服务器本身的网络访问目标 | 无需选择代理节点 |

终端默认代理决定程序是否连接 Clash 入口，路由模式决定进入内核后的出口。TUN 接管的请求也按所选模式处理。选择节点与切换模式互相独立。

## 项目来源

管理脚本来自 [nelvko/clashctl](https://github.com/nelvko/clashctl)，代理内核为 [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo)。项目许可见 [LICENSE](LICENSE) 和 [界面许可](ui/LICENSE)。
