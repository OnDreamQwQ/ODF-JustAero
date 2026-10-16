# ODF-JustAero Pack

把本地**已经在跑的** Minecraft 实例，转成一个可分发、可自动更新的 packwiz 整合包，并自动产出启动器能识别的 `.mrpack`。

- **游戏版本**：Minecraft 1.21.1 / NeoForge 21.1.259
- **当前版本**：`0.5.0`
- **规模**：244 个 mod（238 个联网下载 + 6 个随包附带）
- **来源**：直接从 `D:\Minecraft\.minecraft\versions\ODF-JustAero` 提取，逐文件哈希核对

---

## 这个仓库解决什么问题

传统打包是「把 mods 文件夹压成 zip」，结果是：

- 包体几百 MB，每次更新都要重新打包、重新传
- 玩家下载的是别人二次分发的 jar，作者更新了也不知道
- 启动器有的认得 zip，有的只认 `.mrpack`，格式对不上

这个仓库改成「**声明式**」：仓库里只存每个 mod 的**来源和哈希**（`pack/mods/*.pw.toml`），jar 本身由启动器/安装器从原作者的 CDN 下载。于是：

| | 传统 zip | 这个仓库 |
|---|---|---|
| 仓库体积 | ~900 MB | ~60 MB（只有 py 文件和元数据 + 6 个自带 jar）|
| 玩家体积 | 完整包 | 完整包（首次同样要下）|
| 更新 | 重传整包 | 改几行元数据，玩家增量更新 |
| 启动器 | 看格式 | `.mrpack` 通用导入 + packwiz 自动更新 |

---

## 目录结构

```
ODF-JustAero-Pack/
├─ pack/                    ← packwiz 整合包本体（会被脚本整目录重建）
│  ├─ pack.toml             包名 / 版本 / 游戏版本
│  ├─ index.toml            所有文件的 sha256 清单
│  ├─ mods/                 238 个 .pw.toml（下载来源）+ 6 个自带 jar
│  ├─ config/  defaultconfigs/  resourcepacks/  options.txt
│  └─ .packwizignore        让 packwiz 别把报告文件打进包里
│
├─ tools/                   ← 工具
│  ├─ settings.ps1          唯一需要改的文件（实例路径在这里）
│  ├─ Get-Tools.ps1         下载 packwiz.exe 和安装器
│  ├─ Update-Pack.ps1       主流程：实例 → 仓库
│  ├─ Verify-Pack.ps1       核对仓库和实例是否一致
│  ├─ Export-Mrpack.ps1     生成 .mrpack
│  ├─ packwiz_from_instance.py   核心解析器（纯标准库 Python）
│  └─ bin/                  packwiz.exe 等二进制（不进 git）
│
├─ docs/使用文档.md          ← 完整说明，先看这个
├─ dist/                    .mrpack 输出（不进 git）
└─ .github/workflows/build-mrpack.yml   推送后自动打包并发布 Release
```

---

## 快速开始

```powershell
# 0. 前提：Python 3.9+、git、GitHub CLI（gh auth login）
# 1. 抓二进制
.\tools\Get-Tools.ps1

# 2. 从实例重建整包（会顺手跑一遍核对）
.\tools\Update-Pack.ps1

# 3. 本地生成 .mrpack
.\tools\Export-Mrpack.ps1
```

日常改动流程：

```powershell
# 在实例里改完整合包（加 mod / 改配置），然后：
.\tools\Update-Pack.ps1 -Bump -Push
# GitHub Actions 会自动构建 .mrpack 并更新 Release
```

---

## 给玩家

两种安装方式，任选其一。

**A. `.mrpack`（推荐，最省事）**

去 [Releases](../../releases/latest) 下载 `ODF-JustAero-<版本>.mrpack`，
在 Prism Launcher / Modrinth App / ATLauncher / MultiMC 里「导入整合包」即可。

**B. packwiz 自动更新（改了就能更新）**

```bash
java -jar packwiz-installer-bootstrap.jar https://raw.githubusercontent.com/OnDreamQwQ/ODF-JustAero-Pack/main/pack/pack.toml
```

---

## 文档

- [完整使用文档](docs/使用文档.md) —— 每个脚本的参数、排错、原理
- [packwiz 官方文档](https://packwiz.infra.link/)
- [.mrpack 格式规范](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack)

---

## 已知事项

- 包里有 **6 个 jar 是随包附带**的（上游没发布到任何 CDN，或者你本地改过）：
  `create_flight_control`、`createredstonelinkgui`、`createrenxin`、`neo-voxy`、`simurail`。
  这些是「你再分发」的内容，分享前请自行确认授权。
- 有 **33 个 mod 只存在于 CurseForge**，它们的下载链接不是 Modrinth 白名单域名。
  因此这个包**可以直接用启动器安装，但不能直接投稿到 Modrinth**。
  详见 [使用文档 · 关于授权](docs/使用文档.md)。
