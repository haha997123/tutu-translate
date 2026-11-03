# 中英离线模型

本目录记录 Argos Translate 官方模型索引提供的两个 1.9 版本模型。源码仓库仅含来源、许可、配置和词表，未提交 `model.bin` 权重与 `sentencepiece.model` 分词器：

- `en_zh`：英语 → 简体中文。
- `zh_en`：中文 → 英语。

官方索引：https://github.com/argosopentech/argospm-index

下载地址、原始压缩包 SHA-256，以及每个保留文件的大小和 SHA-256 记录在 `provenance.json`。
权重、SentencePiece 分词器、配置和词表原样保留。为减小体积，未包含原始包的 Stanza 分句模型；软件使用本地分句，直接通过 CTranslate2 在 CPU 上进行 int8 推理，无需 PyTorch、显卡或联网下载。

从仓库根目录运行 `python scripts/setup_models.py --download` 明确下载并校验缺失文件，或运行 `python scripts/setup_models.py --from-install "完整便携版目录"` 从已有安装复制；`python scripts/setup_models.py --verify` 检查所有文件。准备完毕后运行应用不再下载模型。上段“原样保留”是指完整安装后的模型文件内容，不表示源码仓库已经附带权重。

## 署名与许可

两个原始包的 `README.md` 均声明：所衍生的原始 OPUS 模型采用 **Creative Commons Attribution 4.0 International（CC BY 4.0）**。
这些原始 README 随各自模型保留。完整许可文本为本目录的 `LICENSE-CC-BY-4.0.txt`，许可链接：
https://creativecommons.org/licenses/by/4.0/

作者：Jörg Tiedemann 和 Santhosh Thottingal。

作品：OPUS-MT — Building open translation services for the World.
Proceedings of the 22nd Annual Conference of the European Association for Machine Translation（EAMT），2020，Lisbon, Portugal。

OPUS-MT 项目：https://github.com/Helsinki-NLP/OPUS-MT-train

Argos Translate 打包项目：https://github.com/argosopentech/argos-translate

本软件没有修改模型权重；仅移除了未使用的 Stanza 分句文件，并将模型目录重新命名。
