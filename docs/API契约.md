# AML Add/Search 契约

核对日期：2026-10-25。[官方 API 指南](https://agentmemoryleaderboard.ai/api-guide)、[用户文档](https://agentmemoryleaderboard.ai/docs)。本页是实现映射，正式参评前仍需 Smoke 和冻结合同核对。

## 路由

- GET `/health`：无鉴权，检查 SQLite 可读。
- POST `/add`：request_id、user_id、session_id、非空 messages；消息包含 user/assistant role、content、可选 Unix 毫秒 timestamp。
- POST `/search`：query、user_id、top_k，选择题可带顶层 options；结果为 `{"data": [...]}`，按相关性排序，最多 top_k，空结果为 `{"data": []}`。

content/query 为非空字符串，或非空有序数组：`{"type":"text","text":"..."}` / `{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}`。JPEG/PNG/WebP，原图而非远程 URL；本实现核查 MIME 与实际图片格式，损坏或危险解码明确拒绝。

Add 200 返回 success=true 与原样三个 ID，仅在持久化且相关记忆可检索后成功。Search 项包含稳定 id、非空 content、数值 score、UTC created_at；不添加 metadata 包装或答案。

## 限制与错误

官方：每图最多10 MiB解码字节；Add请求和Search响应图片合计各最多30 MiB；正式 top_k=100上限。Answer 可用输入窗口为117,760 tokens，候选过多时按返回顺序保留前缀。本地 B0 尚未做精准视觉 token 估计。

本地补充：HTTP body 默认64 MiB可配置，query图片合计限30 MiB，未知字段和严格类型不符422；这些是显式资源/兼容边界，不冒充官方全部限制。

| HTTP | 含义 |
|---|---|
| 401 | 已配置服务密钥但认证失败 |
| 409 | 相同 scope/request_id 的正文冲突，不能视为成功 |
| 413 | 超过本地 HTTP body 上限 |
| 422 | 入参、时间或媒体格式/体积不符合契约 |
| 503 | 存储暂时不可用、已存媒体丢失或损坏 |

校验错误不回显输入文本、图片或凭据；日志避免正文。当前只实现同步200，不支持202状态查询。

## 认证与运行

TW_API_KEY 非空时，TW_AUTH_MODE 支持 bearer、token、x-api-key；Health保持公开。无Key用于本机开发，CLI禁止无Key绑定非回环地址；若直接以ASGI加载，部署者仍须设置Key和安全网关。认证密钥是参赛系统服务Key，不是 OpenRouter Key 或 AML Key。

## 模型与正式评测边界

开源方法榜指定 embedding 为 text-embedding-v4，LLM 组件为 gpt-4o-mini，reranker 不限。OpenRouter 对应服务标识为 openai/gpt-4o-mini；渠道、provider和快照准入尚待确认。平台 Answer/Eval 配置独立，不因本地调用同名LLM就相同。

正式记忆/问题由平台请求发送，不提供金标或完整套件下载；仅用于当前任务，不作为开发/训练材料。开发用上游公开数据，见评测方案。正式数据和派生副本依官方要求于任务结束后30天内删除，不能公开。
