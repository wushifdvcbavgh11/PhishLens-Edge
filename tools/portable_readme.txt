PhishLens U 盘便携版（Windows 10/11，x64）

1. 把整个 PhishLens-Portable 文件夹复制到 U 盘，不要只复制启动文件。
   也可以复制到目标电脑的磁盘后运行；首次读取模型时，U 盘速度会影响等待时间。
2. 双击“检查环境.bat”。应出现各组件的 [OK]；不需要另外安装 Python。
3. 双击“启动项目.bat”，等待 Api 和 Demo 显示 HTTP-ready。
   启动成功后会打开 Edge 中的本地演示页。
4. 首次使用这台电脑时，在 Edge 地址栏输入 edge://extensions/。
   开启开发人员模式，点击“加载解压缩的扩展”，选择本文件夹下 browser-extension。
   确认 PhishLens Edge Guard 的版本为 0.1.1，回到演示页刷新。
   若已有另一份 PhishLens 扩展，应先停用旧扩展，避免重复检测。
5. 演示地址：http://127.0.0.1:8001/fake_alipay.html
   只使用虚构信息。检测只提供风险线索，不代表安全保证。
6. 结束时双击“停止项目.bat”，关闭演示页，再安全弹出 U 盘。

重要说明
- 这是双击启动的便携版，不会插入自动执行、安装服务或修改浏览器设置。
- 浏览器扩展首次需要手动加载。U 盘盘符或文件夹改变后，可能需要重新加载目录。
- 不要在服务运行时移动文件夹或拔出 U 盘。
- 日志和本机进程记录在 %LOCALAPPDATA%\PhishLensPortable 下。
  不会把上一台电脑的进程记录当成下一台电脑的进程。
- 如果端口 8765/8001 已被其他软件占用，程序会提示并停止启动，不会关闭其他程序。
- 没有打包浏览历史、历史检测日志、训练集或原开发环境的运行记录。
- 自带已有模型和独立 Python；运行时不会自动下载模型或域名列表。
- 本版只提供浏览器检测，不包含原桌面悬浮窗。
- 通用图像模型的品牌候选只作诊断；证据不足时会显示“无法确认品牌”。
  品牌识别准确率仍待进一步验证，不保证所有演示页都会拦截。
- 已在开发电脑的隔离运行环境中验证；仍需在另一台实际 Windows 电脑验收。
  ARM/32 位 Windows、学校或公司禁止加载扩展的电脑不在本版支持范围。
- 如果提示缺少 DLL，可查看 Python/分析服务日志；部分电脑可能需要微软官方
  Visual C++ x64 运行库。不要从非官方网站下载单个 DLL。

官方说明
Edge 扩展加载：https://learn.microsoft.com/en-us/microsoft-edge/extensions/getting-started/extension-sideloading
Python 隔离运行：https://docs.python.org/3.11/using/windows.html#finding-modules
微软运行库：https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist

维护与自检
可在 PowerShell 中运行：
.\runtime\python.exe -B .\tools\check_portable.py --models --integrity
此命令校验打包文件，并在禁用网络连接的测试进程里运行真实模型。
浏览器重新加载后仍显示旧版本时，请核对其加载的 browser-extension 目录。
