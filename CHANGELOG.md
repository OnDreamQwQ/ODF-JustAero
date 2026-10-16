# 更新日志

ODF-JustAero 整合包的全部版本记录。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 `主版本.次版本.修订号`：

| 位数 | 什么时候 +1 | 例子 |
|---|---|---|
| **主版本** | Minecraft 或加载器大版本换了 | `0.9.2` → `1.0.0`（升到 MC 1.22） |
| **次版本** | 加/删 mod、换大 mod、改玩法 | `0.5.3` → `0.6.0` |
| **修订号** | 只改配置、修 bug、修崩溃 | `0.5.0` → `0.5.1` |

> 每一条写完，记得在 `pack\pack.toml` 之外同步改版本号：
> `.\tools\Update-Pack.ps1 -Version 0.6.0 -Push`

---

## [未发布]

<!-- 下次发版前，把改动先写在这里，发布时改成版本号 -->

### 新增

-

### 变更

-

### 修复

-

---

## [0.5.0] —— 2026-10-16

**首个 packwiz 版本。** 打包方式从 PCL 手动导出改为「实例 → 脚本提取 → GitHub Actions 自动构建」，
仓库：<https://github.com/OnDreamQwQ/ODF-JustAero>

- **Minecraft** 1.21.1 / **NeoForge** 21.1.259
- **244 个 mod**（238 个联网下载 + 6 个随包附带）

### 新增 mod（13 个）

| mod | 说明 |
|---|---|
| `create_flight_control-neoforge-0.7.8` | 飞行控制 |
| `create_mobile_packages-1.21.1-0.7.8` | 移动包裹 |
| `create_parachute-1.1.4` | 降落伞 |
| `create-infinite-railways-0.2.0` | 无限轨道 |
| `createredstonelinkgui-1.21.1-1.9.3` | 红石链接 GUI（本地 fork）|
| `createrenxin-1.0.0` | — |
| `aero_no_horizon-1.0.4` | — |
| `sable_aeronautics_scaling-1.0.0-r77` | — |
| `MecanumWheels-1.21.1-1.0.1-1.0.1` | 麦克纳姆轮 |
| `neo-voxy.jar` | Voxy 渲染（本地构建）|
| `simurail-0.1.0+mc1.21.1+6560762` | — |
| `irisveil-0.4.0` | — |
| `generalfeedback-1.0.0` | — |

### 移除 mod（2 个）

| mod | 原因 |
|---|---|
| `[战斗机座舱]fixed_tweaked_controller-1.4.0.jar` | 不再使用 |
| `lithostitched-1.8.0-neoforge-21.1.jar` | **与 1.8.1 的 modId 重复**，留着会启动失败 |

### 改名

| 旧 | 新 |
|---|---|
| `Terralith_1.21.x_v2.6.2.jar` | `Terralith_1.21.1_v2.6.2_Neoforge.jar` |
| `ritchiesprojectilelib-2.1.2-mc.1.21.1-neoforge.jar` | `ritchiesprojectilelib-2.1.2+mc.1.21.1-neoforge.jar` |

### 变更

- 打包方式：PCL 导出 → **packwiz**（`pack\` 由脚本从实例提取，元数据进 git）
- 发布方式：手动上传 → **GitHub Actions 构建 `.mrpack` 并挂 Release**
- 3 个资源包（`Squareful方纹`、`XK redstone display`、`Smooth wheel!`）
  从「索引下载」改为**随包附带**（两种方式玩家拿到的文件一样）
- `MecanumWheels`、`create-infinite-railways` 从随包附带改为**联网下载**
  （0.4.0 是打进包里的，现在能找到 Modrinth 来源了）

### 修复

- **剔除 57.26 MB 缓存**：`config/create_fire_control/cache/`（含一个 31.54 MB 的
  `.mp4`）和 `config/netMusicListCache/`（25.10 MB 的缓存图片和音频）。
  这些是 mod 自己会重新下载的缓存，发给玩家纯属浪费。
- 剔除个人客户端状态：JEI 按世界的搜索历史（含世界名）、spark 临时转储、
  Axiom 快捷栏快照、`*.json.backup`
- 修复 `sable-dynamic-lights` 的哈希校验失败：本地是改过的版本，
  改用随包附带而不是指向上游 CDN

---

## [0.4.0] 及更早

PCL2 手动导出的版本，没有版本记录。

作为对比基准，`ODF-JustAero 0.4.0.zip`：

- 索引 **235 个文件**：209 个 CurseForge CDN + 26 个 Modrinth CDN
- 其中 **232 个是 mod**，另 3 个是资源包
- 体积 **90.81 MB**

---

## 怎么写这一页

发版时按这个顺序：

1. **先在本地改完实例**（加/删 mod、改配置），进游戏确认能跑
2. **写日志**：把上面 `[未发布]` 区块的内容确认一遍
3. **改版本号并推送**：

   ```powershell
   # 只改配置 / 修 bug
   .\tools\Update-Pack.ps1 -Bump -Push

   # 加删了 mod
   .\tools\Update-Pack.ps1 -Version 0.6.0 -Push
   ```

4. **把 `[未发布]` 改成 `[0.6.0] —— 2026-XX-XX`**，并在上面补一个新的 `[未发布]` 空区块
5. **手动发布**：

   ```powershell
   gh workflow run build-mrpack.yml
   ```

### 怎么写「新增 / 移除」

想知道两个版本之间到底加删了哪些 mod，可以直接 diff 两个 `.mrpack` 的
`modrinth.index.json`，或者对比 `pack\mods\` 里的文件名。

**不要只写「更新了一些 mod」** —— 三个月后的你会想知道到底是哪几个。

### 怎么判断该升哪一位

- 只是改了 `config\` 里的数值、修了个崩溃 → **修订号**（`-Bump`）
- 加了新 mod、删了 mod、换了主要玩法 mod → **次版本**（`-Version 0.6.0`）
- 换了 Minecraft 或 NeoForge 大版本 → **主版本**（`-Version 1.0.0`）

> ⚠️ 大版本跳跃的 mod 升级（比如某个 Create 附属从 `0.6` 到 `0.7`）
> **一定要先写进日志**，这类改动最容易把整合包的配方体系打乱。
