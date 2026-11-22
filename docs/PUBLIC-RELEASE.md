# 公开源码发布与其他 AI 接手

更新日期：2026-11-22。状态：**完整源码已公开，默认分支已设置，匿名HTTPS克隆已实际成功，Draft PR #1已创建且未合并；应用仍未验证。**下表记录已完成的发布事实，后续接手命令不代表应用已运行。

## 授权与范围

用户已明确要求将`fund-legal-workbench`公开，方便其他电脑不登录GitHub即可取得源码。本次授权覆盖此前仅允许私有仓库的要求，不需要重复征询是否公开。2026-11-21文档中的私有目标及认证阻塞是历史记录，当前操作以本说明和最新用户指令为准。

公开内容限代码、文档和虚构样例；不发布真实合同、数据库、运行文件、密钥或账号秘密。源码采用根目录[MIT许可](../LICENSE)；第三方内容按各自许可使用。应用继续只监听本机，不部署公网，不自动合并PR。

用户“先不验证”的应用安排仍有效：不自动启动应用、测试、语法检查、浏览器应用验收、搜索接口或模型评测。GitHub发布和匿名取回源码应单独记录，不能写成应用已通过测试。

## 发布状态

| 项目 | 实际结果 |
| --- | --- |
| 账号 | 本轮已通过浏览器确认`wcy8822`，GitHub API确认当前账号`push/admin=true`；本机CLI认证仍需分别判断 |
| 公开仓库 | [wcy8822/fund-legal-workbench](https://github.com/wcy8822/fund-legal-workbench)已创建，GitHub API确认`visibility=public` |
| 初始化基线 | `main`提交`de15ebe3e40ed5a0638256adeb07ca1a8f05f376`，仅有README；保留为PR审阅基线 |
| 完整代码分支 | `feat/v0.3-evidence-retrieval`；76个文件已实际发布 |
| 首个完整源码提交 | `f8e28d342138899f8e3d95e052f0f30b33cfa8e1` |
| 文件树 | `26752bcbdb6f7bb9ea61713e3deecae167ae18fd`，与本地`d9a8476`源码树相同 |
| 默认分支 | 已通过浏览器设为`feat/v0.3-evidence-retrieval`，普通克隆可直接取得完整源码 |
| Draft PR | [PR #1](https://github.com/wcy8822/fund-legal-workbench/pull/1)，`feat/v0.3-evidence-retrieval` → `main`，`draft=true`、`merged=false` |
| 开源许可 | 根目录`LICENSE`已提供MIT文本 |
| 匿名获取 | 清空凭据助手及HTTP额外头、禁用全局Git配置与交互认证后，在独立临时目录以HTTPS执行`clone --depth 1`成功 |
| 应用验证 | V0.2/V0.3未运行；V0.1历史测试仅证明当时版本 |

匿名克隆实际取得分支`feat/v0.3-evidence-retrieval`、HEAD `f8e28d342138899f8e3d95e052f0f30b33cfa8e1`、76个文件及上述相同文件树。此记录固定描述首个完整源码快照；后续文档提交会推进分支，不应把当时HEAD写成永远不变的最新提交。没有启动应用或运行测试。

`main`作为审阅基线保留，现有PR不自动合并。后续开发以GitHub实时默认分支为基线，先读取并保留已有工作，再创建独立分支。旧ZIP、增量patch及Git bundle仅作历史备份，用户无需下载或转交，也不要用旧包覆盖GitHub后续改动。

## 如何取得源码

默认分支已设置，直接克隆：

```bash
git clone https://github.com/wcy8822/fund-legal-workbench.git
cd fund-legal-workbench
```

需要明确指定开发版时：

```bash
git clone --branch feat/v0.3-evidence-retrieval --single-branch https://github.com/wcy8822/fund-legal-workbench.git
```

只读取公开源码不需要GitHub登录。提交到远端或开PR仍需相应权限。克隆后先阅读README和AGENTS，不自动安装依赖、启动程序或调用外部服务。

## 可交给其他 AI 的接手提示词

```text
请接手已公开的 fund-legal-workbench V0.3：https://github.com/wcy8822/fund-legal-workbench。当前默认分支为feat/v0.3-evidence-retrieval，完整代码已发布，匿名HTTPS克隆已实际成功。

先以HTTPS匿名克隆公开源码；如已有本地目录，先读取并保留已有工作，不覆盖。以后续实际取得的GitHub默认分支为开发基线，创建独立工作分支，不重建项目。无需我下载或转交ZIP、patch、bundle，也不要用旧包替换当前源码。无法取回时报告真实错误，不猜测仓库状态。只克隆不要求我登录GitHub。

读取 AGENTS.md、README.md、docs/PUBLIC-RELEASE.md、docs/CODEX-HANDOFF.md 和 docs/RETRIEVAL-V0.3.md，沿已有代码继续，不重新搭建。用户2026-11-22的公开源码授权覆盖旧private-only要求；历史说明保留为历史，不当作当前禁止公开的指令。

当前先不验证应用。不要自动安装依赖、启动程序、运行测试或语法检查、搜索API/MCP、模型或浏览器应用验收。不要把V0.1历史通过结果表述成V0.2/V0.3已经通过。只报告实际取得的分支、提交和阅读结论；需要进一步开发时沿独立分支实施并注明未验证。

应用维持合同、审核意见、法源、交付物四入口。原件和历史不得覆盖，旧意见不得自动关闭，对外件须人工批准。Tavily默认关闭；专业法律MCP、公司库和自动浏览器未连接。配置不代表授权，不发真实客户信息或合同，不扫描电脑或内部账号。

若后续需要提交，先确认实际写入权限、比较远端现有工作，提交到从当前默认分支建立的独立分支并开Draft PR；不自动合并、不强推、不部署公网。现有https://github.com/wcy8822/fund-legal-workbench/pull/1是feat/v0.3-evidence-retrieval向main的Draft PR，保持未合并，不重复创建。后续PR按实际改动选择审阅基线。代码、文档和虚构样例可以公开，真实合同、运行数据与密钥不得入Git。

最后给出真实仓库/分支/提交、做了哪些改动、哪些步骤未执行以及下一项最有价值的工作。缺少权限时准确说明阻塞，不伪造发布或测试结果。
```

完整开发接手说明和恢复验证后的路径见[CODEX-HANDOFF.md](CODEX-HANDOFF.md)。

## 迭代日志

- 来源：用户2026-11-22公开源码授权、既有V0.3实现与暂缓应用验证安排。
- 结论：完整源码、默认分支、匿名克隆、MIT许可及未合并Draft PR均已交付，应用验证未恢复。
- 改动点：写入首个完整源码提交、文件树、76文件及无登录克隆证据，其他AI直接从GitHub接手。
- 待补充：按后续安排执行的应用运行与回归结果；专业服务及真实项目授权。
- 下一步建议：从GitHub实时默认分支继续独立分支开发，不把源码可获取扩大为应用可用或真实项目可上线。

思维导图结构：公开授权 → 完整源码分支 → 匿名克隆 → 读取现有说明 → 独立分支继续工作 → Draft PR人工审阅；应用验证另按后续安排。
