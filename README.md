# WxPayPcNotify（新版微信 OCR 版）

基于 [likeyun/liKeYun_WxPayPcNotify](https://github.com/likeyun/liKeYun_WxPayPcNotify) 改造。

新版微信 PC（Qt）已无法用旧版 `ChatWnd` 控件读取收款文字，本仓库改为：

1. 后台截取「微信收款助手 / 微信支付」窗口（不抢焦点）
2. RapidOCR 识别收款金额 / 来自 / 到账时间
3. POST 通知本地发卡插件：`http://127.0.0.1:17888/wxpay/notify`

## 运行（Python）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -i https://pypi.org/simple
python WxPayPcNotify.py
```

## 打包 exe

```powershell
.\build_exe.ps1
# 输出：dist\WxPayPcNotify.exe
```

## 使用注意

- 打开「微信收款助手」独立窗口，滚到能看到「收款金额」的消息
- 窗口可被遮挡，但不要最小化
- 修改脚本顶部 `SERVER_URL` / `NOTIFY_TOKEN` 对接你的通知地址
