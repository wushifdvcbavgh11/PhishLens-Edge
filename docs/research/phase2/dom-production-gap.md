# DOM生产适配差距清单

2026-10-27；只读审查开发目录0.2.8代码及三个相关测试文件，未控制日常Edge、未重载扩展、未启动浏览器测试、未修改生产协议。本公开候选包的原型来源摘要见provenance.json。以下“已有”表示源码实现及已存在测试覆盖，不代表本轮再次完成实机验证。

| 方面 | 生产现有能力及源码证据 | 第十项原型与差距 | 决策 |
|---|---|---|---|
| 本页结构版本 | content.js:432 hasPageMutation排除扩展自有节点；505 invalidatePage递增pageRevision、取消请求、清空判定/同意；536 pageSignature含URL、revision、DOM信号、form action；542 isCurrent比对请求序号与签名。结构版本并非原型首次提供。 | 原型增加UUID文档生命周期、显式采集序号、结构SHA-256、每个节点/原生form身份和跨可读根版本。不要将page_fingerprint（标题+密码数+前200字符的缓存信号，content.js:82）误认为生产唯一失效防线。 | 继续窄范围协议研究，复用已有revision，避免叠加两套互相失效的状态机。 |
| 截图与请求绑定 | background.js:15-29跟踪截图中tab激活、loading/URL、关闭；33-98校验顶层、活动tab、window及截图前后URL。content.js:677/703/726/738在等待模型、截图、网络、解析后用isCurrent拒绝旧请求；722把截图与随后DOM放同一payload。 | CAPTURE不携带requestId或documentId，仅返回dataUrl/tabId；ANALYZE回调由闭包关联，不显式回显截图/DOM哈希、采集版本。原型有前后采集和请求/结果哈希绑定，但不是像素原子事务。 | 继续研究最小显式绑定schema；不能称当前完全无绑定，也不能称已有强文档身份原子截图。 |
| 消息来源及tab/frame路由 | background.js:104-139仅runtime.onMessage内部桥；ANALYZE/MODEL_STATUS/CANCEL要求浏览器提供sender.tab、frameId===0、HTTP(S) URL及整数requestId；payload URL与sender同文档（只容许fragment差异）；key为tabId:documentId或URL:requestId；固定回环端点，credentials omit、redirect error、最大8并发、截图字符串32MiB上限。manifest无externally_connectable，无onMessageExternal桥。 | 生产来源信息来自浏览器MessageSender，强于普通页面JS自报身份；不能据缺少显式sender.id比对就断言任意网站可发内部消息。CAPTURE对缺省frameId较宽松，且不使用documentId；URL fallback不是强文档标识。原型是在受控页面主世界，未实现生产isolated-world认证或跨frame分发。 | 生产接入前审查端到端消息来源、文档fallback、重放和导航路由。原型UUID/哈希不是认证机制。 |
| 导航/SPA/BFCache | content.js:528 URL同步检查、1026 popstate/hashchange、1041附近500ms轮询；1028 pagehide暂停并invalidatePage，1035 persisted pageshow重新安排分析。tests/test_extension_state.cjs:737已有pagehide取消及BFCache恢复规则测试。background捕获same-URL loading与切换往返。 | 已有BFCache处理不能列作完全缺失；仍缺浏览器真实BFCache+异步结果返回组合证据，原型pagehide/pageshow也未穷尽该场景。full-document重载依赖新内容脚本实例，非显式持久document epoch。 | 不重复基础实现；未来只补真实生命周期组合验证。 |
| service worker重启 | background.js:15 pendingCaptures Set及103 localAnalyses Map仅在内存；finally清理/30秒AbortController；content.js:788附近扩展context失效提示与其他失败重试。 | 没有持久任务注册、恢复或跨SW实例epoch；重启后原cancel key映射与待截图事件状态不保证可恢复。回调断开/重发/重复后端任务的行为未通过本轮真实测试确认。原型Node回显也不模拟MV3进程生命周期。 | 生产评审前需单独本地扩展生命周期测试；不能宣称重启必然误报，也不能宣称已经解决。 |
| 无form、form归属、iframe/Shadow | content.js:151的collectDomSignals以顶层document查询；passwordForms使用原生form.elements，已有部分原生关系；visualDomContext通过祖先容器文字提供凭据上下文，未给节点稳定身份。manifest未设置all_frames，background明确拒绝非顶层分析。 | 原型可记录原生input.form及label、无form保持null，可遍历同源iframe和open root；生产不显式采集这些根，不应把DOM看不到与截图没有Logo等同。跨源权限、closed根仍不可读；未标记closed host无法普遍探测。 | 保持范围明确，先设计局部证据的不完整标记；不直接开启all_frames或扩大权限。 |
| 非原子与像素变化 | 生产截图前后检查tab/URL，本页signature能发现受观察的变更；截图和collectDomSignals先后执行。document观察器不穿透iframe/open shadow，不保证看见Canvas/video、CSSOM、动画/伪类的纯像素变化。 | 原型DOM前后括定、SHA绑定同样不能证明像素未改变，最终检查与UI渲染间还有竞争。第十项截图工具caret干扰修复仅适用该工具，不证明captureVisibleTab的完整行为。 | 不将SHA或“测试全通过”作为所有像素一致性保证；暂不接入强风险提示。 |
| 持续变化CPU/内存 | 生产顶层subtree所有属性/文本/childList观察，750ms防抖重检、500ms URL轮询，signature多次collectDomSignals；querySelectorAll先产生列表再slice，部分form/meta/文本扫描无节点总量上限。可能扫描开销、请求饥饿和频繁清空判定；自有UI排除减少自激。 | 原型根/输出有上限，但querySelectorAll/Array.from仍可能先物化完整子树；text TreeWalker也先拼接后截断，不能把输出上限当计算上限。每个可读根一观察器及Map需生命周期清理。48次小控制页采集中位0.6ms不代表繁忙页面持续成本。 | 暂停直接复制原型；要求CPU/内存/队列高水位与饥饿策略证据，之后再设计有限迭代扫描。 |

## 测试证据边界

只读检查test_background_analysis.cjs：固定端点/无Cookie、子frame和伪造URL拒绝、按document取消、fragment处理；test_background_capture.cjs：活动tab、切换往返、同URL重载、frame拒绝；test_extension_state.cjs：DOM失效、自有UI排除、过期回调、页面变化、防抖、pagehide/pageshow及管理页排除。它们是VM/模拟Chrome API测试源码，本轮未执行；不把测试名称当真实Edge认证证据。

第十项78项通过来自独立headless Edge控制页面及合成分析回显，不能证明生产扩展的isolated world、captureVisibleTab、真实服务分析、MV3重启或日常Edge已具备原型能力。

## 接入前置条件

先形成增量设计：沿用pageRevision与取消逻辑，补document/request/screenshot/DOM绑定和不完整作用域schema；说明sender真实性、tab/frame/document路由、失败与重启语义。只在隔离的本地扩展测试配置验证导航/同URL重载/BFCache/SW重启/晚到结果组合和忙碌页面开销。不能扩大权限绕过closed root，不把不可观察误当安全。满足证据后方可提出小范围生产集成评审，实际生产修改仍需新的明确任务。
