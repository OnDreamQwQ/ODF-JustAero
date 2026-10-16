# ODF-JustAero

<!-- TODO（你自己写）：一两句话介绍这个整合包是什么风格、给谁玩的 -->

> 面向玩家的整合包主页。技术细节、维护流程见仓库外的本地文档
> `docs\使用文档.md`（**故意不进 git**）。

---

## 简介

<!-- TODO（你自己写）：3-5 行简介。可以写：
     - 主题 / 玩法方向（机械动力 + 航空/载具？）
     - 和 ATM10 之类的区别（"小"整合包，不肝）
     - 大致规模、开荒时长
     - 单人 / 多人（有没有服务端） -->

- **Minecraft**：1.21.1
- **加载器**：NeoForge 21.1.259
- **规模**：244 个 mod
- **整合包版本**：`0.5.0`

---

## 安装

### 方式 A：`.mrpack`（推荐）

1. 去 [Releases](../../releases/latest) 下载 `ODF-JustAero-<版本>.mrpack`
2. 启动器里导入：
   - **Prism Launcher**：`Add Instance` → `Import from file` → 选 `.mrpack`
   - **Modrinth App**：`Add Instance` → `Import from file`
   - **ATLauncher / MultiMC**：都有 import 入口
   - **HMCL**：`版本列表` → `安装新游戏` → `从整合包安装`
3. 首次启动会联网下载约 900 MB 的 mod，耐心等

### 方式 B：packwiz 自动更新（改了就能跟）

```bash
java -jar packwiz-installer-bootstrap.jar https://raw.githubusercontent.com/OnDreamQwQ/ODF-JustAero-Pack/main/pack/pack.toml
```

放进启动器的启动前命令里，之后每次启动都会增量更新。

---

## 更新日志

<!-- TODO（你自己写）：每个版本一行，写清楚加了什么 mod / 改了什么配置 -->

- `0.5.0` —— 首个 packwiz 版本

---

## ⚠️ 已知事项

- 包里有 **6 个 jar 是随包附带**的（上游没发布到任何 CDN，或者本地改过）：
  `create_flight_control`、`createredstonelinkgui`、`createrenxin`、`neo-voxy`、
  `simurail`、`sable-dynamic-lights`。
- 有 **33 个 mod 只存在于 CurseForge**，所以这个包**不能直接投稿到 Modrinth**。

---

## 致谢 / 授权

<!-- TODO（你自己写）：可以写"mod 版权归各自作者所有"之类的话 -->
