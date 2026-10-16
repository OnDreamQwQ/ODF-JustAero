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

## 下载

[Releases](../../releases/latest) → `ODF-JustAero-<版本>.mrpack`

### 自动更新（可选）

放进启动器的**启动前命令**，之后每次启动都会增量更新：

```bash
java -jar packwiz-installer-bootstrap.jar https://raw.githubusercontent.com/OnDreamQwQ/ODF-JustAero/main/pack/pack.toml
```

---

## 更新日志

完整记录见 **[CHANGELOG.md](CHANGELOG.md)**。

<!-- TODO（你自己写）：这里只放最近两三版的摘要，详细内容写进 CHANGELOG.md -->

- `0.5.0` —— 首个 packwiz 版本（244 个 mod，MC 1.21.1 / NeoForge 21.1.259）

---

## ⚠️ 已知事项

- 包里有 **6 个 jar 是随包附带**的（上游没发布到任何 CDN，或者本地改过）：
  `create_flight_control`、`createredstonelinkgui`、`createrenxin`、`neo-voxy`、
  `simurail`、`sable-dynamic-lights`。
- 有 **33 个 mod 只存在于 CurseForge**，所以这个包**不能直接投稿到 Modrinth**。

---

## 致谢 / 授权

<!-- TODO（你自己写）：可以写"mod 版权归各自作者所有"之类的话 -->
