# Clash for Linux Panel

用 VS Code 可视化管理 Linux 上的 Clash / Mihomo：节点、订阅、内核、终端默认代理和 TUN。

![Clash Panel](ui/media/preview.png)

根目录是 **clashctl 管理脚本**，`ui/` 是 **VS Code 扩展源码**。这是源码仓库，不包含个人订阅、真实服务器地址、账号、运行配置、内核二进制、日志或测试缓存。GitHub Release 另外提供打包好的 **VSIX 插件**，用户下载后即可安装，无需自行构建。

## 直接下载 VSIX 安装（推荐）

前提：VS Code 1.96+；被管理的 Linux 需要 Bash、Python 3.9+ 和兼容版本的 clashctl。VSIX 是界面插件，Mihomo 内核运行在 Linux 上。

1. 下载 [clashctl-panel-0.3.3.vsix](https://github.com/zkyqust/clash-for-linux-panel/releases/download/v0.3.3/clashctl-panel-0.3.3.vsix)，或从 [Releases](https://github.com/zkyqust/clash-for-linux-panel/releases/latest) 选择最新版本的 `.vsix` 文件。下载后保持 `.vsix` 后缀，无需解压。
2. 在 Windows 的 VS Code 中安装 **Remote - SSH**，连接需要管理的 Linux 服务器。
3. 在该 Remote SSH 窗口中打开 **扩展 → 右上角 … → 从 VSIX 安装**，选择下载的文件，并确认安装到 **SSH 主机**。如果文件选择器只显示服务器文件，先把 VSIX 上传服务器，再选择它。
4. 按 `Ctrl+Shift+P`，执行 **Developer: Reload Window（重新加载窗口）**，然后打开左侧 **Clash Panel**。
5. 打开面板的 **连接设置 → 当前 Linux / Remote SSH 窗口**，填写 clashctl 的实际安装目录。新安装默认 `~/clashctl`；已有安装位于其他目录时填写原路径。

已有 clashctl 的用户完成以上步骤即可管理原安装，不必下载源码或重新构建插件。通常可以在服务器终端执行 `echo "$CLASHCTL_HOME"` 查看原安装位置。

Windows 本地窗口也可以安装同一个 VSIX，通过面板“连接设置 → 从本机通过 SSH 连接服务器”管理；这种方式需要提前配置 SSH 密钥或 SSH Agent。

## 服务器还没有安装 clashctl

在 VS Code 的 **Remote SSH 远程终端**执行以下命令。首次安装需要联网下载 Mihomo 内核、依赖和发布好的 VSIX；服务器需要已有 Git、Bash 和 Python 3.9+。

```bash
git clone https://github.com/zkyqust/clash-for-linux-panel.git
cd clash-for-linux-panel
bash setup.sh
```

新安装默认放到 `~/clashctl`，面板默认连接路径也为 `~/clashctl`。脚本会检查选定目录中的安装，已有完整 clashctl 时沿用原程序、订阅和配置；否则调用本项目附带的 clashctl 安装器。界面源码复制到安装目录的 `ui/`，插件直接使用 GitHub Release 发布的 VSIX，不在正常安装过程中构建源码。

已有 `CLASHCTL_HOME` 环境变量时沿用其指向的目录；也可通过 `--home` 明确指定。复用安装不会给原目录改名或自动搬迁，也不会搜索服务器上的所有目录。

没有 `code` 命令或自动安装失败时，在 VS Code 扩展页右上角 `…` → **从 VSIX 安装**，选择脚本打印的 VSIX 路径。Remote SSH 窗口应安装到 **SSH 主机**。最后执行 **Developer: Reload Window**，打开左侧 Clash Panel。

指定安装目录：

```bash
bash setup.sh --home "$HOME/clashctl"
```

自定义目录后，在面板“连接设置”中选择相同路径。`~/clash-for-linux-panel` 是下载的源码目录，`~/clashctl` 是默认的运行安装目录；确认安装正常后，源码目录可以删除。

如果已经在电脑上下载好同版本 VSIX，可以上传服务器后指定该文件，跳过界面插件的联网下载：

```bash
bash setup.sh --vsix "$HOME/clashctl-panel-0.3.3.vsix"
```

`--vsix` 只提供界面安装包；尚未安装的 Mihomo 内核和依赖仍需要由安装器获取。

## 服务器无法直接访问 GitHub：使用下载加速

服务器能够访问 `https://gh-proxy.com` 时，可以使用下面的完整命令。根据 [GH-Proxy 使用文档](https://gh-proxy.com/docs/github-accelerator)，在 GitHub 地址前加上该服务的前缀，可加速公开仓库和 Release 文件下载。

在 Linux / Remote SSH 终端中逐行执行：

```bash
# 内核和工具的安装器会读取这个 GitHub 下载加速地址
export GH_PROXY="https://gh-proxy.com"

# 通过加速地址下载源码
cd "$HOME"
git clone "$GH_PROXY/https://github.com/zkyqust/clash-for-linux-panel.git"
cd clash-for-linux-panel

# 通过加速地址下载现成的界面插件
curl -fL --retry 2 \
  "$GH_PROXY/https://github.com/zkyqust/clash-for-linux-panel/releases/download/v0.3.3/clashctl-panel-0.3.3.vsix" \
  -o "$HOME/clashctl-panel-0.3.3.vsix"

# 使用本地 VSIX；内核和工具经 GH_PROXY 下载，不重新构建界面
bash setup.sh --vsix "$HOME/clashctl-panel-0.3.3.vsix"
```

其中 `$HOME` 表示当前 Linux 用户的主目录，例如 `/home/alice`；VSIX 会先保存到这个目录。`GH_PROXY` 是 GitHub 下载地址前缀，与 HTTP_PROXY、系统代理和 TUN 开关独立。

三处下载分别处理：`git clone` 使用加速链接；VSIX 用 `curl` 提前下载并通过 `--vsix` 传入；Mihomo 内核和 yq 等工具由安装器读取 `GH_PROXY` 后下载。当前版本的 VSIX 下载器不会自动读取 `GH_PROXY`，所以这套命令需要保留 `curl` 和 `--vsix` 两步。

如果已经下载过源码，不要重复克隆；可以在原源码目录更新后继续安装：

```bash
cd "$HOME/clash-for-linux-panel"
export GH_PROXY="https://gh-proxy.com"
git remote set-url origin "$GH_PROXY/https://github.com/zkyqust/clash-for-linux-panel.git"
git pull --ff-only
```

更新后，继续执行上面的 `curl` 和 `bash setup.sh --vsix ...` 两步。加速服务仍需可访问；如果服务器连加速服务也无法访问，可在电脑上下载源码包和 VSIX 后上传，首次内核及依赖的获取仍需另行解决。

## 从源码构建（开发者可选）

普通用户直接下载 VSIX 即可。开发者修改源码后，可以只生成 VSIX，不操作内核：

```bash
python3 ui/tools/package.py
```

Windows 可使用 `py ui/tools/package.py`。生成文件为 `ui/build/clashctl-panel-0.3.3.vsix`，通过“从 VSIX 安装”安装。

也可执行 `bash setup.sh --build-only`。构建工具只依赖 Python 3，不需要 npm 或 JavaScript 编译器。源码本身不能直接作为 VSIX 安装，构建脚本负责生成 VS Code 所需的安装包。

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

## 目录

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
