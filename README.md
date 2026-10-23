# 自动小说工坊（Automated-Novel）

AI 自动写小说应用：输入小说大纲，按章生成正文。自动构建人物关系网并在每次生成前注入上下文，用向量记忆召回前文情节，保证长篇连载的连贯性。

## 功能特性

- **大纲驱动**：提交书名、题材、故事梗概与章节大纲即可开始创作
- **逐章生成正文**：单章目标 1500~2000 字，超限自动压缩、过短自动扩写，仍不合格自动作废
- **人物关系网**：前端可直接提交人物与关系；留空则从大纲提取，提取不到自动原创一套；人物少于 3 人自动补充
- **全书一致性**：人物关系从开头到结尾保持一致；允许只登场一两章的边缘人物，但不得与已有设定矛盾
- **向量记忆**：已写章节切块存入 ChromaDB，生成新章节时自动召回相关前文情节
- **字数统计**：支持按篇幅档位换算总字数（短篇约 2 万 / 中篇约 30 万 / 长篇约 110 万）或自定义，逐章记录实际字数
- **重写保护**：重写已生成章节时，自动标记后续章节为内容过时，提示重新生成
- **数据持久化**：大纲、正文、人物关系网落盘保存，服务重启不丢数据；删除小说时向量记忆一并清理
- **Web 界面**：新建小说、章节生成/查看、进度展示、人物关系网可视化、小说列表管理

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
| POST | `/outline` | 创建小说（提交大纲、可选人物与关系） |
| GET | `/outline/{id}` | 查询小说大纲 |
| GET | `/outlines` | 小说列表 |
| DELETE | `/outline/{id}` | 删除小说（正文、关系网、向量记忆一并删除） |
| POST | `/outline/{id}/chapters/{no}/generate` | 生成一章正文 |
| GET | `/outline/{id}/chapters/{no}` | 查询已生成的章节正文 |
| GET | `/outline/{id}/stats` | 全书字数统计（逐章字数） |
| GET | `/outline/{id}/network` | 查看人物关系网（未构建则自动构建） |

## 写作规则

- 单章正文目标 1500~2000 字：低于下限自动扩写一次，超出上限不足 1000 字不作废，超出达 1000 字作废重试
- 每次生成前完整遍历人物关系网并注入 prompt，人名与关系严格一致
- 人物少于 3 人时自动补充贴合剧情的新人物，已有人物设定不变
- 生成接口强制 JSON 结构化输出校验，模型返回不合格内容直接丢弃重试，不影响已存数据

## 目录结构

```
Automated-Novel/
├── main.py            # FastAPI 接口与数据持久化
├── generator.py       # 逐章生成：召回前文 -> 生成 -> 字数校验 -> 存储
├── characters.py      # 人物关系网构建/补充/校验
├── memory.py          # ChromaDB 向量记忆（召回/保存/删除）
├── llm.py             # DeepSeek API 封装
├── config.py          # 字数等全局参数
├── static/            # 前端页面
├── data/              # 业务数据（novels.json，不入库）
├── db/                # 向量库数据（不入库）
├── Dockerfile         # Docker 镜像构建
├── docker-compose.yml # Docker 部署编排
├── .env.example       # 配置模板（真实 Key 填入 .env，勿提交）
└── requirements.txt   # 依赖（已锁版本）
```

## 注意事项

- `.env` 含真实 API Key，已加入 `.gitignore`，仅 `.env.example` 模板入库，请勿将真实 Key 提交到仓库
- `data/` 与 `db/` 是你的创作数据，备份时记得一起备份
- Docker 模式下服务监听 `0.0.0.0`，若暴露公网请务必设置 `APP_TOKEN`
