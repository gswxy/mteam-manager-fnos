# 第三方项目声明（Third Party Notices）

本项目（馒头管家 / mteam-manager-fnos，MIT，© 2026 gswxy）在开发过程中参考或捆绑了以下第三方开源项目。
依据各项目的许可证要求，在此保留其版权与许可声明。本项目作者署名不覆盖以下任何原作者的权利。

---

## 1. M-Team-Helper（参考，未直接复制代码）

- 项目：<https://github.com/spellyaohui/M-Team-Helper>
- 作者：spellyaohui
- 许可证：MIT（声明于项目 README：「MIT（见各子项目声明；作者 spellyaohui）」）
- 使用方式：本项目 `backend/app/services/mteam.py` 中的 M-Team API 端点行为
  （POST / JSON 与表单编码区分、响应信封 `code=="0"` 字符串判定、种子 `status`
  子对象与全站促销 `promotionRule` 合并、`discountEndTime` 双格式兼容等）参考了该
  项目经真机验证的实现经验，为独立重写，未逐行复制其源码。
- 原版权声明（依据其 README 的 MIT 声明）：

  > Copyright (c) spellyaohui. Released under the MIT License.
  >
  > Permission is hereby granted, free of charge, to any person obtaining a copy
  > of this software and associated documentation files (the "Software"), to deal
  > in the Software without restriction... [MIT License 全文见 LICENSE]

## 2. FNOS-API（思路参考，未复制代码）

- 项目：<https://github.com/heijiangangqin/FNOS-API>
- 作者：heijiangangqin
- 许可证：README 中声明 MIT（仓库内无许可证全文）
- 使用方式：仅参考其对飞牛 fnOS 下载中心 RPC（`appcgi.downloadcenter.*`）调用方式的
  公开描述与 FPK 打包实践。本项目对 fnOS 下载中心的对接
  （`backend/app/services/fnos_ws.py`：WebSocket `user.login` + HMAC-SHA256 签名 +
  分块响应重组）为独立逆向实现，并在真实 fnOS 设备上验证，未复制其任何源码或捆绑其
  二进制。

## 3. python-build-standalone（捆绑分发）

- 项目：<https://github.com/astral-sh/python-build-standalone>
- 作者：astral-sh 及 CPython 贡献者
- 许可证：Python Software Foundation License Version 2（PSF-2.0）
- 使用方式：FPK 安装包内 `app/runtime/` 捆绑其 CPython 3.11「install_only」构建，
  使应用安装后无需依赖 NAS 系统 Python。构建时从其官方 Release 下载。
- 原版权声明（PSF-2.0）：

  > PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2
  > --------------------------------------------
  > 1. This LICENSE AGREEMENT is between the Python Software Foundation
  > ("PSF"), and the Individual or Organization ("Licensee") accessing and
  > otherwise using this software ("Python") in source or binary form and
  > its associated documentation.
  > 2. Subject to the terms and conditions of this License Agreement, PSF hereby
  > grants Licensee a nonexclusive, royalty-free, world-wide license to reproduce,
  > analyze, test, perform and/or display publicly, prepare derivative works,
  > distribute, and otherwise use Python alone or in any derivative version...
  > [全文见 https://docs.python.org/3/license.html]

## 4. 后端运行依赖（PyPI，随运行时安装）

| 包 | 许可证 | 版权 |
|---|---|---|
| fastapi / starlette | MIT | © 2018 Sebastián Ramírez |
| uvicorn | BSD-3-Clause | © 2017-present Encode OSS Ltd. |
| sqlalchemy | MIT | © 2005-2025 Michael Bayer and contributors |
| apscheduler | MIT | © Alex Grönholm |
| httpx / httpcore / anyio / sniffio | BSD-3-Clause | © 2017-present Encode OSS Ltd. 等 |
| websockets | BSD-3-Clause | © 2011-2026 Aymeric Augustin and contributors |
| pydantic / pydantic-core / annotated-types | MIT | © 2017-present Pydantic Services Inc. and contributors |
| python-multipart | Apache-2.0 | © 2006-2026, Andy Klardiff 等 |
| itsdangerous | BSD-3-Clause | © 2011 Pallets |
| typing-extensions | PSF-2.0 | Python 贡献者 |

各许可证全文见 PyPI 项目页或随包分发的 LICENSE 文件。

## 5. 前端运行依赖（npm，构建产物内联）

| 包 | 许可证 | 版权 |
|---|---|---|
| react / react-dom | MIT | © Meta Platforms, Inc. and affiliates |
| react-router-dom | MIT | © Remix Software Inc. |
| vite / @vitejs/plugin-react | MIT | © Yuxi (Evan) You and Vite contributors |
| typescript | Apache-2.0 | © Microsoft Corporation |

## 6. 商标说明

- 「M-Team」「馒头」及相关商标归其站点所有者所有，本项目为第三方工具，与站点无隶属关系。
- 「飞牛」「fnOS」「fnnas」及相关商标归广州飞牛科技有限公司所有，本项目为社区第三方应用，
  未获官方认证。
