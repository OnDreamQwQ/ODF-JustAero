<div align="center">
  <h1>ODF-JustAero</h1>

  <img src="icon.png" width="512" alt="ODF-JustAero">

  [![Minecraft 1.21.1](https://img.shields.io/badge/Minecraft-1.21.1-62B47A?logo=minecraft&logoColor=white)](https://www.minecraft.net/)
  [![NeoForge 21.1.259](https://img.shields.io/badge/NeoForge-21.1.259-EA6D0A)](https://neoforged.net/)
  [![下载](https://img.shields.io/github/v/release/OnDreamQwQ/ODF-JustAero?label=%E4%B8%8B%E8%BD%BD&color=2F81F7&logo=github&logoColor=white)](https://github.com/OnDreamQwQ/ODF-JustAero/releases/latest)
  [![License](https://img.shields.io/github/license/OnDreamQwQ/ODF-JustAero?color=blue)](LICENSE)

  <!-- TODO（你自己写）：一句话口号，比如「让 XX 玩起来更 XX」 -->
  <p>面向 Minecraft Java Edition 1.21.1 的 <strong>NeoForge</strong> 整合包，共 <strong>244</strong> 个 mod。</p>
</div>

> 面向玩家的整合包主页。技术细节、维护流程见仓库外的本地文档
> `docs\使用文档.md`（**故意不进 git**）。

---

## 简介

<!-- TODO（你自己写）：3-5 行简介。可以写：
     - 主题 / 玩法方向（机械动力 + 航空/载具？）
     - 和 ATM10 之类的区别（"小"整合包，不肝）
     - 大致规模、开荒时长
     - 单人 / 多人（有没有服务端） -->

---

## 下载

[Releases](../../releases/latest) → `ODF-JustAero-<版本>.mrpack`

用**第三方启动器**导入即可。

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

整合包本身（`pack/`、`tools/`、脚本与配置）以 **[MIT](LICENSE)** 授权。

包里的每一个 mod、资源包、材质和模型，版权归各自作者所有、遵循各自的许可协议，
**MIT 不覆盖它们**——它们不是可以随意再分发或商用的东西。

<!-- TODO（你自己写）：可以在这里点名感谢特别重要的 mod 作者 -->
