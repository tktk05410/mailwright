# Mailwright

企业邮件回复助手：读取邮件正文，结合企业手册条款（RAG）与历史邮件语气，生成可直接使用的中文回复草稿。桌面端基于 Flet 1.0.4，界面为瑞士国际主义风格（Inter + Noto Sans SC，米白 / 黑 / 强调红三色平涂）。

> Mailwright = Mail + Wright（匠人）——为你打造邮件的人。

## 功能

- **邮件来源**：剪贴板 / Outlook 选中（COM，需 Outlook 已打开）
- **分类摘要**：标签、是否需回复、一句话摘要、置信度
- **条款检索**：BM25 + 向量双路召回，RRF 融合，相似度阈值过滤；支持多份手册，启用开关在检索期过滤、即时生效
- **语气模仿**：内置语气样本，可导入 Outlook 已发送邮件（落盘前对邮箱地址脱敏，仅保存在本机）
- **规则约束**：自定义回复规则注入 prompt（限 20 条、自动去重）
- **引用校验**：模型引用不存在的条款时给出警示
- **离线降级**：未配置 API Key 或调用失败时自动降级为本地演示结果，界面明确标注「离线演示」

## 运行

```powershell
cd mailwright
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

窗口默认 1080×760，支持缩放与最大化；未配置 API Key 时以离线演示模式运行，可直接体验完整流程。

## 模型配置

默认使用 DeepSeek `deepseek-chat`。复制 `.env.example` 为 `.env` 填入自己的 Key，或在应用「设置 → 模型 API」中填写（保存前自动做连通性测试，失败不落盘）：

```
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

支持任意 OpenAI 兼容接口（OpenAI、Moonshot、智谱、通义、SiliconFlow 等），改三行即可切换。

## 目录结构

```
adapters/      数据来源（剪贴板、Outlook COM）
preprocess/    邮件清洗（去引用/签名）与手册切块
rag/           索引（BM25+向量）、融合检索、持久化
agent/         编排、prompt、工具层
core/          LLM 客户端与数据模型
assets/fonts/  Inter 与 Noto Sans SC 字体
data/handbook/ 手册文件（md/txt/pdf/docx），含演示手册
data/styles/   语气样本（samples.json 为虚构演示数据）
data/index/    索引缓存（首次运行自动生成，可随时删除）
config.json    检索阈值、温度、规则等参数
```

## 测试

```powershell
.\.venv\Scripts\python -m pytest tests/ -q
```

## 隐私说明

- `.env`（含 API Key）、`data/styles/imported.json`（导入的真实邮件）、`data/index/`（缓存）均已加入 `.gitignore`
- 导入语气样本时自动将邮箱地址替换为 `***@***`
- 索引缓存签名包含嵌入模型与全部源文件指纹，手册变动自动重建
