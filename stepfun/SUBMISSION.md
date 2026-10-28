# 提交与验收入口

- **提交分支：** `2026-competition-ecosystem`
- **开发基线：** `d647275c308e09b7b623f322a26f5c4e85734fb0`（上游同名分支）
- **提交仓库：** <https://github.com/chamaamir03-cell/powercontext>
- **项目目录：** `stepfun/`
- **评测报告：** [WebGPU FP16](./reports/measurement-2026-10-29.json) 和 [WASM Q8](./reports/measurement-2026-10-29-wasm.json)，均由应用实际浏览器评测导出，含 FP32 与量化配置的首次轮、5 次预热轮、分阶段耗时和检索质量指标。

评委应使用本次提交提供的 branch 和 commit ID 检出源码；检出后在本目录运行：

```bash
npm ci --ignore-scripts --legacy-peer-deps
npm run dev
```

使用 Node.js 22+ 和最新版 Chrome/Edge，访问终端打印的 `http://127.0.0.1:4173`。WSL/Linux 环境要求 Linux 版 Node。首次启动可能需要从 Hugging Face 下载模型；首次 Linux 启动还需要下载 seekdb 原生绑定。完整依赖、数据存储、浏览器要求和验收步骤见 [README.md](./README.md)。

可重复验证：

```bash
npm test
npm run build
npm run test:acceptance-api
```

其中 API 验收需先启动应用。量化模型的浏览器端评测可在页面点击“运行端侧评测”；自动降级模拟入口为 `?acceptance-webgpu-unavailable=1`。归档报告是特定浏览器与设备上的小样本结果，不替代目标设备和比赛数据集上的正式复测。
