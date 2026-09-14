# -*- coding: utf-8 -*-
"""
新版微信 PC（Qt）收款监听：截取支付相关窗口并 OCR 解析收款通知。
旧版 ChatWnd 控件方案对新版无效。
"""
import ctypes
import re
import time

import numpy as np
import requests
import uiautomation as automation
import win32gui
import win32ui
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

# 接收通知的 Url（故里群管发卡插件本地监听）
SERVER_URL = "http://127.0.0.1:17888/wxpay/notify"
# 若私信配置了 token，在此填写同一值；否则留空
NOTIFY_TOKEN = ""

# 优先匹配的窗口标题（新版微信）
WINDOW_TITLES = ("微信收款助手", "微信支付")

# 截图 / OCR 间隔（秒）；勿过短，OCR 较吃 CPU
POLL_INTERVAL = 3

_last_notify_key = None
_last_image_hash = None
_ocr = None
_miss_count = 0


def _enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_ocr():
    global _ocr
    if _ocr is None:
        _ocr = RapidOCR()
    return _ocr


def list_candidate_windows():
    """按优先级返回可监听窗口：(uiautomation控件, 标题)。"""
    found = []
    seen = set()
    root = automation.GetRootControl()
    for child in root.GetChildren():
        try:
            name = child.Name or ""
            cls = child.ClassName or ""
            if "Qt51514QWindowIcon" not in cls:
                continue
            if name in seen:
                continue
            # 优先收款助手 / 微信支付；也接受标题里带收款的窗口
            if name in WINDOW_TITLES or ("收款" in name and "微信" in name):
                seen.add(name)
                found.append((child, name))
        except Exception:
            continue

    # 按 WINDOW_TITLES 顺序排序
    order = {t: i for i, t in enumerate(WINDOW_TITLES)}
    found.sort(key=lambda x: order.get(x[1], 99))
    return found


def capture_window(win):
    """后台截取窗口，不抢焦点、不置顶（避免电脑没法操作）。"""
    hwnd = win.NativeWindowHandle
    if not hwnd:
        return None

    # 最小化时 PrintWindow 常空白，只提示，绝不自动还原抢焦点
    if win32gui.IsIconic(hwnd):
        return None

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(bitmap)

    # PW_RENDERFULLCONTENT=2：后台也能抓到 Qt/微信画面
    ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
    bmpinfo = bitmap.GetInfo()
    bmpstr = bitmap.GetBitmapBits(True)

    img = Image.frombuffer(
        "RGB",
        (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
        bmpstr,
        "raw",
        "BGRX",
        0,
        1,
    )

    win32gui.DeleteObject(bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    if not ok:
        # 不再用 ImageGrab 全屏截取（会受遮挡影响，也更慢）
        return None
    return img


def image_changed(img):
    """画面没变就跳过 OCR，减轻卡顿。"""
    global _last_image_hash
    # 缩小后再哈希，足够判断是否有新消息刷新
    small = img.resize((64, 64))
    h = hash(small.tobytes())
    if h == _last_image_hash:
        return False
    _last_image_hash = h
    return True


def ocr_text(img):
    engine = get_ocr()
    result, _ = engine(np.array(img))
    if not result:
        return ""
    lines = [item[1] for item in result if item and len(item) >= 2 and item[1]]
    return "\n".join(lines)


def parse_payment(text):
    """
    兼容两种版式：
    1) 旧版聊天：收款金额￥x / 来自xx到账时间xx备注
    2) 新版收款助手卡片：收款到账通知 + 09月14日 14:27 + 收款金额 / ￥1.00
       （个人收款服务通常没有「来自」）
    """
    # 去掉易干扰的底部导航 / 按钮文案
    noise = (
        "经营指南", "小账本", "收款码", "查看详情", "查看经营报表",
        "个人收款服务", "点击可查看详情", "已存入零钱",
    )
    cleaned = text or ""
    for n in noise:
        cleaned = cleaned.replace(n, " ")

    flat = re.sub(r"\s+", "", cleaned).replace("¥", "￥")

    amount = None
    sender = None
    timestamp = None

    # —— 金额 ——
    m = re.search(r"收款金额￥?\s*([\d]+(?:\.[\d]+)?)", flat)
    if not m:
        m = re.search(r"微信支付收款([\d]+(?:\.[\d]+)?)元", flat)
    if not m:
        # 卡片常见：收款金额 与 ￥1.00 分行，压扁后为 收款金额￥1.00
        m = re.search(r"收款到账通知.*?￥([\d]+(?:\.[\d]+)?)", flat)
    if not m:
        m = re.search(r"￥([\d]+(?:\.[\d]+)?)", flat)
    if m:
        amount = m.group(1)

    # —— 时间 ——
    # 新版卡片：收款到账通知 下方直接是 09月14日 14:27（无「到账时间」四字）
    m = re.search(
        r"收款到账通知"
        r"(\d{1,2}月\d{1,2}日\d{1,2}:\d{2}(?::\d{2})?)",
        flat,
    )
    if not m:
        m = re.search(
            r"到账时间[:：]?"
            r"([0-9]{4}[-/年]?[0-9]{1,2}[-/月][0-9]{1,2}日?\d{1,2}:\d{2}(?::\d{2})?"
            r"|"
            r"\d{1,2}月\d{1,2}日\d{1,2}:\d{2}(?::\d{2})?)",
            flat,
        )
    if not m:
        # 兜底：整段里找「X月X日 HH:MM」，优先靠近「收款」
        m = re.search(r"(\d{1,2}月\d{1,2}日\d{1,2}:\d{2}(?::\d{2})?)", flat)
    if m:
        timestamp = m.group(1)
        # 可读性：09月14日14:27 → 09月14日 14:27；2026-09-1412:00 → 带空格
        timestamp = re.sub(r"(日)(\d)", r"\1 \2", timestamp)
        timestamp = re.sub(r"(\d{4}-\d{1,2}-\d{1,2})(\d{1,2}:)", r"\1 \2", timestamp)

    # —— 来自（旧版才有；新版个人收款助手通常没有）——
    m = re.search(r"来自(.+?)到账时间", flat)
    if m:
        sender = m.group(1).strip()
    else:
        m = re.search(r"(?:来自|付款方|付款人)[:：]?([^\n到账备注汇总]{1,30})", cleaned)
        if m:
            sender = re.sub(r"\s+", "", m.group(1)).strip()

    # 过滤把导航文案当成「来自」
    if sender and any(x in sender for x in ("经营", "账本", "指南", "收款码", "查看")):
        sender = ""

    if not amount:
        return None

    # 必须像一笔到账通知：有「收款金额/到账通知」或能解析出卡片时间，避免误报
    if ("收款金额" not in flat) and ("收款到账通知" not in flat) and ("微信支付收款" not in flat):
        return None

    return {
        "amount": amount,
        "sender": sender or "",
        "timestamp": timestamp or "",
    }


def send_http_request(amount, sender, timestamp):
    try:
        data = {"amount": amount, "sender": sender, "timestamp": timestamp}
        if NOTIFY_TOKEN:
            data["token"] = NOTIFY_TOKEN
        requests.post(SERVER_URL, data=data, timeout=10)
    except Exception as e:
        print(f"通知服务器失败...: {e}")


def notify_if_new(info):
    global _last_notify_key
    key = f"{info['amount']}|{info['sender']}|{info['timestamp']}"
    if key == _last_notify_key:
        return False
    _last_notify_key = key
    print(
        f"收款金额: ￥{info['amount']}, "
        f"来自: {info['sender'] or '(无/个人收款)'}, "
        f"到账时间: {info['timestamp'] or '(未识别)'}"
    )
    print("-----------------------------------------------------------------")
    print("持续监听中...")
    print("-----------------------------------------------------------------")
    send_http_request(info["amount"], info["sender"], info["timestamp"])
    return True


def poll_once(debug=False):
    global _miss_count
    windows = list_candidate_windows()
    if not windows:
        print("无法获取到窗口：请双击打开「微信收款助手」或「微信支付」为独立窗口，并保持显示（可被遮挡，勿最小化）")
        return

    for win, title in windows:
        if not win.Exists(0):
            continue
        hwnd = win.NativeWindowHandle
        if hwnd and win32gui.IsIconic(hwnd):
            if debug or _miss_count % 10 == 0:
                print(f"窗口「{title}」已最小化，请手动还原（脚本不会抢焦点）")
            _miss_count += 1
            continue

        img = capture_window(win)
        if img is None:
            continue

        # 启动 debug 强制 OCR 一次；平时画面不变则跳过
        if not debug and not image_changed(img):
            return

        text = ocr_text(img)
        if debug:
            print(f"[debug] 窗口「{title}」OCR预览:\n{text[:800]}\n---")

        info = parse_payment(text)
        if info:
            _miss_count = 0
            notify_if_new(info)
            return

        _miss_count += 1
        if debug or _miss_count % 10 == 1:
            print(
                f"已找到窗口（{title}），但未识别到收款通知。"
                f"请滚到含「收款金额」的消息；窗口可被遮挡，但不要最小化。"
            )
        return


def main():
    _enable_dpi_awareness()
    print("-----------------------------------------------------------------")
    print("欢迎使用 liKeYun_WxPayPcNotify（新版微信 OCR 版）...")
    print("用法：")
    print("  1. 打开「微信收款助手」独立窗口（可被其它窗口遮挡，勿最小化）")
    print("  2. 窗口里能看到含「收款金额￥」的到账消息")
    print("  3. 本脚本不会抢鼠标/焦点，可正常用电脑")
    print("  4. SERVER_URL=http://127.0.0.1:17888/wxpay/notify")
    print("-----------------------------------------------------------------")
    print("正在加载 OCR 引擎...")
    get_ocr()
    print("OCR 就绪，开始监听...")
    print("-----------------------------------------------------------------")

    try:
        poll_once(debug=True)
    except Exception as e:
        print(f"发生错误: {e}")

    while True:
        try:
            poll_once(debug=False)
        except Exception as e:
            print(f"发生错误: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
