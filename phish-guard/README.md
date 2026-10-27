# PhishGuard：本地联合检测后台源码

本目录保存 PhishLens 第一阶段使用的 PhishGuard 修复代码、页面和对应测试。源版本为本地稳定提交 `b9543186963c341c1dcf384e12a10a746c72d9db`（本地标签 `phishguard-local-20261026`）。它与仓库根目录的 PhishLens 服务分别运行；第二阶段研究原型没有接入这里。

## 目录与发布范围

- `config/`、`src/`：配置、URL 特征提取、模型加载、数据库访问和 API。
- `web/`：后台页面、模型解释与联合检测记录展示。
- `tests/`：模型解释/API 测试和联合记录展示测试。
- `requirements.txt`：保留稳定版本依赖清单，未在新环境重装验证。
- [本地稳定版本记录](docs/releases/local-20261026.md)：历史验收证据及限制。
- [来源与同步范围](SOURCES.md)：代码来源和本次归档方式。

不包含模型权重、图库、数据库、原始 URL 数据、日志、历史截图、虚拟环境、训练脚本或私有维护材料。`src/ml/model.py` 和 `src/database/` 是运行源码，不是模型二进制或数据库文件。本次只做静态审查和源码发布，没有训练、推理、浏览器测试或部署。

## 本地运行条件

先配置可信的 Python 3.11 环境和依赖，并另行提供已核验且匹配的本地模型 `data/models/phishing_model.pkl`。不要加载来源不明的 pickle/joblib 文件。本地验收使用的模型版本与 SHA-256 记录在稳定版本说明中。缺少模型时，`/health` 可以返回 HTTP 200，但 `model_loaded` 为 false，不能当成检测成功。

在本目录执行命令，避免根目录与其他项目的 Python 包名冲突：

```powershell
cd phish-guard
python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

这只是启动说明，本次没有执行。先确认 8000 端口空闲；不要停止未知服务。数据库缺失时程序会创建空表，这不包含历史 PhishTank 数据或验收记录。需要恢复历史数据时使用本地私有备份，不将其加入 Git。

- 健康检查：`http://127.0.0.1:8000/health`，核对 `model_loaded` 和 `model_version`。
- 联合后台：`http://127.0.0.1:8000/shadow`，依赖 PhishLens 视觉服务的 `http://127.0.0.1:8765/shadow/events`。
- URL 分析桥：`POST /api/v1/analyze/url`；原视觉判定与联合建议分开显示。

运行方式限定本机。本次没有验收公开部署、认证配置或网络暴露。

## 测试与指标边界

`tests/test_model_insights.py` 的部分测试依赖带 specialist 的私有模型，不能用仅检出的源码宣称全部测试可复现。`tests/test_shadow_display.cjs` 使用 DOM stub，不是日常 Edge 实机测试。历史测试次数见稳定版本说明，本次没有重新执行。

后台指标来自已加载模型保存的反复使用开发测试集，不等于独立真实网站准确率。风险分数不是已校准安全概率；未命中数据库或低分不保证网站安全。原上游 README 的准确率宣传和默认训练步骤不作为本项目的发布结论。
