# Clash Panel · VS Code 可视化管理

在 VS Code 中管理 Linux clashctl / Mihomo，支持 Remote SSH 和本机通过 SSH 管理服务器。

![界面预览](media/preview.png)

## 安装界面

**服务器已安装 clashctl**：下载 [clashctl-panel-0.3.4.vsix](https://github.com/zkyqust/clash-for-linux-panel/releases/download/v0.3.4/clashctl-panel-0.3.4.vsix)，按下面的步骤安装插件即可。

**服务器尚未安装 clashctl**：按[仓库安装说明](https://github.com/zkyqust/clash-for-linux-panel#安装方式)运行 `setup.sh`。它会安装 clashctl / Mihomo，并尝试自动安装插件，正常安装不编译界面。

`setup.sh` 已自动装好插件时，重新加载窗口即可。如果服务器没有 `code` 命令或插件自动安装失败，再手动安装 VSIX：

1. 在 VS Code 的 Remote SSH 窗口打开 **扩展 → … → 从 VSIX 安装**，选择下载的 VSIX，或脚本打印路径中的文件，安装到 **SSH 主机**。文件选择器只显示服务器文件时，可先上传 VSIX。
2. 按 `Ctrl+Shift+P`，执行 **Developer: Reload Window（重新加载窗口）**，然后打开左侧 Clash Panel。
3. 在“连接设置”中选择“当前 Linux / Remote SSH 窗口”，填写 clashctl 的实际运行目录，默认 `~/clashctl`。

VSIX 提供管理界面，clashctl / Mihomo 运行在 Linux 上。`setup.sh --home` 选择的是 clashctl 运行目录，面板应填写同一个路径；VSIX 由 VS Code 管理安装位置。

需要 VS Code 1.96+，目标 Linux 需要 Bash 和 Python 3.9+。首次安装、复用已有目录、GitHub 下载加速及开发构建命令统一见[仓库 README](https://github.com/zkyqust/clash-for-linux-panel#readme)。

## 首次使用

1. 打开左侧 Clash Panel，在“连接设置”选择当前 Linux / Remote SSH 窗口，安装目录默认 `~/clashctl`。
2. 在“订阅管理”添加自己的链接，或导入自己的 YAML，保存后点击“启用”。
3. 在“概览”启动内核，在“路由模式”选择规则、全局或直连，按需开启“终端默认代理”。
4. 在“代理节点”为规则使用的策略组或全局模式的 GLOBAL 组选择节点，选择会保存；直连模式无需选择代理节点。

Windows 本地窗口也可以选择“通过 SSH”，填写主机别名或 user@host 和端口。需要提前配置密钥或 SSH Agent，并确认过主机指纹；插件不保存 SSH 密码。

## 三个独立控制

| 控制 | 行为 |
| --- | --- |
| 内核服务 | 启动或停止 Mihomo，不切换终端默认代理 |
| 终端默认代理 | 保存当前用户的默认设置，新开的 Bash/Zsh 终端读取当前代理端口，不启动或停止内核 |
| TUN 虚拟网卡 | 复用 clashctl 的 TUN 功能，沿用原有的配置保存机制 |

终端默认代理的开关保存在 `data/panel-terminal-proxy.json`，启动脚本保存在 `data/terminal-proxy.py` 和 `data/terminal-proxy.sh`。Bash/Zsh 启动文件中安装带标记的加载段。每次新开终端都会读取 `data/runtime.yaml`，不写死端口；关闭后新终端清除对应代理变量。写入前备份，失败时尝试恢复，备份位于 `data/backups/panel-terminal-*`。

HTTP/HTTPS 优先使用有效独立 HTTP 端口，否则用混合端口；SOCKS 优先使用有效 SOCKS 端口，否则用混合端口。同时设置大小写代理变量，并让本机地址绕过代理。

TUN 需要 `/dev/net/tun`、iproute2 和网络管理权限。Docker 中需要管理员提供设备和 CAP_NET_ADMIN；界面检测并显示缺失条件。代理转发和 TUN 实际生效需要内核运行。

## 路由模式：规则、全局、直连

用户可以在 **概览 → 路由模式** 中切换以下三种模式。处理范围是**已经进入 Clash 的请求**。

| 模式 | 流量如何处理 | 节点选择 |
| --- | --- | --- |
| 规则（rule） | 按当前配置中的规则分流，可使用 DIRECT 直连、代理策略组或拒绝连接。具体行为取决于规则内容。 | 为规则使用的策略组选择节点，组名以当前订阅为准。 |
| 全局（global） | 统一使用 GLOBAL 策略组当前选择的出口。 | 在“代理节点”选择 GLOBAL，再选择节点或其他可用出口；如果选的是另一个策略组，则继续使用该组的选择结果。 |
| 直连（direct） | 使用 DIRECT 出口，通过服务器本身的网络访问目标，不使用订阅中的代理节点。 | 无需选择代理节点，原有选择继续保存。 |

“终端默认代理”决定新终端中支持代理环境变量的程序是否连接 Clash 入口；路由模式决定请求进入内核后使用哪个出口。TUN 接管的请求同样由所选路由模式处理。

模式切换会保存，重启内核或切换订阅后仍保留。选择节点与切换模式互相独立，选择节点不会自动切换路由模式。

## 界面功能

- **概览**：三个独立开关、路由模式和当前订阅。
- **设置**：管理连接与 HTTP / 混合 / SOCKS 端口，不重复显示概览开关。
- **代理节点**：搜索、选择、单节点或整组延迟测试。测试不切换节点；失败可以重试，悬停失败结果可查看原因。测试目标为 Google 连通性地址，超时 5 秒，结果为 ms，不是下载带宽。
- **订阅管理**：添加链接、导入 YAML、启用、更新和删除，支持删除使用中的订阅。添加只保存，不自动切换。
- **活动连接 / 运行日志**：读取连接及最近 120 行日志，凭据会被隐藏。
- **新建服务器终端**：打开遵循默认代理设置的普通服务器 Bash 终端。

## 原有配置继续保存

模式、端口和 TUN 使用 clashctl 的 `data/mixin.yaml`。当前订阅复用 `data/profiles.yaml`。首次手动选节点时启用 Mihomo 的 `profile.store-selected`，以后由内核缓存保存选择。

端口不允许重复，0 表示关闭监听。应用后确认实际监听，失败时尝试恢复。停止内核、删除订阅和 TUN 切换按操作内容进行确认。

## 隐私与开发

控制接口只从服务器回环地址访问，无需向公网开放 9090。控制密钥、订阅完整链接、代理认证信息不进入 Webview。项目没有遥测，不包含用户订阅和真实服务器地址。

```bash
node --check src/extension.js
node --check src/backend.js
node --check media/app.js
node --test tests/backend.test.js tests/controller.test.js
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tools/package.py
```

测试使用临时目录。`tests/live_core.py` 在有 Mihomo/yq 的 Linux 上启动独立内核，不操作正在运行的内核配置。UI 测试通过 PLAYWRIGHT_MODULE 指定 Playwright 位置。

MIT License。来源与许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
