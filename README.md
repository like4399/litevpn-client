# litevpn-client

基于 [mihomo](https://github.com/MetaCubeX/mihomo)（Clash Meta）的轻量 **VPN / 代理客户端**，面向 Ubuntu 24.04。

导入 Clash / Mihomo 订阅后，通过本机混合代理与 Web 面板选择节点、分流流量。适合 SSH 服务器或无桌面环境：命令行启停，浏览器图形化管理。

> v1 **不开 TUN**，也不改系统默认路由；只有主动走 `127.0.0.1:7890` 的进程才会经节点代理。

## 功能

- 订阅导入与配置合并（`proxies` / `proxy-groups` / `rules`）
- 本机混合代理：`127.0.0.1:7890`（HTTP + SOCKS5）
- Web 面板：[MetaCubeXD](https://github.com/MetaCubeX/metacubexd)（`http://127.0.0.1:9090/ui`）
- 用户级 systemd 启停；可选 Docker 部署（见 [docs/deploy.md](docs/deploy.md)）

## 依赖

目标机需要：`bash`、`curl`、`gzip`、`tar`、`python3`、`systemd --user`（Ubuntu 24.04 默认具备）。

## 安装

```bash
cd litevpn-client
chmod +x litevpn scripts/install.sh
./scripts/install.sh
```

会将 `mihomo`、`litevpn` 安装到 `~/.local/bin`，面板安装到 `~/.local/share/litevpn/ui`。若 PATH 中尚无 `~/.local/bin`：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

无图形界面的 SSH 会话若希望退出后服务仍在，执行一次：

```bash
loginctl enable-linger "$USER"
```

## 快速开始

```bash
litevpn setup
litevpn import '你的订阅链接'
litevpn up
```

也可启动后在 [http://127.0.0.1:9090/ui](http://127.0.0.1:9090/ui) 的「代理 → 代理提供者」导入，或在 [http://127.0.0.1:9090/subs](http://127.0.0.1:9090/subs) 粘贴订阅链接。

面板需要 API secret，执行 `litevpn status` 查看，或：

```bash
cat ~/.config/litevpn/secret
```

**MetaCubeXD「后端地址」**须与浏览器地址栏一致：打开的是 `localhost:9090` 则填 `http://localhost:9090`，打开的是 `127.0.0.1:9090` 则填 `http://127.0.0.1:9090`。

从其他机器访问时，用 SSH 转发后再打开本机 `http://127.0.0.1:9090/ui`：

```bash
ssh -L 9090:127.0.0.1:9090 -p <ssh-port> <user>@<host>
```

## 命令

| 命令 | 作用 |
| --- | --- |
| `litevpn setup` | 创建配置目录、API 密钥、用户 systemd 单元 |
| `litevpn import <url>` | 保存并拉取订阅，合并端口/面板设置，`mihomo -t` 校验 |
| `litevpn update` | 按已存链接再拉一次；服务在跑则热重载 |
| `litevpn up` / `down` | 启动 / 停止（mihomo + 本机 9090 反代） |
| `litevpn status` | 服务状态、Web 地址、secret |
| `litevpn log` / `litevpn log -f` | 查看日志 |
| `litevpn env` | 打印终端代理环境变量 |
| `litevpn help` | 帮助 |

## 如何走代理

混合端口同时提供 HTTP 与 SOCKS5。需要代理的进程任选以下方式。

**当前终端：**

```bash
eval "$(litevpn env)"
```

会导出 `http_proxy` / `https_proxy` / `ALL_PROXY`（指向 `http://127.0.0.1:7890`）。此后该 shell 里认这些变量的命令会走代理。

**单次命令：**

```bash
curl -x http://127.0.0.1:7890 -I https://www.google.com/generate_204
```

**长期环境（写入 `~/.bashrc` 等）：**

```bash
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
export ALL_PROXY=http://127.0.0.1:7890
export no_proxy=localhost,127.0.0.1
```

**systemd 服务：** 在对应 unit 的 `[Service]` 里加 `Environment=`（或 `EnvironmentFile=`），内容同上。

**Docker：** 给容器设置 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`。非 host 网络时，把地址改成能访问到宿主机代理的地址（例如 `http://172.17.0.1:7890`）。

**浏览器：** 扩展或手动代理填 `127.0.0.1:7890`。

远程机器上的进程不要直连公网 `7890`。需要时用 SSH 转发：

```bash
ssh -L 7890:127.0.0.1:7890 -L 9090:127.0.0.1:9090 -p <ssh-port> <user>@<host>
```

## 端口与路径

| 端口 / 路径 | 说明 |
| --- | --- |
| `127.0.0.1:9090` | 本机反代：`/subs` 导入订阅，`/ui` 与 Clash API 转到内核 |
| `127.0.0.1:7890` | 混合代理（HTTP + SOCKS5） |
| `~/.local/bin/mihomo` | 内核 |
| `~/.local/bin/litevpn` | CLI |
| `~/.local/share/litevpn/ui` | metacubexd 静态文件 |
| `~/.local/share/litevpn/pack/web_server.py` | 本机 9090 反代 |
| `~/.config/litevpn/config.yaml` | 合并后的运行配置 |
| `~/.config/systemd/user/litevpn.service` | mihomo 用户服务 |
| `~/.config/systemd/user/litevpn-web.service` | 9090 反代用户服务 |

9090 / 7890 均只绑定本机。远程请用 SSH 转发；安全组只开放 SSH。订阅 URL 存在 `~/.config/litevpn/`（权限 600/700），命令和网页列表不展示完整链接。

## 说明

- 项目名 **litevpn-client**；命令行为 `litevpn`，配置目录为 `~/.config/litevpn`。
- 订阅须为 Clash / Mihomo YAML（或 Base64 编码的 YAML）。
- 合并时会覆盖订阅中的端口、DNS、`external-controller`、`secret`、`external-ui`，并去掉 `tun`，保留 `proxies` / `proxy-groups` / `rules`。
