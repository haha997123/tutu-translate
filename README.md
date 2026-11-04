# 兔兔翻译 · Tutu Translate

Windows 中英快捷翻译工具，带小兔界面、输入即译、本地语音转文字、截图翻译、联网优先 / 本地兜底、朗读、参考音标和每日英语学习。

<img src="assets/mascot.png" alt="兔兔翻译的小兔" width="150">

## 下载即用

普通用户请到 **[Releases 下载完整便携版](https://github.com/haha997123/tutu-translate/releases/latest)**，在 Assets 中选择 `TutuTranslate-1.0.0-Windows-x64.zip`，完整解压后双击 `TutuTranslate/兔兔翻译.exe`。适用于 64 位 Windows 10/11，无需安装 Python、申请 API Key 或另外下载模型。请保留同目录的 `_internal`、`models` 和 `licenses`，不要把 EXE 单独拿出来。`SHA256SUMS.txt` 提供下载包校验值。

便携包已附带中英离线翻译、OCR 与 SenseVoice-Small 语音识别模型，断网可用。**默认有网时会尝试 Google 翻译，失败后转本地；语音与截图图像始终在本机识别，转出的文字仍沿用该翻译策略。** 系统朗读依赖电脑已有的中英文语音。

GitHub 自动提供的 `Source code (zip/tar.gz)` 是源码，运行库和模型权重需要按下文准备。仓库保留代码、资源、测试和第三方许可。本工具主要用于日常中英交流和英语学习，未针对专业论文翻译设计。

## 使用

双击“兔兔翻译.exe”或桌面快捷方式。输入或粘贴文字，停顿约 550 毫秒即提交翻译；遇到汉字时译成英文，其余译成中文。无需选择方向或点击翻译按钮。一次最多 5000 个字符。

| 快捷键 | 功能 |
| --- | --- |
| Alt + Q | 呼出窗口 |
| Alt + W | 粘贴并翻译当前剪贴板文字 |
| Alt + E | 框选截图翻译 |
| Ctrl + R | 语音输入 / 结束录音（翻译页内） |
| Ctrl + Enter | 立即处理当前输入 |
| Ctrl + L | 清空 |
| Esc / 关闭窗口 | 收到托盘；截图时 Esc 取消框选 |

点击“截图翻译”或按 Alt+E，屏幕轻微变暗后拖动框选中英文字；紫色边框内保留原亮度，松开鼠标后在本机识别并翻译。按 Esc 或右键可取消框选。截图不写文件、不上传；识别出的文字会填入原文框。识别结果可能有误，可直接改字。支持多屏和位于主屏左侧的屏幕。复杂表格、竖排、小字及多栏阅读顺序可能不准确。

“保持在最前”开启后，窗口会显示在其他普通窗口上方。右键系统托盘小兔图标选择“退出”，完全关闭程序。快捷键被其他软件占用时会显示提示，仍可使用界面和托盘菜单。

## 语音输入

点击“语音输入”开始说中文或英文，再点“结束录音”，在本机转成文字后填入原文框，沿用输入即译。也可在翻译页按 Ctrl+R 开始 / 结束。一次最多 60 秒；首次识别会加载模型，耗时取决于电脑。它是录完后识别，不是边说边出字。录音前会停止软件朗读，避免把自己的声音录进去。

识别使用阿里巴巴的成品 **SenseVoice-Small**，通过 **sherpa-onnx** 的固定 INT8 ONNX 导出在 CPU 上运行，没有训练自制语音模型。语音模型权重约 228 MiB，录音与识别均无需联网、显卡或云端账号。模型会听错字词，噪声、口音、远距离说话及专有名词会影响效果，可在输入框修改。缺少设备或权限时，会显示错误；可到 Windows“设置 → 隐私和安全性 → 麦克风”允许桌面应用访问。

Windows 原生词表读取按系统文件编码处理，已验证本机中文目录可用。若其他电脑提示目录字符不受支持，可将整个软件文件夹移到 `D:\TutuTranslate` 等英文路径。

原文在成功识别前保留。录音 / 识别期间编辑、清空、粘贴、截图、切换学习页、隐藏或退出窗口会取消本次语音输入，迟到的结果不会覆盖文字。仅在点按钮或按快捷键后打开麦克风，录音只保存在内存，不写音频文件，也不上传音频。**识别出来的文字会按现有策略自动发送给 Google 翻译**；断网时由本地翻译接替。

## 每日英语学习

点击侧栏“英语学习”，按学习卡练习日常生活和上网交流的英语。内置原创 200 个单词/词组、100 个句子，分为四档难度，每个单词配有英文例句和中文意思。词库、单词参考音标与系统朗读均可离线使用，不调用在线生成接口。

默认每天 10 个单词、5 个句子；可将单词设为 1–30 个、句子设为 1–15 个，选择难度与“上网 + 日常 / 日常生活 / 上网交流”，点击“保存”更新计划。今天已学过的卡片会保留，其余部分按新设置准备；仅选一个主题时，数量会受该难度可用卡片数限制。

点击“朗读英语”听单词或句子，单词还有“读例句”；开启“慢速”后下一次播放放慢。可以“收起中文”先读英文再核对。点“记住了”或“再练一练”记录今天学过并跳到下一张；后者安排次日复习，前者按 1、3、7、14、30 天逐步延长复习间隔。复习卡计入每天数量。词库用完会继续轮换复习。

每天完成目标后显示进度和连续完成天数；每完成 3 天会给出尝试更高难度的提示，难度由你手动选择。四档用于本工具内部练习分级，不是英语水平考试或正式等级认证。

学习记录保存于 `%LOCALAPPDATA%\TutuTranslate\learning.json`，仅含词卡 ID、目标、日期及学习进度，不记录翻译和截图文字。重新打开会保留今天的计划，跨天生成下一份；换电脑想保留学习进度，可在关闭软件后将这个文件复制到新电脑的相同位置。

## 联网与离线

程序使用此电脑当前网络及其配置的代理。默认先请求 Google 公开网页翻译接口；明确连接失败即本地接替，约 0.8 秒仍无响应也启动本地翻译。程序启动时后台预热两个模型。首次准备、长文字、较慢电脑会增加耗时，不保证所有翻译都在 0.8 秒内完成。网络错误会短暂冷却，避免每次输入重复等待；后续自动重新尝试联网。迟到的联网译文不会覆盖已经显示的本地译文。

完整便携版附带本地中→英、英→中模型、分词器和 OCR 模型，正常使用不再下载，断网也能翻译和识别截图，无需显卡或 Python。源码仓库的模型准备方法见下文。本地模型较小，对复杂长句、术语、混合中英及歧义的处理可能不如在线服务。段落空行保留，长段落会分段处理。

Google 接口无需本工具 API Key，但不是受支持的 Google Cloud Translation API，可能变更、限流或停止服务。本版不再连续等待 MyMemory 等备用服务器。若本地模型缺失或文件被删，也会提示无法完成；请完整保留 `_internal`、`models` 文件夹。

## 朗读和音标

原文、译文右上角均可“朗读”，再点一次停止。选中文字时只读选区；“慢速朗读”控制下一次播放。使用 Windows 本地中英文语音，不联网。缺少语音时，可在 Windows“时间和语言 → 语音”添加对应的文本转语音组件，下载一次后可离线朗读。

输入或选中一个英文词，即显示美式参考音标，并可点“听单词”。随附 CMUdict 约 12.6 万词条，源 ARPABET 转写为宽式 IPA，重音位置按音节规则整理；多读音未按上下文区分，不提供整句音标，生词不生成猜测音标。

## 隐私

输入停顿、粘贴翻译、截图或语音识别完成后，原文默认可能发送至 Google。截图图像、麦克风音频只在内存中、本机处理；识别文字按相同翻译策略提交。朗读、音标、本地模型都在本机执行。没有翻译历史、截图 / 录音文件或内容日志；内存缓存最多 256 条，30 分钟过期，退出后清除。软件不会持续读取剪贴板、捕获屏幕或监听麦克风。

## 搬到其他电脑

适用于 64 位 Windows 10 / 11。复制整个软件文件夹，或完整解压便携包后运行，无需源码、Python 或另外下载翻译 / OCR 模型。离线朗读依赖目标电脑的系统语音。不要单独拷贝 exe。

`models/README.md`、`models/provenance.json` 保存模型来源、署名、体积及 SHA-256；许可证见 `THIRD_PARTY_NOTICES.md`、`licenses/` 和模型文件夹。

## 源码运行与构建

Python 3.10 x64。运行依赖使用无界面的 OpenCV，减少体积；RapidOCR 需单独以 `--no-deps` 安装，防止它再次拉取带界面的 OpenCV。包名依赖警告不影响已验证的 cv2 接口。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps rapidocr-onnxruntime==1.4.4
.\.venv\Scripts\python.exe scripts/setup_models.py --download
.\.venv\Scripts\python.exe scripts/setup_models.py --verify
.\.venv\Scripts\python.exe scripts/setup_voice_model.py --download
.\.venv\Scripts\python.exe scripts/setup_voice_model.py --verify
.\.venv\Scripts\python.exe app.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`--download` 仅在明确执行时联网下载两个固定版本的 Argos 官方模型包，并校验来源记录中的 SHA-256；只安装运行所需文件。已有完整便携版时也可用 `python scripts/setup_models.py --from-install "你的软件目录"` 从本机复制并校验。源码仓库不重复保存约 162 MiB 的模型权重，程序启动时不会自动下载它们。模型服务器暂不可用时可以稍后重试，或从本机完整安装复制。

OCR 权重随 `rapidocr-onnxruntime` 分发包安装，构建时一并收集。朗读依赖 Windows 自带的语音组件。

`scripts/setup_voice_model.py --download` 明确下载固定的官方 sherpa-onnx SenseVoice 导出包，校验压缩包和安装文件的 SHA-256；也支持 `--archive 包路径` 或 `--from-install 完整软件目录`。只安装 INT8 模型、词表和原始许可，保留仓库中的模型来源与许可说明。语音权重不提交 GitHub，普通启动不会下载模型。模型清单在 `models/asr/sensevoice/provenance.json`。

使用官方导出包中的 `test_wavs` 可运行 `python app.py --dictation-self-test 报告.json --dictation-audio-dir 官方包/test_wavs`，验证真实本地引擎及中英文样例；不会打开麦克风。`--dictation-ui-smoke 报告.json` 仅用假服务检查界面和输入保护，不代表识别准确率。

在上述虚拟环境中运行 `build.ps1`，会检查模型及分词器、打包程序，并把模型放到 EXE 同级目录。可用 `--combined-self-test 报告.json` 执行真实离线翻译、合成图片 OCR 和无声系统朗读检查；不捕获真实屏幕。

仓库不包含个人学习进度、翻译历史、截图、账号密钥或已安装的运行库。`.gitignore` 排除这些本机文件、构建结果及模型权重。

## 许可说明

第三方组件、词典和模型按各自许可使用，来源与完整声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)、`licenses/` 和 `models/`。应用自身的原创代码目前未另行指定开源许可证；公开仓库与第三方许可不自动为原创部分授予额外许可。

参考：[Argos 官方模型索引](https://github.com/argosopentech/argospm-index)、[CTranslate2](https://opennmt.net/CTranslate2/)、[RapidOCR](https://github.com/RapidAI/RapidOCR)、[微软语音说明](https://support.microsoft.com/en-us/accessibility/windows/narrator/appendix-a-supported-languages-and-voices)。
