# Clash for Linux Panel

用 VS Code 可视化管理 Linux 上的 Clash / Mihomo：节点、订阅、内核、终端默认代理和 TUN。

![Clash Panel](ui/media/preview.png)

本项目由两部分组成：**Linux 上的 clashctl / Mihomo 负责代理运行，VS Code 中的 Clash Panel 插件提供管理界面**。源码仓库附带管理脚本和界面源码，Release 提供可以直接安装的 VSIX。

## 安装方式

### 服务器还没有安装 clashctl

先用 VS Code 的 Remote - SSH 连接服务器，然后在该窗口的远程终端执行：

```bash
cd "$HOME"
git clone https://github.com/zkyqust/clash-for-linux-panel.git
cd clash-for-linux-panel
bash setup.sh
```

`git clone` 获取源码；`setup.sh` 负责后续安装，流程是：

**获取发布好的 VSIX → 安装或复用 clashctl / Mihomo → 尝试自动安装 VS Code 插件。**

正常安装使用现成的 VSIX，不需要自行编译界面。服务器需准备 Git、Bash、Python 3.9+、curl 或 wget，以及 tar、gzip、unzip、sha256sum；首次安装需要联网下载。

插件自动安装成功后，重新加载 VS Code 窗口即可。`setup.sh` 使用 `code --install-extension` 安装插件；如果找不到 `code` 命令或这一步失败，按下一节手动安装 VSIX，已安装好的 clashctl 可以继续使用。

### 已有 clashctl，或需要手动安装插件

已有 clashctl 时只需安装界面，不必重新运行 `setup.sh`。如果上一步已自动装好插件，也无需重复手动安装。

1. 下载 [clashctl-panel-0.3.4.vsix](https://github.com/zkyqust/clash-for-linux-panel/releases/download/v0.3.4/clashctl-panel-0.3.4.vsix)。如果 `setup.sh` 已准备好 VSIX，直接使用它打印的文件路径。
2. 在 VS Code 的 Remote SSH 窗口，打开 **扩展 → … → 从 VSIX 安装**，选择文件并安装到 **SSH 主机**。文件选择器只显示服务器文件时，先将下载的 VSIX 上传服务器。
3. 按 `Ctrl+Shift+P`，执行 **Developer: Reload Window（重新加载窗口）**，然后打开左侧 Clash Panel。
4. 在 **连接设置** 中选择“当前 Linux / Remote SSH 窗口”，填写 clashctl 的实际安装目录，默认是 `~/clashctl`。

VS Code 需要 1.96+。Windows 本地窗口也可安装同一个 VSIX，通过面板的“从本机通过 SSH 连接服务器”管理，需提前配置 SSH 密钥或 SSH Agent。

## 安装目录和可选参数

| 位置 | 用途 |
| --- | --- |
| `~/clash-for-linux-panel` | 下载的源码，提供 `setup.sh` 等安装文件 |
| `~/clashctl` | 默认运行目录，保存管理脚本、内核、订阅和配置；面板连接这里 |
| `~/clashctl/ui` | 安装时复制的界面源码；VSIX 则由 VS Code 安装到自己的扩展目录 |

安装正常后，源码目录可以删除；运行目录需要保留。`~` 和 `$HOME` 都表示当前 Linux 用户的主目录。

默认直接运行 `bash setup.sh` 即可。需要换运行目录时才使用 `--home`，例如：

```bash
bash setup.sh --home "$HOME/my-clash"
```

随后把面板的连接目录也设为 `~/my-clash`。已有安装时可指定原目录，脚本会复用选定目录中的完整 clashctl，不会给目录改名或搬迁。目录选择优先使用 `--home`，其次是环境变量 `CLASHCTL_HOME`，都未设置时使用 `~/clashctl`。

如果已经下载好同版本的 VSIX，可以通过 `--vsix` 使用本地文件，跳过插件包下载：

```bash
bash setup.sh --vsix "$HOME/clashctl-panel-0.3.4.vsix"
```

`--vsix` 只提供界面插件；首次安装缺少的内核和工具仍需下载。

## 服务器无法直接访问 GitHub：使用下载加速

服务器能访问 [GH-Proxy](https://gh-proxy.com/docs/github-accelerator) 时，可在 Remote SSH 远程终端执行：

```bash
export GH_PROXY="https://gh-proxy.com"
cd "$HOME"
git clone "$GH_PROXY/https://github.com/zkyqust/clash-for-linux-panel.git"
cd clash-for-linux-panel

curl -fL --retry 2 \
  "$GH_PROXY/https://github.com/zkyqust/clash-for-linux-panel/releases/download/v0.3.4/clashctl-panel-0.3.4.vsix" \
  -o "$HOME/clashctl-panel-0.3.4.vsix"

bash setup.sh --vsix "$HOME/clashctl-panel-0.3.4.vsix"
```

这套命令通过加速地址下载源码和 VSIX；安装器再读取 `GH_PROXY` 下载内核及工具。VSIX 下载器本身不读取 `GH_PROXY`，因此这里先用 `curl` 下载，再通过 `--vsix` 传入。`GH_PROXY` 是下载地址前缀，与终端代理和 TUN 开关独立。

已有源码时进入原目录更新即可，无需重复克隆。如果加速服务也无法访问，可以先在其他电脑下载源码包和 VSIX 后上传；首次安装所需的内核及工具还需另行准备。

## 从源码构建（开发者可选）

修改界面源码后，在仓库根目录执行：

```bash
python3 ui/tools/package.py
```

Windows 可用 `py` 代替 `python3`，也可执行 `bash setup.sh --build-only`。生成文件为 `ui/build/clashctl-panel-0.3.4.vsix`。这个步骤只构建插件，不安装内核；普通安装直接使用 Release 的 VSIX 即可。

## 第一次打开面板

1. **连接设置**：Remote SSH 窗口选择当前 Linux，目录默认 `~/clashctl`。Windows 本地窗口可通过 SSH 管理，需要预先配置密钥或 SSH Agent。
2. **订阅管理**：添加自己的链接或导入自己的 YAML，保存后点击“启用”。本项目不提供节点或订阅。
3. **概览**：启动内核，在“路由模式”选择规则、全局或直连，按需开启终端默认代理。
4. **代理节点**：规则模式下，为规则使用的策略组选择节点；全局模式下，选择 GLOBAL 组的出口。可单独或整组测延迟。

订阅下载被服务商拒绝时，可以先在可下载的机器保存 YAML，再导入。界面不会通过旧节点临时代理下载订阅。

## 三个独立开关

| 开关 | 作用 |
| --- | --- |
| 内核服务 | 启动/停止 Mihomo，提供代理监听和流量转发 |
| 终端默认代理 | 保存当前用户的默认设置，新开的 Bash/Zsh 终端读取当前代理地址 |
| TUN 虚拟网卡 | 通过虚拟网卡和路由接管流量，使用上游 TUN 设置 |

终端默认代理开关不启动或停止内核。代理连接和 TUN 实际生效需要内核运行。TUN 需要 `/dev/net/tun`、iproute2 和网络管理权限；Docker 中需要管理员提供设备及 CAP_NET_ADMIN。

节点选择、订阅、模式、端口和 TUN 沿用已有保存机制。终端默认代理额外安装当前用户的 Bash/Zsh 加载段，不修改 `/etc`，保存默认代理不需要 root。

## 路由模式：规则、全局、直连

内核运行后，用户可以在 **概览 → 路由模式** 中选择以下任一模式。这三种模式决定的是**已经进入 Clash 的请求**如何访问目标网站。

| 模式 | 流量如何处理 | 用户如何选择出口 |
| --- | --- | --- |
| 规则（rule） | 按当前配置的规则分流，结果可以是直连、使用代理节点或拒绝连接，具体取决于规则内容。 | 在“代理节点”中，为规则使用的策略组选择节点；策略组名称由订阅配置决定。 |
| 全局（global） | 统一使用 GLOBAL 策略组当前选择的出口。 | 在“代理节点”中选择 GLOBAL 组，再选择节点或其他可用出口；如果选的是另一个策略组，则继续使用该组的选择结果。 |
| 直连（direct） | 使用 DIRECT 出口，通过服务器本身的网络访问目标，不使用订阅中的代理节点。 | 无需选择代理节点；保留的节点选择可以在切回规则或全局模式后使用。 |

例如：规则模式可以按配置让部分网站直连、部分网站经过代理；全局模式下，GLOBAL 直接选择日本节点时，请求经过该日本节点；直连模式下，请求使用服务器本身的网络。

“终端默认代理”让新终端中支持代理环境变量的程序连接 Clash 入口；请求进入内核后，再由所选路由模式处理。TUN 接管的请求也按照所选路由模式处理。

模式切换会自动保存，重启内核或切换订阅后继续保留。选择节点与切换模式是两个独立操作，用户可以按需选择；选择节点不会自动切换路由模式。

## 源码结构

```text
install.sh / uninstall.sh   上游 clashctl 安装与卸载器
scripts/                   上游管理脚本
resources/                 无个人配置的上游模板
ui/                        扩展、Python 桥接、构建工具和测试源码
setup.sh                   整体安装入口，使用发布版或本地 VSIX
UPSTREAM.json              上游来源和固定提交
UPSTREAM_README.md         上游原始说明
```

运行后产生的 data、bin、archives、.env 和构建产物均被 Git 忽略。Geo 数据文件和 Mihomo 二进制不在源码包内，由安装器或内核在需要时获取。

## 开发与测试

见 [界面开发说明](ui/README.md)。测试使用临时目录，覆盖节点测试、终端默认开关、全新 Bash、动态端口读取和写入失败恢复；隔离内核测试检查原有模式、节点和订阅持久化。

## 来源与许可

- 管理脚本来自 [nelvko/clashctl](https://github.com/nelvko/clashctl)，MIT License，保留上游版权。固定提交见 UPSTREAM.json。
- 界面与桥接使用 MIT License，见 [ui/LICENSE](ui/LICENSE)。
- 实际运行内核为 [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo)，由上游安装器下载，遵循其自身许可。

本项目不隶属于上述项目或微软。请勿在 Issues、截图或 Pull Request 中上传订阅 URL、节点密码、控制密钥或账号信息。
