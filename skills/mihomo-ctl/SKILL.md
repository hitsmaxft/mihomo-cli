---
name: mihomo-ctl
description: mihomo 代理的命令行控制器，支持节点切换、订阅更新、内核升级
---
# mihomo-ctl

mihomo 代理的命令行控制工具，不需要 GUI，一个命令完成所有操作。

## 使用场景

- **节点测延迟** — 批量测试全组节点，按延迟排序，找出最快的
- **快速切节点** — 命令行切代理，比 GUI 更快
- **自动订阅更新** — 一行命令更新订阅并重载配置
- **内核升级** — 自动下载安装最新版 mihomo 内核
- **终端党必备** — 全程 terminal 操作，macOS/Linux 都支持

## 工作原理

通过 Unix socket 与 mihomo 通信，调 API 实现：
- 查状态 / 流量 / 连接
- 切节点 / 测延迟
- 重载配置 / 启停进程

## 配置目录

- `~/.local/share/mihomo/` — 独立部署的 mihomo
- `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/` — Clash Verge 模式（加 `--verge` 参数）

## 核心功能速查

### 切换节点

先用 `groups` 看有哪些组：

```
mihomo-ctl groups
```

查看某组所有节点（◀ 是当前选中的）：

```
mihomo-ctl list <group-name>
```

切换到某个节点：

```
mihomo-ctl switch <group-name> <proxy-name>
```

### 测延迟

测单节点：

```
mihomo-ctl delay <proxy-name>
```

测全组，按延迟排序：

```
mihomo-ctl gdelay <group-name>
```

### 订阅更新

更新订阅并重载：

```
mihomo-ctl sub
```

只重载配置（不重新下载）：

```
mihomo-ctl apply
```

### 切换订阅配置

```
mihomo-ctl profile list    # 看有哪些订阅
mihomo-ctl profile use <name>  # 切换到某个订阅
```

### 查看状态

```
mihomo-ctl status    # 版本、端口、模式
mihomo-ctl traffic   # 实时流量（Ctrl+C 停止）
mihomo-ctl conns      # 当前连接列表
```

### 内核升级

```
mihomo-ctl kernel versions   # 列出最近版本
mihomo-ctl kernel upgrade   # 升级到最新版
mihomo-ctl kernel install v1.19.22  # 安装指定版本
```

## 常用场景示例

### 发 Twitter / 外网访问卡了

```bash
# 1. 看全组延迟
mihomo-ctl gdelay Fast

# 2. 切到延迟最低的节点
mihomo-ctl switch Fast <最快节点名>
```

### 更新订阅

```bash
mihomo-ctl sub
```

### 用 Clash Verge 的配置

```bash
mihomo-ctl --verge gdelay Fast
```

### 切代理模式

```bash
mihomo-ctl mode rule     # 规则模式
mihomo-ctl mode global   # 全局模式
mihomo-ctl mode direct  # 直连
```

## 安装

```bash
uv tool install git+https://github.com/hitsmaxft/mihomo-cli
```

或直接运行脚本：

```bash
uv run python src/mihomo_ctl.py --help
```

## 依赖

- Python 3.11+
- pyyaml
- node.js（脚本增强功能需要）