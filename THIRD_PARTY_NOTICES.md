# 第三方组件声明

兔兔翻译使用下列开源组件。版本和许可证根据本次实际打包环境的已安装分发包元数据及许可证文件核对。原始许可证与声明按组件保存在 `licenses/`，适用于组件本身；请保留这些文件。

## 运行组件

| 组件 | 本次版本 | 许可证 | 上游源码 |
| --- | --- | --- | --- |
| CPython / tkinter | 3.10.6 | Python Software Foundation License 及所附声明 | [CPython](https://github.com/python/cpython/tree/v3.10.6) |
| Tcl / Tk | 8.6 系列 | Tcl/Tk 许可证 | [Tcl](https://www.tcl-lang.org/software/tcltk/license.html) |
| CustomTkinter | 6.0.0 | MIT，以分发包 `LICENSE` 为准 | [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) |
| Pillow | 12.3.0 | MIT-CMU | [Pillow](https://github.com/python-pillow/Pillow) |
| Requests | 2.34.2 | Apache-2.0 | [Requests](https://github.com/psf/requests) |
| pystray | 0.19.5 | LGPL v3 | [pystray 0.19.5](https://github.com/moses-palmer/pystray/tree/v0.19.5) |
| darkdetect | 0.8.0 | BSD-3-Clause | [darkdetect](https://github.com/albertosottile/darkdetect) |
| packaging | 26.3 | Apache-2.0 或 BSD-2-Clause | [packaging](https://github.com/pypa/packaging) |
| six | 1.17.0 | MIT | [six](https://github.com/benjaminp/six) |
| charset-normalizer | 3.5.2 | MIT | [charset-normalizer](https://github.com/jawah/charset_normalizer) |
| idna | 3.20 | BSD-3-Clause | [idna](https://github.com/kjd/idna) |
| urllib3 | 2.8.0 | MIT | [urllib3](https://github.com/urllib3/urllib3) |
| certifi | 2026.7.22 | MPL-2.0 | [certifi](https://github.com/certifi/python-certifi) |

CustomTkinter 6.0.0 元数据的 `License` 字段与所附文件不一致；所附 `LICENSE` 完整内容是 MIT License，因此这里按原始许可证文件列示。Requests 的原始 `NOTICE` 也已随附。Pillow 的许可证涵盖其主体；编译组件的附带声明以其原始文件与上游发行资料为准。CPython 原始许可证中包括其捆绑组件的声明。

pystray 的 LGPL 许可证与其引用的 GPL 许可证全文分别保存在 `licenses/pystray/`。本应用源码随源码包提供；pystray 上游源码可由上表的对应版本链接取得。可以在源码环境中安装修改后的 pystray，再运行或重新打包应用，以替换该库。第三方库未在本应用中修改。

## 构建工具

| 组件 | 本次版本 | 许可证 | 上游源码 |
| --- | --- | --- | --- |
| PyInstaller | 6.22.3 | GPL-2.0-or-later，附 Bootloader Exception | [PyInstaller](https://github.com/pyinstaller/pyinstaller) |
| pyinstaller-hooks-contrib | 2026.8 | 构建钩子 GPL-2.0-or-later；运行时钩子 Apache-2.0 | [社区钩子](https://github.com/pyinstaller/pyinstaller-hooks-contrib) |
| altgraph | 0.17.5 | MIT | [altgraph](https://github.com/ronaldoussoren/altgraph) |
| pefile | 2024.8.26 | MIT | [pefile](https://github.com/erocarrera/pefile) |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | [pywin32-ctypes](https://github.com/enthought/pywin32-ctypes) |

PyInstaller 的 bootloader 例外许可将其编译引导器及相关文件与其他程序组合分发，完整条款见 `licenses/pyinstaller/`。构建工具列示不意味着整套构建工具都随可执行程序分发。

Google 是本版使用的远程服务，不是随软件分发的开源库；本版不再请求 MyMemory。接口说明、限制与联网提交方式见 [README.md](README.md)。

## 发音词典与系统语音

离线发音词典采用 [CMU Pronouncing Dictionary](https://github.com/cmusphinx/cmudict)，固定提交 `74790861f652b15e4ac49015a90074ad62a27690`，版权及 BSD 类许可全文随资源放在 `assets/cmudict.LICENSE`；上游说明及下载 / SHA-256 校验记录在 `assets/cmudict.README`、`assets/cmudict.provenance.json`。源 ARPABET 数据仅做 gzip 压缩，本程序另外转换为宽式美式 IPA。便携版资源在 `_internal/assets/`。

朗读调用 Windows 自带 System.Speech 和已安装的本地语音。微软语音本身不随本应用复制分发，适用其系统组件许可。本应用的语音桥接代码不依赖额外第三方朗读库。

## 离线翻译与截图识别组件

| 组件 | 版本 | 许可 / 上游 |
| --- | --- | --- |
| CTranslate2 | 4.8.3 | MIT；https://github.com/OpenNMT/CTranslate2 |
| SentencePiece | 0.2.2 | Apache-2.0；https://github.com/google/sentencepiece |
| NumPy / OpenBLAS | 2.2.6 | BSD-3-Clause 及分发包附带许可；https://github.com/numpy/numpy |
| PyYAML | 6.0.3 | MIT；https://github.com/yaml/pyyaml |
| RapidOCR | 1.4.4 | Apache-2.0；https://github.com/RapidAI/RapidOCR |
| PaddleOCR ONNX 检测 / 识别 / 方向模型 | PP-OCRv4 / mobile-v2 | Apache-2.0；https://github.com/PaddlePaddle/PaddleOCR |
| ONNX Runtime CPU | 1.23.2 | MIT；https://github.com/microsoft/onnxruntime |
| OpenCV headless | 4.10.0.84 | Apache-2.0，另附第三方组件许可；https://github.com/opencv/opencv-python |
| pyclipper | 1.4.0 | MIT；https://github.com/fonttools/pyclipper |
| Shapely / GEOS | 2.1.2 | BSD-3-Clause / LGPL-2.1；https://github.com/shapely/shapely |
| tqdm | 4.70.1 | MPL-2.0 AND MIT；https://github.com/tqdm/tqdm |
| FlatBuffers | 25.12.19 | Apache-2.0；https://github.com/google/flatbuffers |
| Protobuf | 7.36.2 | BSD-3-Clause；https://github.com/protocolbuffers/protobuf |
| coloredlogs / humanfriendly | 15.0.1 / 10.0 | MIT；https://github.com/xolox/python-coloredlogs |
| colorama | 0.4.6 | BSD-3-Clause；https://github.com/tartley/colorama |

实际运行环境版本、附带许可文件见 `licenses/runtime-versions.json` 与各组件目录。原有许可证请随软件保留。动态库均未修改；可在源码环境替换相应库后重新构建。构建中排除了模型转换、训练、视频读取和人脸检测等本软件未使用的组件。

翻译模型来自 Argos 官方模型索引中的 `en_zh`、`zh_en` 1.9 包，衍生自 OPUS-MT，采用 **CC BY 4.0**。作者 Jörg Tiedemann 和 Santhosh Thottingal，作品 OPUS-MT — Building open translation services for the World，EAMT 2020。模型权重未修改；移除了未使用的 Stanza 分句模型，重新命名目录。来源、SHA-256、完整许可和原始署名 README 在 `models/`，详情见 `models/README.md`。不得以 Argos 索引项目的 MIT 许可替代模型自身的 CC BY 4.0。

OCR 模型来自 RapidOCR 1.4.4 分发包：`ch_PP-OCRv4_det_infer.onnx`、`ch_PP-OCRv4_rec_infer.onnx`、`ch_ppocr_mobile_v2.0_cls_infer.onnx`，位于 `_internal/rapidocr_onnxruntime/models/`。上游许可分别保存为 `licenses/RapidOCR-LICENSE.txt` 与 `licenses/PaddleOCR-models-LICENSE.txt`。
