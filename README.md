# 自动小说工坊（Automated-Novel）

AI 自动写小说应用：给一段小说描述或一份小说大纲，按章生成正文。自动规划题材、情节框架与人物关系，每次生成前注入人物关系网并用向量记忆召回前文情节，保证长篇连载的连贯性。

## 功能特性

- **描述生成计划**：在「小说描述」窗口输入一句话想法，自动规划题材、故事梗概、章节框架、人物与人物关系并填充表单，篇幅（短篇/中篇/长篇）由 AI 根据描述判断，创建后直接开始生成整本小说
- **大纲驱动**：也可以手动提交书名、题材、故事梗概与章节大纲开始创作
- **逐章生成正文**：单章目标 2000~2500 字（参考上限 2500 字），超限自动压缩、过短自动扩写，仍不合格自动作废
- **相似度检测**：正文不得与前面章节高度相似——整章与段落两级比对，命中自动重写（最多两次），仍相似直接作废；短于 50 字的段落（对话短句）不参与比对防误伤
- **流式输出**：生成正文时以 SSE 逐段推送，前端实时显示写作过程，压缩/扩写/相似重写都会实时提示
- **连续自动补入**：一键按章节顺序自动补入缺失章节正文（已生成章节跳过不重写），累计字数达到全书目标后自动停止
- **中断续传**：生成过程中可随时点「■ 停止」中断输出，主动断开连接并停止大模型继续生成；未完成的正文不保存，已完成章节保留，重新点击可断点续传仅补缺失章节
- **章节管理**：可单独删除某章正文（大纲保留，后续章节提示内容过时），也可「从本章重写」按顺序重新生成该章起到已写进度的所有章节
- **人物关系网**：前端可直接提交人物与关系；留空则从大纲提取，提取不到自动原创一套；人物少于 3 人自动补充
- **全书一致性**：人物关系从开头到结尾保持一致；允许只登场一两章的边缘人物，但不得与已有设定矛盾
- **向量记忆**：已写章节切块存入 ChromaDB，生成新章节时自动召回相关前文情节
- **字数统计**：支持按篇幅档位换算总字数（短篇约 2 万 / 中篇约 30 万 / 长篇约 110 万）或自定义，逐章记录实际字数
- **重写保护**：重写已生成章节时，自动标记后续章节为内容过时，提示重新生成
- **数据持久化**：大纲、正文、人物关系网落盘保存，服务重启不丢数据；删除小说时向量记忆一并清理
- **Web 界面**：新建小说、章节生成/查看/重写/删除、进度展示、人物关系网可视化、小说列表管理；章节大纲、章节、人物、人物关系列表均支持分页

## 技术栈

| 组件 | 选型 |
| --- | --- |
| Web 框架 | FastAPI + Uvicorn |
| 大纲/推理模型 | deepseek-reasoner（R1） |
| 正文生成模型 | deepseek-chat（V3） |
| 向量数据库 | ChromaDB（持久化于 `db/`） |
| 前端 | 原生 HTML/CSS/JS |

## 快速开始

### 本地运行

需要 Python 3.10+（开发环境为 3.13）。

```powershell
git clone https://github.com/zZAran1/Automated-Novel.git
cd Automated-Novel

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 复制配置模板并填入你的 DeepSeek API Key
Copy-Item .env.example .env

# 启动（开发模式带热重载）
python -m uvicorn main:app --reload
```

启动后访问：

- 使用界面：http://127.0.0.1:8000
- 接口文档：http://127.0.0.1:8000/docs

### Docker 部署

```powershell
Copy-Item .env.example .env   # 填入你的 DeepSeek API Key
docker compose up -d          # 首次自动构建镜像
docker compose logs -f        # 查看日志
docker compose down           # 停止
```

> 注意：本地 uvicorn 与 Docker 模式**二选一**，二者共用 8000 端口和同一数据目录（`data/`、`db/`），不要同时运行。切换模式前先停掉另一个。

## 创作流程

1. **新建小说**：在「小说描述」窗口输入想法点「生成内容并开写」，AI 自动规划题材、章节框架、人物与关系并填充表单（篇幅由描述判断），创建后直接开始连续生成；也可以手动填写书名、题材、梗概、章节大纲（支持分页编辑）后点「创建并进入工作台」
2. **工作台**：点「⚡ 一键连续生成」连续补入缺失章节；每章可「查看 / 生成 / 从本章重写 / 删除」，生成过程中可「■ 停止」，之后再点可断点续传
3. **修稿**：某章起写歪了就「从本章重写」（该章起到已写进度按顺序全部重写，中间缺失的章会补写）；不要的章「删除」正文后按需重新生成，后续章节会提示内容过时

## 配置说明（.env）

| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `DEEPSEEK_API_KEY` | 是 | — | DeepSeek API Key，获取地址：https://platform.deepseek.com/api_keys |
| `DEEPSEEK_OUTLINE_MODEL` | 否 | `deepseek-reasoner` | 大纲/人物关系推理模型 |
| `DEEPSEEK_TEXT_MODEL` | 否 | `deepseek-chat` | 正文生成/JSON 整理模型 |
| `APP_TOKEN` | 否 | 不开启 | 设置后业务接口需携带 `Authorization: Bearer <token>`，公网部署建议开启；浏览器访问时在链接后加 `?token=你的APP_TOKEN` |

## API 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/outline/plan/stream` | 由小说描述生成写作计划（SSE：progress/done/error，篇幅由模型根据描述判断） |
| POST | `/outline` | 创建小说（提交大纲、可选人物与关系） |
| GET | `/outline/{id}` | 查询小说大纲 |
| GET | `/outlines` | 小说列表 |
| DELETE | `/outline/{id}` | 删除小说（正文、关系网、向量记忆一并删除） |
| POST | `/outline/{id}/chapters/{no}/generate` | 生成一章正文 |
| POST | `/outline/{id}/chapters/{no}/generate/stream` | 流式生成一章正文（SSE：delta/reset/done/error 事件） |
| POST | `/outline/{id}/chapters/generate/auto` | 一键连续自动补入缺失章节正文（SSE，跳过已生成章节，达目标字数即停） |
| POST | `/outline/{id}/chapters/{no}/regenerate/auto` | 从某章开始重新生成（SSE，重写该章起到已写进度的所有章节，中间缺失章补写） |
| GET | `/outline/{id}/chapters/{no}` | 查询已生成的章节正文 |
| DELETE | `/outline/{id}/chapters/{no}` | 删除某章正文（大纲保留，向量记忆一并清理，返回后续过时章节） |
| GET | `/outline/{id}/stats` | 全书字数统计（逐章字数） |
| GET | `/outline/{id}/network` | 查看人物关系网（未构建则自动构建） |

## 写作规则

- 单章正文目标 2000~2500 字（参考上限 2500 字）：低于下限自动扩写一次，超出上限不足 1000 字不作废，超出达 1000 字作废
- 正文不得与前面章节高度相似：整章按字符 4-gram Jaccard、段落按 n-gram 包含度比对，相似度达到 0.5 视为高度相似，自动重写最多两次，仍相似作废；短于 50 字的段落不参与比对（阈值均可在 `config.py` 调整）
- 每次生成前完整遍历人物关系网并注入 prompt，人名与关系严格一致
- 人物少于 3 人时自动补充贴合剧情的新人物，已有人物设定不变
- 生成接口强制 JSON 结构化输出校验，模型返回不合格内容直接丢弃重试，不影响已存数据

## 目录结构

```
Automated-Novel/
├── main.py            # FastAPI 接口与数据持久化
├── planner.py         # 小说描述 -> 写作计划（题材/梗概/章节大纲/人物/关系）
├── generator.py       # 逐章生成：召回前文 -> 生成 -> 字数/相似度校验 -> 存储
├── characters.py      # 人物关系网构建/补充/校验
├── memory.py          # ChromaDB 向量记忆（召回/保存/删除）
├── llm.py             # DeepSeek API 封装
├── config.py          # 字数/相似度等全局参数
├── static/            # 前端页面
├── data/              # 业务数据（novels.json，不入库）
├── db/                # 向量库数据（不入库）
├── .chroma-cache/     # 嵌入模型缓存（Docker 挂载持久化，不入库、无需备份）
├── Dockerfile         # Docker 镜像构建
├── docker-compose.yml # Docker 部署编排
├── .env.example       # 配置模板（真实 Key 填入 .env，勿提交）
└── requirements.txt   # 依赖（已锁版本）
```

## 注意事项

- `.env` 含真实 API Key，已加入 `.gitignore`，仅 `.env.example` 模板入库，请勿将真实 Key 提交到仓库
- `data/` 与 `db/` 是你的创作数据，备份时记得一起备份
- Docker 模式下服务监听 `0.0.0.0`，若暴露公网请务必设置 `APP_TOKEN`
