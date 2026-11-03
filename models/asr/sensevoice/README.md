# SenseVoice-Small · 本地语音识别

作者 / 来源：Alibaba Group / FunAudioLLM（阿里巴巴集团）。

- 原项目：https://github.com/QwenAudio/SenseVoice
- 官方模型卡：https://huggingface.co/FunAudioLLM/SenseVoiceSmall
- 运行引擎与固定导出：https://github.com/k2-fsa/sherpa-onnx
- 本目录的 `provenance.json` 记录下载包、安装文件和样例的 SHA-256。

使用官方 `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17` 的 INT8 ONNX 权重，不训练、不微调模型。权重为 239,233,841 字节，另需 315,894 字节词表。普通运行不会下载文件；源码用户执行 `python scripts/setup_voice_model.py --download` 准备，已有便携版可用 `--from-install 完整软件目录`。

模型权重使用 FunASR Model Open Source License Agreement v1.1，完整文本见 [完整模型许可](../../../licenses/SenseVoice-FunASR-MODEL_LICENSE.txt)。原始导出包 `LICENSE` 指向同一项目许可。模型卡的 `other` 许可与 sherpa-onnx 的 Apache-2.0 代码许可分别适用；保留来源、作者与模型名，其他条件以原文为准。

中文、英文自动识别；INT8 模型可以误识别。官方英语样例末词 gold 在开启 ITN 时会被识别成 code；界面允许修改识别结果。录音仅在内存中，本机 CPU 识别，转出的文字随后沿用应用默认的联网优先翻译策略。
