---
name: mihomo-ctl
description: mihomo 代理的命令行控制器，支持节点切换、订阅更新、内核升级。agent-optimized 版本
---
# mihomo-ctl

mihomo 代理的命令行控制工具，agent-optimized：减少调用次数，一次获取足够信息。

## 安装

```bash
uv tool install git+https://github.com/hitsmaxft/mihomo-cli
```

或直接运行：
```bash
uv run python src/mihomo_ctl.py --help
```

## Agent 优化命令

这些命令一次获取所有需要的信息，减少 CLI 调用：

### `overview`

一键获取完整状态：版本、模式、端口、流量、所有代理组及当前节点。

```
mihomo-ctl overview
mihomo-ctl overview --json
mihomo-ctl overview --hints    # 显示操作建议
```

### `gdelay <group>`

测延迟 + 显示最快节点 + 操作建议。

```
mihomo-ctl gdelay Fast         # 测试 Fast 组
mihomo-ctl gdelay --json       # JSON 输出
mihomo-ctl gdelay --hints      # 显示如何切换
```

输出示例：
```
=== Latency Test: Fast ===
Tested: 20 | Alive: 18 | Timeout: 2

By delay (ascending):
     85ms  香港-03 ◀ CURRENT
    102ms  新加坡-01
    118ms  日本-02

Fastest: 香港-03 (85ms)

Hints:
  mihomo-ctl switch Fast 香港-03  # Switch to fastest
```

### `switch <group> <proxy>`

切换节点 + 验证结果 + 下一步建议。

```
mihomo-ctl switch Fast 香港-03
mihomo-ctl switch Fast 香港-03 --hints
```

### `sub`

更新订阅 + 生成配置 + 显示更新后的代理组。

```
mihomo-ctl sub
mihomo-ctl sub --json
```

## 基本命令

### 查看状态

```bash
mihomo-ctl status          # 状态 + 代理组概览
mihomo-ctl groups / g       # 列出所有代理组
mihomo-ctl list <group>     # 列出某组所有节点
```

### 切换节点

```bash
mihomo-ctl switch <group> <proxy>    # 切换节点
mihomo-ctl delay <proxy>            # 测单节点延迟
mihomo-ctl mode [rule|global|direct] # 切换模式
```

### 订阅管理

```bash
mihomo-ctl sub               # 更新订阅 + 重载
mihomo-ctl apply            # 重新生成配置
mihomo-ctl profile list     # 列出订阅
mihomo-ctl profile use <n>  # 切换订阅
```

### 内核升级

```bash
mihomo-ctl kernel versions  # 列出最近版本
mihomo-ctl kernel upgrade   # 升级到最新版
mihomo-ctl kernel install v1.19.22  # 安装指定版本
```

### 其他

```bash
mihomo-ctl conns            # 查看连接
mihomo-ctl killall / ka     # 断开所有连接
mihomo-ctl dns <domain>     # DNS 查询
mihomo-ctl traffic / t      # 实时流量
mihomo-ctl logs             # 日志流
mihomo-ctl start/stop       # 启停进程
```

## 全局参数

| 参数 | 说明 |
|------|------|
| `--verge` | 使用 Clash Verge 配置目录 |
| `--json` | JSON 输出，便于解析 |
| `--hints` | 显示操作建议 |

## 配置目录

- 独立部署：`~/.local/share/mihomo/`
- Clash Verge：`~/.config/mihomo-ctl/verge`（用 `--verge` 参数）

## 常用场景

### 发 Twitter 卡了 → 测延迟 + 切换

```bash
# 一步搞定：测延迟 + 找到最快节点
mihomo-ctl gdelay Fast --hints

# 切换到最快节点
mihomo-ctl switch Fast <最快节点名>
```

### 更新订阅

```bash
mihomo-ctl sub
```

### 查看完整状态

```bash
mihomo-ctl overview
```

## 依赖

- Python 3.11+
- pyyaml
- node.js（脚本增强功能需要）