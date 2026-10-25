# D.1：DeepSeek 接入契约与预算控制

2026-10-25 的交付与运行快照；当前状态见 [TODO](../../TODO.md)。

- **范围**：官方 DeepSeek JSON 薄适配、持久预算账本与合成 `model-check`；模型检查独立于 B0 Add/Search。调用与失败机制见 [开发设计](../开发设计.md#模型调用与预算)。
- **复现入口**：在仓库根目录按 [README](../../README.md#install-and-verify) 运行离线检查；已有真实产物为 `runs/d1-live-2026-10-25/summary.json` 和同目录 `vision-fixture.png`（Git 忽略）。产物不存在时，可按 README 的 Model contract check 使用新目录付费复跑，验收不要求再次调用。
- **实际结果**：离线测试 **75项通过**，ruff 检查和24个Python文件格式检查通过；测试使用 MockTransport。真实3次调用无重试：文本返回ready；合成图读出TW731、蓝色正方形、红色圆形；排序返回白名单fixture_a、fixture_b。输入758、输出57 tokens，按当日核对峰时价格逐次向上取整估算 **0.000296美元**，当时账本剩余 **4.999704美元**，未知费用预留为0。
- **限制**：真实响应仅报告 `deepseek-flash` 别名，未锁定后端快照；合成检查不证明数据集检索或QA收益，模拟失败测试不等于供应商故障注入。测试仍有Starlette/httpx弃用提示。费用为固定价格假设下的估算，账单以供应商为准。
- **人工判断**：检查合成图、summary与上述边界，判断是否具备进入视觉注释写入的条件；验收后再推进下一片。

价格来源：[官方模型/价格](https://api-docs.deepseek.com/quick_start/pricing/)，核对日期2026-10-25。实现位置：`adapters/models/deepseek.py`、`adapters/budget.py`、`evaluation/model_check.py`。
