# Feature flag 组合与回滚

权威默认值由 `modules/feature_flags.py` 提供，桌面设置在 `frontend/settings_dialog.py` 合并该默认值。启动时 `validate_feature_flags` 输出冲突或全关闭警告。

| Flag | 默认 | 职责 |
|---|---:|---|
| `unified_semantic_parser_enabled` | true | 启用唯一 SemanticActionParser 主入口 |
| `interaction_coordinator_enabled` | true | 启用补槽、选择、确认和消费状态 |
| `business_resolver_enabled` | true | 使用真实业务对象解析与 ID 验证 |
| `action_preview_enabled` | true | 在确认状态保存动作预览 |
| `action_batch_enabled` | true | 启用最多 3 项、有依赖/best-effort 的批处理 |
| `deterministic_response_enabled` | true | 回复事实骨架来自真实结果 |
| `unified_client_action_dispatcher_enabled` | true | 客户端动作唯一真实执行入口 |
| `legacy_intent_path_enabled` | false | 旧 IntentRouter 直接入口，仅回滚使用 |
| `legacy_direct_pet_action_enabled` | false | 旧桌宠动作 wrapper，仅紧急回滚 |

## 合法配置

- 默认：全部 unified 核心开启，两个 legacy flag 关闭。
- 语义兼容回滚：使用 `compatibility_rollback_profile()`；旧 intent 开启，语义协调/Resolver/Preview/Batch/确定性回复关闭，统一客户端 Dispatcher 仍保留。
- 单项诊断：可以关闭 Preview 或 Batch，但不能把新旧真实写路径同时开启。

## 启动警告

以下组合会报警：新旧 intent 同开、全部 intent 关闭、Preview 无 InteractionState、Batch/Resolver 无 unified parser、新旧 pet 动作同开、全部 pet 动作关闭。主链采用优先级而非双执行：unified intent 开启时不会进入 legacy intent；unified Dispatcher 开启时不会进入 legacy pet wrapper。
