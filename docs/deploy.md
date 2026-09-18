# 容器化部署

litevpn-client-linux 打成 **一个轻量镜像**（mihomo 内核 + 9090 反代/订阅导入），用 Compose 跑。不依赖数据库。面板和代理默认只绑宿主机 **127.0.0.1**，远程请 SSH 转发。

## 需要的服务

| 容器 | 说明 |
|------|------|
| `litevpn-client-linux` | 唯一业务容器：内部 mihomo（7890 / 19090）+ `web_server.py`（9090） |

19090 只在容器内给面板用，不映射到宿主机。

## 目录（测试机）

```
/opt/deploy/litevpn-client-linux/
  Dockerfile
  docker-compose.yml
  docker/entrypoint.sh
  docker/mihomo          # 构建时打进镜像
  docker/ui/             # metacubexd 静态资源
  pack/
  data/config/litevpn/   # 持久化：secret、config.yaml、订阅
```

## 构建与启动

在 Linux 上（需 Docker 与 Compose v2）：

```bash
cd /opt/deploy/litevpn-client-linux
# 若 docker/mihomo、docker/ui 不存在，从已有安装拷贝或按 scripts/install.sh 同源下载
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=80
```

端口（仅本机）：

- `127.0.0.1:9090` — 面板 `/ui`、订阅 API
- `127.0.0.1:7890` — HTTP + SOCKS 混合代理

不要把这两端口放到云安全组。

## 远程访问

```bash
ssh -p <ssh端口> -L 9090:127.0.0.1:9090 -L 7890:127.0.0.1:7890 <user>@<host>
```

浏览器：http://127.0.0.1:9090/ui （后端地址填 `http://127.0.0.1:9090`）。

本机测代理：

```bash
curl.exe -x http://127.0.0.1:7890 -I https://www.google.com/generate_204
```

在服务器本机：

```bash
curl -x http://127.0.0.1:7890 -I https://www.google.com/generate_204
```

面板左上角用 **规则 / 直连 / 全局** 开关代理；**直连 = 关闭走节点**。

## 从 systemd 迁移

1. `systemctl disable --now litevpn litevpn-web`
2. 将原配置目录拷到 `data/config/litevpn/`（含 `secret`、`config.yaml`、`subscription.yaml`、`providers/`）
3. `chown -R 1000:1000 data/config`
4. `docker compose up -d --build`

入口脚本会把 `bind-address` 改成 `0.0.0.0`（供容器端口映射），`external-ui` 改成镜像内面板路径。`secret` 与节点配置会保留。

## 常用命令

```bash
cd /opt/deploy/litevpn-client-linux
docker compose ps
docker compose logs -f
docker compose restart
docker compose down          # 停止（数据卷还在 ./data）
docker compose up -d --build # 改代码后重建
```

## 阿里云 ACR

测试机构建后已推送：

```
crpi-xjld0ipmc9tve2uz.cn-chengdu.personal.cr.aliyuncs.com/xiaoliu_111/litevpn-client-linux:v1.2
```

个人版 ACR 不接受 Buildx attestation。推送前需：

```bash
docker build --provenance=false --sbom=false \
  -t litevpn-client-linux:latest \
  -t crpi-xjld0ipmc9tve2uz.cn-chengdu.personal.cr.aliyuncs.com/xiaoliu_111/litevpn-client-linux:v1.2 .
docker push crpi-xjld0ipmc9tve2uz.cn-chengdu.personal.cr.aliyuncs.com/xiaoliu_111/litevpn-client-linux:v1.2
```

其它环境拉取（需先 `docker login` 该仓库）：

```bash
docker pull crpi-xjld0ipmc9tve2uz.cn-chengdu.personal.cr.aliyuncs.com/xiaoliu_111/litevpn-client-linux:v1.2
```

- 基础：`python:3.12-slim-bookworm` + `curl`（拉订阅）
- 非 root 用户 uid 1000
- 一个容器两个进程：`mihomo` 与 `web_server.py`
