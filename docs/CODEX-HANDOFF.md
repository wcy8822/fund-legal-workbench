# Codex 接手：V0.3 公开源码、匿名克隆与 Draft PR

## 已完成与阻塞

当前接手分支为 `feat/v0.3-evidence-retrieval`，计划作为公开仓库默认分支，Draft PR 目标仍为 `main`，不自动合并。代码包承接 V0.1 的三稿闭环、V0.2 的工作动作设计与意见改写实现，新增本项目关键词检索、公开查询准备、默认关闭的 Tavily 搜索适配器、检索记录与人工法源登记。**V0.2/V0.3 新增代码均尚未运行验证。用户已要求“先不验证”，此安排继续有效；接手不意味着可以自动启动应用、测试、搜索、浏览器验收或模型。**

V0.1 的 51 项 Python 测试及 36 项前端渲染断言仅是历史记录，不能作为当前版本通过的证据。V0.2 的固定资料、提示词准备、虚构离线示例、手工草稿、人工采纳与表达历史仍是未验证实现。`tests/test_opinion_rewrites.py` 和 `tests/test_retrieval.py` 是已写入、未运行的用例；新增表、旧数据升级与回归行为也未执行验证。

V0.3 只搜索本项目已导入资料；不是公司全库、电脑索引或企业权限系统。专业法律 MCP、公司资料库、指定目录与自动浏览器均未连接。Tavily 适配器默认 `disabled`；`keyless` 或服务端 API key 配置仅允许尝试请求，不证明连通、账号权益、配额或公司数据授权。无密钥也会向第三方传输抽象问题。公开文档调研不等于实际搜索 API/MCP 调用。

交付 ZIP 随附完整源码、从 V0.2 实现提交 `bd3459e492d4b5239f280ce9e6015e5d388ade70` 导出的 V0.3 增量 patch，以及本地 `fund-legal-workbench.bundle`。patch 用于已有相同基线时接入；bundle 用于恢复已有基线和独立分支，不能将两条恢复路径重复应用。最终文件名、提交与分支以包内清单和实际 Git 历史为准；均不包含运行数据、真实合同或凭据。

2026-11-21 的历史阻塞：GitHub 连接器已确认登录账号 `wcy8822`，但没有建库接口；目标仓库返回404、安装范围搜索无结果；执行环境 `gh` 未登录，浏览器创建页停在登录界面。当时没有远端推送或 PR。不能把这些历史信息解释为仓库一定不存在或权限永远不可用。

2026-11-22 用户明确授权公开源码，覆盖此前仅允许私有仓库的要求；本轮已确认浏览器登录账号为 `wcy8822`，并实际创建[公开仓库](https://github.com/wcy8822/fund-legal-workbench)。GitHub API 已确认 `visibility=public`、`push/admin=true`。初始化 `main` 提交为 `de15ebe3e40ed5a0638256adeb07ca1a8f05f376`，目前仅有README。完整代码推送、默认分支调整与PR仍在进行，不能声称完整交付或应用验收已完成。源码公开、GitHub写入和应用运行分别记录，详见 [PUBLIC-RELEASE.md](PUBLIC-RELEASE.md)。

## 可直接转交给 Codex 的完整指令

```text
接手 fund-legal-workbench V0.3。公开发布目标是 https://github.com/wcy8822/fund-legal-workbench，目标默认分支为 feat/v0.3-evidence-retrieval；先读取 docs/PUBLIC-RELEASE.md 的实际状态，不把发布计划当成成功结果。公开发布完成后，只克隆源码无需GitHub登录；只有写入远端或开PR时才需要相应权限。你是产品负责人兼全栈工程师，请读取 AGENTS.md、README.md、docs/VERIFICATION.md、docs/OPINION-REWRITE-V0.2.md、docs/RETRIEVAL-V0.3.md、docs/RETRIEVAL-ARCHITECTURE.md、docs/LEGAL-SOURCE-CONNECTORS-RESEARCH.md 和 docs/ROADMAP.md，不重做或覆盖已有成果。四个项目入口保持合同、审核意见、法源、交付物。

用户当前要求“先不验证”。先承接代码和文档，保持独立分支及未验证状态；不要启动应用、运行测试／语法检查、搜索接口或 MCP 调用、浏览器验收或模型评测，也不要探测公司账号、目录或内部系统。只有后续会话明确恢复相应安排时，才执行下面的运行或验证步骤。不要把此前 V0.1 的通过结果作为 V0.2/V0.3 的结果，也不要因写好了测试代码就标为通过。恢复本地验证不等于授权外部传输或公司系统访问。

用户已于2026-11-22明确授权公开该项目源码，取代此前private-only要求，不必再次征询是否公开。执行远端写入时，先确认目标属于wcy8822及实际写入权限。若仓库已存在，读取README、AGENTS、分支和现有代码，先比较后集成；若仍为private，按已授权范围改为public。若确认需要新建且有权限，创建public仓库。不能将404直接解释为不存在，不能覆盖已有工作、强推或改建到其他账号。仅在归属或实际权限无法确认时报告具体阻塞，不写入凭据。公开范围仅含代码、文档和虚构样例，不含真实合同、运行数据库、密钥或账号秘密，不部署公网。

交付ZIP包含完整源码、从bd3459e492d4b5239f280ce9e6015e5d388ade70导出的V0.3增量patch和fund-legal-workbench.bundle，可在远端尚不可读时继续接手；各包的准确版本以清单为准，不假定旧ZIP已包含后续发布文档。当前完整代码分支为feat/v0.3-evidence-retrieval；bundle保留main及既有实现／设计历史。已有相同V0.2基线时再考虑增量patch，不要在已恢复V0.3的分支重复应用。若已有远端，先比较后集成，不强推、不直接合并主干。

发布后将含完整源码的feat/v0.3-evidence-retrieval设为默认分支，避免普通匿名git clone只取得空main。main保留为Draft PR审阅基线，不为满足克隆而自动合并。若远端已有不同内容的默认分支，先保留并比较已有工作，不能直接覆盖。记录真实默认分支和可取回的提交；仓库可读不等于应用测试通过。

保持 Tavily 搜索默认关闭；FWB_PUBLIC_SEARCH_MODE=keyless 或 api_key 不代表数据授权或服务已通过验证，FWB_TAVILY_API_KEY 只存服务端环境。专业法律 MCP、公司资料库、指定目录和自动浏览器均未接入；不要将候选目录或 available=true 解释为实际可用。不要因无密钥模式而自动联网。不得将客户名称、交易秘密、合同全文、内部检索结果或系统凭据发给外部搜索；外部只接收经确认的去标识化法律问题及其必要公开查询参数。

恢复本地验证后，先运行 tests/test_retrieval.py 的本地隔离用例，使用桩件覆盖外部响应，不能以真实搜索替代桩件。确认本项目与历史范围、待 OCR 提示、默认不外发、出站字段、有限敏感信息预检、失败与无结果区分、未知执行状态不自动重试、摘要不自动变成引文、人工来源确认、来源幂等及不可覆盖。没有结果只表示本次范围没有候选；不能写成规则或案例不存在。网页公开时间、生效时间、效力状态、判断时点和人工核验时间须分别保留。

随后恢复 V0.2 意见改写的必要回归，覆盖快照固定、普通资料无假模型输出、离线示例限制、草稿追加、人工确认、版本／状态变化后的过期拒绝、不同用途表达连续采纳并存、采纳后批准失效及跨项目范围。仅增加采用表达不应让其他草稿或表达过期；实际依据和处理状态改变仍应使旧快照需要重新准备。新增法源沿审查版本更新机制使旧改写快照和外稿批准需要复核，但不能自动改写旧意见、关闭问题或批准交付。

恢复运行安排后，可用 bash start.sh，在本机浏览器打开 http://127.0.0.1:8765；保持只监听 loopback，不部署公网。原有链路为虚构投资项目→初稿/对方版→差异→带准确条款与民法典565出处的意见→新版→期限恢复但通知对象和审批附件未解决→内部清单与对外清洁阅读稿→内部审阅包→人工批准→下载。V0.2 链路为选定意见→选择读者与语气→准备资料或明确的离线演示→手工保存草稿→核对含义和披露范围→人工采纳→表达历史及 Word 复制。V0.3 链路为法源→查本项目已导入资料→另行输入去标识化法律问题→准备官方跳转→人工核对原文与时点→登记新法源→在审核意见选用。默认链路不需要外部 API、模型或自动浏览器。不要用真实客户合同。

恢复验证后须确认：历史原件和原意见不被采纳覆盖；旧问题不自动结案；当前版回退或 carry_issues=false 也不能漏掉未结问题；未批准对外件在公司接口返回403，内部件不从公司接口下载；采纳表达或新增来源等审查资料变化使旧批准失效；导入幂等与项目 scope 有效。应用默认无公司连接，测试使用临时随机令牌。文字差异不等于语义保证；结构检查与正文预览不能冒充 Word/PDF 排版或完整浏览器验收。

待用户恢复相关安排后，在实际 Mac/Word 环境补做浏览器点击、响应式和 Word 核对，重点检查请求失败及资料过期时是否保留未保存输入，复制对方文字时是否带入内部说明。真实搜索连通、专业法律 MCP、公司库及受控浏览器需分别有访问与数据授权，再按获准范围实施；配置或本地测试通过不能替代这些条件。只启用获准的只读工具，不开放反馈提交、客户名称查询或整台电脑搜索。Gemini/Claude 保持未调用、未测状态，除非已有数据与调用授权并实际完成同题虚构材料盲测。真实项目仍需另过 V1 公司授权、厂商留存与训练条件、权限隔离、审计及备份恢复门槛。

把代码、文档、虚构样例提交到独立分支，推送到已核验归属的公开仓库。首次交付计划使用feat/v0.3-evidence-retrieval向main开Draft PR，后续改动先保留已有工作再选独立分支。当前暂缓验证时可以交付明确标为未验证的Draft PR，但不能把合并或上线当成默认下一步，也不能无视当前安排触发应用验证；遇到强制流程如实记录。本次PR正文已准备在docs/PR-DRAFT-V0.3.md，读取并按实际状态更新后使用；不要复用V0.1通过声明。不得自动合并。最后报告真实仓库/PR URL、默认分支、commit、匿名获取结果、已执行与未执行项，以及下一步最有价值的一项工作。
```

## 新建空仓库时的参考操作

以下命令是参考步骤，不是已执行记录；发布结果以 [PUBLIC-RELEASE.md](PUBLIC-RELEASE.md) 为准。逐组查看结果后再继续，不整段盲跑。若远端已存在，跳过新建流程，先阅读并做差异集成。只克隆已公开源码不需要执行GitHub登录或建库命令。

```bash
gh api user --jq .login
gh repo view wcy8822/fund-legal-workbench --json nameWithOwner,isPrivate,defaultBranchRef
```

账号必须为 `wcy8822`；不能把权限导致的404简单视为不存在。仅在确认需要新建且凭据可建库后，按用户最新授权创建公开仓库：

```bash
git clone -b main fund-legal-workbench.bundle fund-legal-workbench-git
cd fund-legal-workbench-git
gh repo create wcy8822/fund-legal-workbench --public
gh repo view wcy8822/fund-legal-workbench --json nameWithOwner,isPrivate
```

确认查询返回 `nameWithOwner=wcy8822/fund-legal-workbench` 且 `isPrivate=false`。若既有仓库仍为私有，按已授权范围调整公开状态；无法完成时如实记录。以下`main`推送仅适用于新建空仓库，不覆盖已有远端分支：

```bash
git remote rename origin bundle
git remote add origin https://github.com/wcy8822/fund-legal-workbench.git
git push -u origin main
git switch -c feat/v0.3-evidence-retrieval bundle/feat/v0.3-evidence-retrieval
git push -u origin feat/v0.3-evidence-retrieval
gh repo edit wcy8822/fund-legal-workbench --default-branch feat/v0.3-evidence-retrieval
gh pr create --repo wcy8822/fund-legal-workbench --base main --head feat/v0.3-evidence-retrieval --draft --title "feat: project evidence retrieval and human source records" --body-file docs/PR-DRAFT-V0.3.md
```

PR 正文见 [PR-DRAFT-V0.3.md](PR-DRAFT-V0.3.md)，已区分实现范围与暂未验证状态；创建前只按真实新增结果更新，保留尚未执行项。V0.1/V0.2 的既有说明继续保留作历史。`git clone` 可能已经创建某个本地分支，执行前用 `git branch -a` 查看；已有分支就 `git switch`，不要覆盖。若远端已有默认分支，上述新建仓库的 `main` 推送示例不适用，必须先比较后集成。

## 迭代日志

- 来源：V0.1历史、V0.2/V0.3实现与暂缓验证安排，以及2026-11-22用户明确公开源码授权。
- 结论：接手版本为V0.3；源码公开发布进行中，V0.2/V0.3应用仍未验证。
- 改动点：移除当前操作的private-only要求，补匿名克隆、完整代码默认分支及公开Draft PR流程；保留旧阻塞为历史。
- 待补充：实际仓库/PR结果、默认分支与可取回提交；后续运行、回归及另获授权的外部服务结果。
- 下一步建议：完成公开源码和可克隆分支交付，保持Draft PR未合并；应用验证依后续安排执行。

思维导图结构：V0.1合同与交付历史 → V0.2人工意见表达 → V0.3分范围检索 → 公开源码与匿名克隆 → 完整代码默认分支及未合并Draft PR → 按后续安排恢复验收。
