#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Авто-помощник для опроса ЕС ПТМ — БЕЗ ТАЙМЕРА, по кругу
- ВКЛ/ВЫКЛ, F7 выкл / F6 вкл
- САМ находит кнопки 1-10 и Ответить (авто-поиск)
- Цикл БЕЗ слежения за таймером:
  ждать wait_before (5 сек) -> рандом 4/5/6/7 -> клик -> клик Ответить
  -> ждать wait_after (6 сек) -> снова по кругу
- Мышь почти моментальная (0.08 сек)
"""
import json
import os
import sys
import time
import random
import threading
import queue

import tkinter as tk
from tkinter import ttk, messagebox

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "btn5": None,
    "buttons": None,
    "auto_buttons": None,
    "auto_answer": None,
    "auto_timer_rect": None,  # legacy, больше не используется
    "auto_mode": True,
    "mode": "random",
    "pool": {"4": True, "5": True, "6": True, "7": True},
    "avoid_repeat": True,
    "btn_answer": None,
    "timer_rect": None,  # legacy, таймер УБРАН
    "question_rect": None,
    "click5": True,
    "click_choice": True,
    "click_answer": True,
    "wait_before": 5.0,   # фиксированная пауза ДО ответа вместо таймера
    "wait_after": 6.0,    # пауза ПОСЛЕ Ответить перед следующим кругом
    "wait_jitter": 0.6,   # +/- рандом к паузам чтобы не палиться
    "check_interval": 0.12,
    "mouse_speed": 0.08,  # почти моментально
    "dark_thresh": 180,
    "dark_min_pixels": 20,
}


def migrate_config(cfg):
    """Совместимость со старым config.json."""
    if cfg.get("buttons") is None:
        cfg["buttons"] = {}
    if cfg.get("btn5") and "5" not in cfg["buttons"]:
        cfg["buttons"]["5"] = cfg["btn5"]
    if cfg.get("click_choice") is None:
        cfg["click_choice"] = bool(cfg.get("click5", True))
    if "click_answer" not in cfg:
        cfg["click_answer"] = True
    # фикс: у многих остался click_answer=False со старых версий — включаем клик по умолчанию
    if not cfg.get("_answer_fix3"):
        cfg["click_answer"] = True
        cfg["click_choice"] = True
        cfg["_answer_fix3"] = True
    if "auto_mode" not in cfg:
        cfg["auto_mode"] = True
    if "mouse_speed" not in cfg or cfg.get("mouse_speed", 0.45) > 0.3:
        # ускоряем старых пользователей только если они не трогали
        pass
    if not isinstance(cfg.get("pool"), dict):
        cfg["pool"] = {"4": True, "5": True, "6": True, "7": True}
    for k in ("4", "5", "6", "7"):
        cfg["pool"].setdefault(k, True)
    if cfg.get("mode") not in ("random", "only_5"):
        cfg["mode"] = "random"
    return cfg


def effective_buttons(cfg):
    """Авто-кнопки приоритетнее, потом ручные. Возвращает {int:[x,y]}."""
    auto = cfg.get("auto_buttons") or {}
    out = {}
    for k, v in auto.items():
        try:
            ik = int(k)
            if 4 <= ik <= 7 and isinstance(v, (list, tuple)) and len(v) == 2:
                out[ik] = [int(v[0]), int(v[1])]
        except Exception:
            continue
    if cfg.get("auto_mode", True) and len(out) >= 2:
        return out
    # ручные
    manual = get_buttons(cfg)
    for k, v in manual.items():
        if k not in out:
            out[k] = v
    # если авто выкл — только ручные
    if not cfg.get("auto_mode", True):
        return manual
    return out


def effective_answer(cfg):
    """[x,y] кнопки Ответить: авто приоритетнее."""
    if cfg.get("auto_mode", True) and cfg.get("auto_answer"):
        try:
            a = cfg["auto_answer"]
            return [int(a[0]), int(a[1])]
        except Exception:
            pass
    if cfg.get("btn_answer"):
        return [int(cfg["btn_answer"][0]), int(cfg["btn_answer"][1])]
    return None


def effective_timer_rect(cfg):
    if cfg.get("auto_mode", True) and cfg.get("auto_timer_rect"):
        return cfg["auto_timer_rect"]
    return cfg.get("timer_rect")


def get_buttons(cfg):
    """Возвращает dict {int: [x,y]} доступных калиброванных кнопок."""
    out = {}
    btns = cfg.get("buttons") or {}
    for k, v in btns.items():
        try:
            ik = int(k)
            if isinstance(v, (list, tuple)) and len(v) == 2:
                out[ik] = [int(v[0]), int(v[1])]
        except Exception:
            continue
    # legacy fallback
    if 5 not in out and cfg.get("btn5"):
        try:
            out[5] = [int(cfg["btn5"][0]), int(cfg["btn5"][1])]
        except Exception:
            pass
    return out


def choose_answer(cfg, last_choice=None):
    """
    Выбор ответа при окончании таймера.
    - mode == "only_5" -> всегда 5
    - mode == "random" -> случайное из pool ∩ найденные (авто+ручные).
    """
    btns = effective_buttons(cfg)
    if not btns:
        # fallback на старые ручные
        btns = get_buttons(cfg)
    if not btns:
        return None
    if cfg.get("mode") == "only_5":
        if 5 in btns:
            return 5
        return sorted(btns.keys())[0]
    pool = cfg.get("pool") or {}
    cands = [n for n in (4, 5, 6, 7) if pool.get(str(n), True) and n in btns]
    if not cands:
        cands = sorted(btns.keys())
    if len(cands) == 1:
        return cands[0]
    if cfg.get("avoid_repeat") and last_choice in cands and len(cands) > 1:
        cands = [c for c in cands if c != last_choice]
    return random.choice(cands)

# ---------- lazy imports (чтобы GUI открывалось даже без зависимостей) ----------
pyautogui = None
Image = None
np = None
cv2 = None
keyboard_lib = None
IMPORT_ERRORS = []

try:
    import pyautogui as _pg
    pyautogui = _pg
    pyautogui.FAILSAFE = True  # угол экрана = аварийный стоп
except Exception as e:
    IMPORT_ERRORS.append(f"pyautogui: {e}")

try:
    from PIL import Image as _Img
    Image = _Img
except Exception as e:
    IMPORT_ERRORS.append(f"Pillow: {e}")

try:
    import numpy as _np
    np = _np
except Exception as e:
    IMPORT_ERRORS.append(f"numpy: {e}")

try:
    import cv2 as _cv2
    cv2 = _cv2
except Exception as e:
    IMPORT_ERRORS.append(f"opencv: {e} (авто-поиск кнопок не будет работать, поставь opencv-python)")

try:
    import keyboard as _kb
    keyboard_lib = _kb
except Exception:
    keyboard_lib = None  # не критично, будет работать F7 только когда окно в фокусе


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    # pool это dict — копируем чтобы не мутировать дефолт
    cfg["pool"] = dict(DEFAULT_CONFIG["pool"])
    cfg["buttons"] = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.update(data)
        except Exception:
            pass
    return migrate_config(cfg)


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("save config error:", e)


# ---------- анализ картинок без tesseract ----------
def shot_region(rect):
    """rect = [x,y,w,h] -> PIL image. Только через pyautogui чтобы координаты совпадали с mouse."""
    if pyautogui is None or Image is None:
        return None
    x, y, w, h = map(int, rect)
    if w < 5 or h < 5:
        return None
    try:
        return pyautogui.screenshot(region=(x, y, w, h))
    except Exception:
        return None


def timer_active(img, dark_thresh=180, min_pixels=25):
    """
    Таймер типа '3.8' = темные цифры на белом фоне.
    Считаем темные пиксели. Пустая белая область -> таймера нет.
    Возвращает (active: bool, dark_count: int)
    """
    if img is None or np is None:
        return False, 0
    try:
        gray = np.array(img.convert("L"))
        dark = int((gray < dark_thresh).sum())
        return (dark >= min_pixels), dark
    except Exception:
        return False, 0


def question_hash(img):
    """Простой хэш области вопроса для детекта смены вопроса."""
    if img is None or np is None:
        return None
    try:
        small = img.convert("L").resize((64, 16))
        arr = np.array(small, dtype=float)
        return float(arr.mean()), float(arr.std()), int(arr.sum() // 1000)
    except Exception:
        return None


def question_changed(h1, h2, thresh=6.0):
    if h1 is None or h2 is None:
        return True
    dm = abs(h1[0] - h2[0])
    ds = abs(h1[1] - h2[1])
    return (dm + ds) > thresh or h1[2] != h2[2]


def answer_button_ready(cfg):
    """
    Кнопка 'Ответить' зеленая когда можно отвечать.
    Использует эффективные координаты (авто или ручные).
    """
    if pyautogui is None:
        return None
    pt = effective_answer(cfg)
    if pt is None:
        pt = cfg.get("btn_answer")
    if pt is None:
        return None
    try:
        pass
    except Exception:
        pass
    try:
        r, g, b = pyautogui.pixel(int(pt[0]), int(pt[1]))
        is_green = (g > 150 and g > r + 30 and b > 100 and r < 150)
        return bool(is_green)
    except Exception:
        return None


def human_move_to(x, y, duration=0.12):
    """Быстрая доводка с легкой человечностью. <=0.05 = почти мгновенно."""
    if pyautogui is None:
        return False
    try:
        sx, sy = pyautogui.position()
        ex, ey = int(x), int(y)
        if abs(sx - ex) < 2 and abs(sy - ey) < 2:
            return True
        if duration <= 0.05:
            pyautogui.moveTo(ex, ey, duration=0)
            return True
        # короткий изгиб чтобы не палиться прямой линией
        mx = (sx + ex) / 2 + random.uniform(-25, 25)
        my = (sy + ey) / 2 + random.uniform(-15, 15)
        steps = max(4, int(duration * 60))
        for i in range(1, steps + 1):
            t = i / steps
            te = t * t * (3 - 2 * t)
            cx = (1 - te) ** 2 * sx + 2 * (1 - te) * te * mx + te ** 2 * ex
            cy = (1 - te) ** 2 * sy + 2 * (1 - te) * te * my + te ** 2 * ey
            pyautogui.moveTo(cx, cy, duration=0)
            time.sleep(duration / steps * random.uniform(0.8, 1.2))
        pyautogui.moveTo(ex, ey, duration=0)
        return True
    except Exception as e:
        print("move error:", e)
        return False


# ---------- АВТО-ПОИСК: сам смотрим экран ----------
def _grab_full():
    if pyautogui is None or np is None:
        return None, None
    try:
        pil = pyautogui.screenshot()
        arr = np.array(pil)  # RGB
        return pil, arr
    except Exception:
        return None, None


def detect_scale_buttons(pil_img=None, arr_rgb=None):
    """
    САМ ищем ряд кружков 1-10.
    Возвращает (dict {1..10: [x,y]}, info) или (None, reason).
    Логика без OCR: ищем ~10 кругов на одной линии, сортируем слева-направо = 1..10.
    """
    if cv2 is None or np is None:
        return None, "нет opencv (pip install opencv-python)"
    try:
        if arr_rgb is None:
            if pil_img is None:
                pil_img, arr_rgb = _grab_full()
            else:
                arr_rgb = np.array(pil_img)
        if arr_rgb is None:
            return None, "не смог сделать скрин"
        h, w = arr_rgb.shape[:2]
        gray = cv2.cvtColor(arr_rgb, cv2.COLOR_RGB2GRAY)
        gray = cv2.medianBlur(gray, 5)
        # радиус зависит от высоты экрана: кружки ~2-3% высоты
        r_min = max(12, int(h * 0.015))
        r_max = max(25, int(h * 0.035))
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=int(w * 0.04),
            param1=120, param2=22, minRadius=r_min, maxRadius=r_max,
        )
        if circles is None:
            # вторая попытка чувствительнее
            circles = cv2.HoughCircles(
                gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=int(w * 0.03),
                param1=100, param2=18, minRadius=max(10, r_min - 4), maxRadius=r_max + 8,
            )
        if circles is None:
            return None, "круги не найдены (открой тест со шкалой 1-10 на весь экран)"
        cs = [(int(x), int(y), int(r)) for x, y, r in circles[0, :]]
        # ищем линию: группируем по Y (допуск = средний радиус)
        cs_sorted_y = sorted(cs, key=lambda c: c[1])
        best_row = []
        for _, cy, _ in cs_sorted_y:
            tol = r_max * 1.2
            row = [c for c in cs if abs(c[1] - cy) < tol]
            # только средняя часть экрана по вертикали (шкала обычно 40-70%)
            if not (h * 0.25 < cy < h * 0.85):
                continue
            if len(row) > len(best_row):
                best_row = row
        if len(best_row) < 8:
            return None, f"нашел только {len(best_row)} кругов в ряд (нужно 8-10)"
        best_row = sorted(best_row, key=lambda c: c[0])
        # чистим по шагу: медианный интервал, выкидываем дубли
        xs = [c[0] for c in best_row]
        gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        gaps = [g for g in gaps if g > 5]
        if not gaps:
            return None, "странный ряд"
        med = sorted(gaps)[len(gaps) // 2]
        filt = [best_row[0]]
        for c in best_row[1:]:
            if c[0] - filt[-1][0] > med * 0.5:
                filt.append(c)
        best_row = filt
        if len(best_row) < 8 or len(best_row) > 12:
            return None, f"ряд {len(best_row)} кругов, ожидалось 10"
        # если 10 — прямое соответствие, если 8-9/11 — берем центральную часть
        # сортируем и нумеруем: предполагаем что ряд = 1..N слева направо
        n = len(best_row)
        mapping = {}
        if n == 10:
            for i, (x, y, r) in enumerate(best_row, start=1):
                mapping[i] = [x, y]
        elif n > 10:
            # берем 10 с самым ровным шагом по центру
            best_row = best_row[len(best_row) - 10:]
            for i, (x, y, r) in enumerate(best_row, start=1):
                mapping[i] = [x, y]
        else:
            # 8-9: пытаемся понять сдвиг по краям экрана.
            # Эвристика: ряд занимает большую ширину (>50% экрана) => это 1-10 с пропусками.
            # Проще: интерполируем — считаем что крайние = 1 и 10 если ширина большая.
            span = best_row[-1][0] - best_row[0][0]
            if span > w * 0.5 and n >= 8:
                # растягиваем на 10 позиций линейной интерполяцией
                x0 = best_row[0][0]
                x1 = best_row[-1][0]
                y0 = int(sum(c[1] for c in best_row) / len(best_row))
                for i in range(1, 11):
                    xx = int(x0 + (x1 - x0) * (i - 1) / 9)
                    # ищем ближайший найденный круг, иначе интерполяция
                    near = min(best_row, key=lambda c: abs(c[0] - xx))
                    if abs(near[0] - xx) < med * 0.4:
                        mapping[i] = [near[0], near[1]]
                    else:
                        mapping[i] = [xx, y0]
            else:
                return None, f"нашел {n} кругов, нужно 10 — разверни окно/масштаб 100%"
        # область таймера: под центром шкалы (где "3.8")
        xs10 = [mapping[i][0] for i in range(1, 11)]
        ys10 = [mapping[i][1] for i in range(1, 11)]
        y_scale = int(sum(ys10) / len(ys10))
        r_est = int(sum(c[2] for c in best_row) / len(best_row)) if best_row else 20
        x_mid = (mapping[5][0] + mapping[6][0]) // 2
        timer_rect = [int(x_mid - 70), int(y_scale + r_est + 8), 140, 34]
        return {"buttons": mapping, "y_scale": y_scale, "r": r_est, "timer_rect": timer_rect}, "ok"
    except Exception as e:
        return None, f"ошибка детекта шкалы: {e}"


def detect_answer_button(pil_img=None, arr_rgb=None):
    """
    Ищем зеленую/бирюзовую кнопку Ответить внизу экрана.
    Возвращает ([x,y,w,h], True, info) или (None, False, reason).
    Фильтры расширены под широкий скрин юзера (кнопка ~600x45, aspect ~13-20).
    """
    if cv2 is None or np is None:
        return None, False, "нет opencv"
    try:
        if arr_rgb is None:
            if pil_img is None:
                pil_img, arr_rgb = _grab_full()
            else:
                arr_rgb = np.array(pil_img)
        if arr_rgb is None:
            return None, False, "нет скрина"
        h, w = arr_rgb.shape[:2]
        R = arr_rgb[:, :, 0].astype(int)
        G = arr_rgb[:, :, 1].astype(int)
        B = arr_rgb[:, :, 2].astype(int)
        # проход 1 строгий, проход 2 мягкий (разные оттенки teal)
        for attempt, (g_min, dr, b_min, r_max) in enumerate([
            (150, 30, 90, 150),
            (125, 18, 65, 175),
        ]):
            green_mask = ((G > g_min) & (G > R + dr) & (B > b_min) & (B < 235) & (R < r_max)).astype("uint8") * 255
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 7))
            green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, kernel)
            # убрать мелкий шум
            n, lab, stats, _ = cv2.connectedComponentsWithStats(green_mask, 8)
            clean = np.zeros_like(green_mask)
            for i in range(1, n):
                area = stats[i, cv2.CC_STAT_AREA]
                if area >= 2500:
                    clean[lab == i] = 255
            cnts, _ = cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cands = []
            for c in cnts:
                x, y, ww, hh = cv2.boundingRect(c)
                area = ww * hh
                if y < h * 0.35:
                    continue  # кнопка в нижней половине
                if ww < 80 or hh < 18 or hh > 150:
                    continue
                aspect = ww / max(1, hh)
                if not (1.8 < aspect < 25):
                    continue
                if area < 3000:
                    continue
                cands.append((area, x, y, ww, hh))
            if cands:
                # самая широкая/большая зеленая внизу = Ответить
                cands.sort(reverse=True)
                area, x, y, ww, hh = cands[0]
                cx, cy = int(x + ww // 2), int(y + hh // 2)
                return [cx, cy, int(ww), int(hh)], True, f"зеленая {ww}x{hh} at ({cx},{cy}) проход{attempt+1}"
        return None, False, "зеленую Ответить не вижу: открой тест, разверни окно, масштаб 100%"
    except Exception as e:
        return None, False, f"ошибка: {e}"


def auto_find_all(save_cfg=None):
    """Один проход: скрин -> шкала + Ответить + таймер. Возвращает dict для лога."""
    pil, arr = _grab_full()
    if pil is None:
        return {"ok": False, "msg": "не смог сделать скриншот"}
    scale, msg1 = detect_scale_buttons(pil, arr)
    ans, ready, msg2 = detect_answer_button(pil, arr)
    res = {"ok": False, "scale_msg": msg1, "answer_msg": msg2, "ready": ready}
    if scale is None:
        res["msg"] = f"Шкала: {msg1} | Ответить: {msg2}"
        return res
    mapping = scale["buttons"]
    timer_rect = scale["timer_rect"]
    res.update({"ok": True, "buttons": {k: v for k, v in mapping.items() if 4 <= k <= 7},
                "all10": mapping, "timer_rect": timer_rect, "answer": ans,
                "msg": f"Шкала ок ({len(mapping)}), таймер {timer_rect}, Ответить: {msg2}"})
    if isinstance(save_cfg, dict):
        # сохраняем авто-находки
        save_cfg["auto_buttons"] = {str(k): v for k, v in mapping.items()}
        if ans:
            save_cfg["auto_answer"] = [int(ans[0]), int(ans[1]), int(ans[2]), int(ans[3])]
        save_cfg["auto_timer_rect"] = [int(x) for x in timer_rect]
        try:
            save_config(save_cfg)
        except Exception:
            pass
    return res


class RegionSelector:
    """Полноэкранный выбор прямоугольника мышью."""
    def __init__(self, root):
        self.root = root
        self.result = None

    def select(self):
        win = tk.Toplevel(self.root)
        win.attributes("-fullscreen", True)
        win.attributes("-alpha", 0.35)
        win.attributes("-topmost", True)
        win.configure(bg="gray")
        win.grab_set()

        label = tk.Label(win, text="Выдели ЛКМ область, Esc — отмена",
                         font=("Segoe UI", 16), bg="black", fg="white")
        label.pack(pady=20)

        c = tk.Canvas(win, bg="gray", highlightthickness=0)
        c.pack(fill="both", expand=True)

        start = {}
        rect_id = None

        def on_press(e):
            nonlocal rect_id
            start["x"], start["y"] = e.x_root, e.y_root
            # координаты канваса
            start["cx"], start["cy"] = e.x, e.y
            if rect_id:
                c.delete(rect_id)

        def on_drag(e):
            nonlocal rect_id
            if rect_id:
                c.delete(rect_id)
            rect_id = c.create_rectangle(start.get("cx", e.x), start.get("cy", e.y),
                                         e.x, e.y, outline="red", width=3)

        def on_release(e):
            x1, y1 = start["x"], start["y"]
            x2, y2 = e.x_root, e.y_root
            x, y = min(x1, x2), min(y1, y2)
            w, h = abs(x2 - x1), abs(y2 - y1)
            if w > 10 and h > 10:
                self.result = [int(x), int(y), int(w), int(h)]
            win.destroy()

        def on_esc(e):
            self.result = None
            win.destroy()

        c.bind("<ButtonPress-1>", on_press)
        c.bind("<B1-Motion>", on_drag)
        c.bind("<ButtonRelease-1>", on_release)
        win.bind("<Escape>", on_esc)
        self.root.wait_window(win)
        return self.result


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.enabled = False
        self.worker = None
        self.stop_event = threading.Event()
        self.log_q = queue.Queue()
        self.last_question_hash = None
        self.state = "выкл"

        root.title("Авто-помощник опроса — АВТО (F7 выкл) | Рандом 4-5-6-7")
        root.geometry("620x900")
        root.resizable(False, False)

        # --- верх: большая кнопка ВКЛ/ВЫКЛ ---
        self.big_btn = tk.Button(root, text="▶ ВКЛЮЧИТЬ (АВТО)", font=("Segoe UI", 20, "bold"),
                                 bg="#2ecc71", fg="white", height=2, command=self.toggle)
        self.big_btn.pack(fill="x", padx=12, pady=8)

        self.status_var = tk.StringVar(value="Статус: ВЫКЛЮЧЕНО (F7 — выключить, F6 — включить)")
        ttk.Label(root, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(pady=2)

        # --- АВТО ---
        auto = ttk.LabelFrame(root, text="АВТО-поиск (ничего калибровать не надо)")
        auto.pack(fill="x", padx=12, pady=6)
        self.v_auto = tk.BooleanVar(value=bool(self.cfg.get("auto_mode", True)))
        ttk.Checkbutton(auto, text="АВТО-режим: сам искать кнопки/таймер/Ответить",
                        variable=self.v_auto, command=self.save_ui_to_cfg).grid(row=0, column=0, columnspan=2, sticky="w", padx=6)
        ttk.Button(auto, text="🔍 НАЙТИ АВТОМАТИЧЕСКИ (открыть тест!)", command=self.auto_find_btn).grid(row=1, column=0, sticky="ew", padx=6, pady=2)
        self.lblAuto = tk.StringVar(value="Авто: еще не искали")
        ttk.Label(auto, textvariable=self.lblAuto, font=("Segoe UI", 8)).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(auto, text="Авто сам находит ряд 1-10 и зеленую Ответить. Таймера нет — работа по паузам.",
                  font=("Segoe UI", 8)).grid(row=2, column=0, columnspan=2, sticky="w", padx=6)
        rowA = tk.Frame(auto)
        rowA.grid(row=3, column=0, columnspan=2, sticky="ew", padx=6, pady=2)
        ttk.Button(rowA, text="Проверить поиск Ответить", command=self.test_find_answer).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(rowA, text="Тест клика Ответить", command=self.test_click_answer).pack(side="left", expand=True, fill="x", padx=2)

        # --- калибровка (запасной вариант) ---
        cal = ttk.LabelFrame(root, text="Ручная калибровка (запасной вариант)")
        cal.pack(fill="x", padx=12, pady=4)

        self.lbl4567 = tk.StringVar()
        self.lblAns = tk.StringVar()
        self.lblTimer = tk.StringVar()
        self.lblQ = tk.StringVar()

        ttk.Button(cal, text="1. Захватить 4-5-6-7 (по 3 сек)", command=self.cal_all4567).grid(row=0, column=0, sticky="ew", padx=4, pady=2)
        ttk.Label(cal, textvariable=self.lbl4567, font=("Segoe UI", 8)).grid(row=0, column=1, sticky="w", padx=4)
        row1 = tk.Frame(cal)
        row1.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4)
        ttk.Button(row1, text="4", width=4, command=lambda: self.cal_single("4")).pack(side="left", padx=2)
        ttk.Button(row1, text="5", width=4, command=lambda: self.cal_single("5")).pack(side="left", padx=2)
        ttk.Button(row1, text="6", width=4, command=lambda: self.cal_single("6")).pack(side="left", padx=2)
        ttk.Button(row1, text="7", width=4, command=lambda: self.cal_single("7")).pack(side="left", padx=2)
        ttk.Label(row1, text="— докалибровать одну кнопку (3 сек)", font=("Segoe UI", 8)).pack(side="left", padx=6)
        ttk.Button(cal, text="2. Захватить кнопку Ответить (3 сек)", command=self.cal_answer).grid(row=2, column=0, sticky="ew", padx=4, pady=2)
        ttk.Label(cal, textvariable=self.lblAns).grid(row=2, column=1, sticky="w", padx=4)
        cal.columnconfigure(0, weight=0)
        cal.columnconfigure(1, weight=1)

        # --- рандомайзер ---
        rnd = ttk.LabelFrame(root, text="Рандомайзер ответа")
        rnd.pack(fill="x", padx=12, pady=6)

        self.v_mode = tk.StringVar(value=self.cfg.get("mode", "random"))
        ttk.Radiobutton(rnd, text="Рандом 4-5-6-7 (рекомендую)", value="random",
                        variable=self.v_mode, command=self.save_ui_to_cfg).grid(row=0, column=0, sticky="w", padx=6)
        ttk.Radiobutton(rnd, text="Только 5", value="only_5",
                        variable=self.v_mode, command=self.save_ui_to_cfg).grid(row=0, column=1, sticky="w", padx=6)

        self.v_pool4 = tk.BooleanVar(value=bool(self.cfg.get("pool", {}).get("4", True)))
        self.v_pool5 = tk.BooleanVar(value=bool(self.cfg.get("pool", {}).get("5", True)))
        self.v_pool6 = tk.BooleanVar(value=bool(self.cfg.get("pool", {}).get("6", True)))
        self.v_pool7 = tk.BooleanVar(value=bool(self.cfg.get("pool", {}).get("7", True)))
        ttk.Checkbutton(rnd, text="4", variable=self.v_pool4, command=self.save_ui_to_cfg).grid(row=1, column=0, sticky="w", padx=20)
        ttk.Checkbutton(rnd, text="5", variable=self.v_pool5, command=self.save_ui_to_cfg).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Checkbutton(rnd, text="6", variable=self.v_pool6, command=self.save_ui_to_cfg).grid(row=1, column=2, sticky="w", padx=6)
        ttk.Checkbutton(rnd, text="7", variable=self.v_pool7, command=self.save_ui_to_cfg).grid(row=1, column=3, sticky="w", padx=6)

        self.v_avoid = tk.BooleanVar(value=bool(self.cfg.get("avoid_repeat", True)))
        ttk.Checkbutton(rnd, text="Не повторять то же число подряд", variable=self.v_avoid,
                        command=self.save_ui_to_cfg).grid(row=2, column=0, columnspan=4, sticky="w", padx=6)

        self.lblLast = tk.StringVar(value="Последний выбор: —")
        self.lblStats = tk.StringVar(value="Статистика: 4:0  5:0  6:0  7:0")
        ttk.Label(rnd, textvariable=self.lblLast, font=("Segoe UI", 10, "bold")).grid(row=3, column=0, columnspan=2, sticky="w", padx=6)
        ttk.Label(rnd, textvariable=self.lblStats, font=("Segoe UI", 9)).grid(row=3, column=2, columnspan=2, sticky="w", padx=6)
        ttk.Button(rnd, text="Тест рандома (10 выборов)", command=self.test_random).grid(row=4, column=0, columnspan=2, sticky="ew", padx=6, pady=2)
        ttk.Button(rnd, text="Сбросить статистику", command=self.reset_stats).grid(row=4, column=2, columnspan=2, sticky="ew", padx=6, pady=2)

        # --- настройки: БЕЗ ТАЙМЕРА ---
        st = ttk.LabelFrame(root, text="Настройки (без таймера: паузы)")
        st.pack(fill="x", padx=12, pady=6)

        self.v_click5 = tk.BooleanVar(value=bool(self.cfg.get("click_choice", True)))
        self.v_clickAns = tk.BooleanVar(value=bool(self.cfg.get("click_answer", True)))
        ttk.Checkbutton(st, text="Кликать по цифре (иначе только навести)",
                        variable=self.v_click5, command=self.save_ui_to_cfg).grid(row=0, column=0, sticky="w", padx=6)

        ttk.Checkbutton(st, text="Нажимать «Ответить» после цифры", variable=self.v_clickAns,
                        command=self.save_ui_to_cfg).grid(row=1, column=0, sticky="w", padx=6)

        ttk.Label(st, text="Пауза ДО ответа вместо таймера (сек):").grid(row=2, column=0, sticky="w", padx=6)
        self.v_before = tk.DoubleVar(value=float(self.cfg.get("wait_before", 5.0)))
        ttk.Scale(st, from_=1.0, to=10.0, variable=self.v_before,
                  command=lambda *_: self.save_ui_to_cfg()).grid(row=2, column=1, sticky="ew", padx=6)

        ttk.Label(st, text="Пауза ПОСЛЕ Ответить (сек):").grid(row=3, column=0, sticky="w", padx=6)
        self.v_after = tk.DoubleVar(value=float(self.cfg.get("wait_after", 6.0)))
        ttk.Scale(st, from_=1.0, to=12.0, variable=self.v_after,
                  command=lambda *_: self.save_ui_to_cfg()).grid(row=3, column=1, sticky="ew", padx=6)

        ttk.Label(st, text="Скорость мыши (0=моментально):").grid(row=4, column=0, sticky="w", padx=6)
        self.v_speed = tk.DoubleVar(value=float(self.cfg.get("mouse_speed", 0.08)))
        ttk.Scale(st, from_=0.0, to=0.8, variable=self.v_speed,
                  command=lambda *_: self.save_ui_to_cfg()).grid(row=4, column=1, sticky="ew", padx=6)
        st.columnconfigure(1, weight=1)

        self.last_choice = None
        self.stats = {4: 0, 5: 0, 6: 0, 7: 0}
        self.refresh_labels()

        # --- лог ---
        ttk.Label(root, text="Лог:").pack(anchor="w", padx=12)
        self.log = tk.Text(root, height=10, font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=12, pady=4)
        self.log.configure(state="disabled")

        ttk.Label(root, text="Горячие клавиши: F7 — ВЫКЛ (аварийно), F6 — ВКЛ. Угол экрана — стоп мыши.",
                  font=("Segoe UI", 8)).pack(pady=2)

        # бинды когда окно в фокусе (запасной вариант)
        root.bind("<F7>", lambda *_: self.disable())
        root.bind("<F6>", lambda *_: self.enable())

        # глобальные хоткеи
        if keyboard_lib is not None:
            try:
                keyboard_lib.add_hotkey("F7", self.disable)
                keyboard_lib.add_hotkey("F6", self.enable)
                self.log_msg("Глобальные хоткеи F6/F7 активны.")
            except Exception as e:
                self.log_msg(f"keyboard lib не смог перехватить F7: {e}")
        else:
            self.log_msg("Пакет 'keyboard' не найден: F7 будет работать только когда окно активно. Установи: pip install keyboard")

        if IMPORT_ERRORS:
            self.log_msg("ВНИМАНИЕ нет зависимостей: " + "; ".join(IMPORT_ERRORS))
            self.log_msg("Выполни: pip install -r requirements.txt")

        self.root.after(100, self.poll_log)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI helpers ----------
    def log_msg(self, s):
        self.log_q.put(f"[{time.strftime('%H:%M:%S')}] {s}")

    def poll_log(self):
        try:
            while True:
                s = self.log_q.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", s + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.poll_log)

    def refresh_labels(self):
        btns = get_buttons(self.cfg)
        txt = " | ".join([f"{k}:{v}" for k, v in sorted(btns.items())]) or "не заданы"
        auto_b = self.cfg.get("auto_buttons") or {}
        try:
            auto_txt = ", ".join([f"{k}:{v}" for k, v in sorted(auto_b.items(), key=lambda x: int(x[0])) if int(x[0]) in (4, 5, 6, 7)]) or "не найдены"
        except Exception:
            auto_txt = "не найдены"
        try:
            self.lbl4567.set(f"Ручные 4-5-6-7: {txt}")
            self.lblAns.set(f"Ручная Ответить: {self.cfg.get('btn_answer')} | Авто: {self.cfg.get('auto_answer')}")
            try:
                self.lblTimer.set("")
                self.lblQ.set("")
            except Exception:
                pass
            self.lblAuto.set(f"Авто: 4-5-6-7 {auto_txt}")
        except Exception:
            pass

    def save_ui_to_cfg(self):
        click = bool(self.v_click5.get())
        self.cfg["click_choice"] = click
        self.cfg["click5"] = click
        self.cfg["click_answer"] = bool(self.v_clickAns.get())
        try:
            self.cfg["auto_mode"] = bool(self.v_auto.get())
        except Exception:
            pass
        try:
            self.cfg["mode"] = str(self.v_mode.get())
        except Exception:
            pass
        try:
            self.cfg["pool"] = {
                "4": bool(self.v_pool4.get()),
                "5": bool(self.v_pool5.get()),
                "6": bool(self.v_pool6.get()),
                "7": bool(self.v_pool7.get()),
            }
            self.cfg["avoid_repeat"] = bool(self.v_avoid.get())
        except Exception:
            pass
        try:
            self.cfg["wait_before"] = float(self.v_before.get())
            self.cfg["wait_after"] = float(self.v_after.get())
            self.cfg["mouse_speed"] = float(self.v_speed.get())
        except Exception:
            pass
        save_config(self.cfg)

    def auto_find_btn(self):
        threading.Thread(target=self._auto_find, daemon=True).start()

    def _auto_find(self):
        try:
            self.status_var.set("Статус: ищу шкалу и кнопки...")
        except Exception:
            pass
        self.log_msg("Авто-поиск: открой тест со шкалой на весь экран, ищу...")
        if cv2 is None:
            self.log_msg("Нет opencv! Выполни: pip install opencv-python")
            try:
                messagebox.showerror("Нет opencv", "pip install opencv-python")
            except Exception:
                pass
            return
        res = auto_find_all(self.cfg)
        self.refresh_labels()
        if res.get("ok"):
            msg = res.get("msg", "")
            self.log_msg(f"АВТО НАШЕЛ: {msg}")
            self.log_msg(f"Кнопки 4-7: {res.get('buttons')}")
            try:
                self.status_var.set("Статус: авто нашел! Можно включать.")
                self.lblAuto.set(f"Авто ОК: {res.get('buttons')} | {res.get('answer_msg')}")
            except Exception:
                pass
        else:
            self.log_msg(f"АВТО НЕ НАШЕЛ: {res.get('msg')}")
            self.log_msg("Подсказка: разверни браузер на весь экран, масштаб 100%, шкала 1-10 должна быть видна.")
            try:
                self.status_var.set("Статус: авто не нашел — открой тест и повтори.")
            except Exception:
                pass

    def test_find_answer(self):
        threading.Thread(target=self._test_find_answer, daemon=True).start()

    def _test_find_answer(self):
        self.save_ui_to_cfg()
        if cv2 is None:
            self.log_msg("Нет opencv: pip install opencv-python")
            return
        self.log_msg("Проверка поиска Ответить: скрин 1 сек... наведи тест!")
        time.sleep(1.0)
        try:
            pil, arr = _grab_full()
            ans, ready, msg = detect_answer_button(pil, arr)
            self.log_msg(f"Поиск Ответить: ans={ans} ready={ready} ({msg})")
            self.log_msg(f"Кэш auto_answer={self.cfg.get('auto_answer')} ручная={self.cfg.get('btn_answer')} галка Ответить={self.cfg.get('click_answer')}")
            if ans:
                self.cfg["auto_answer"] = [int(ans[0]), int(ans[1]), int(ans[2]), int(ans[3])]
                save_config(self.cfg)
                self.refresh_labels()
                try:
                    self.status_var.set(f"Нашел Ответить ({ans[0]},{ans[1]}) — теперь тест клика.")
                except Exception:
                    pass
            else:
                try:
                    self.status_var.set("Не вижу Ответить — разверни тест!")
                except Exception:
                    pass
        except Exception as e:
            self.log_msg(f"Ошибка проверки: {e}")

    def test_click_answer(self):
        threading.Thread(target=self._test_click_answer, daemon=True).start()

    def _test_click_answer(self):
        self.save_ui_to_cfg()
        pt = effective_answer(self.cfg)
        if pt is None:
            # live поиск
            try:
                pil, arr = _grab_full()
                ans, _, msg = detect_answer_button(pil, arr)
                self.log_msg(f"Live перед тест-кликом: {ans} ({msg})")
                if ans:
                    pt = [ans[0], ans[1]]
            except Exception as e:
                self.log_msg(f"Live error: {e}")
        if pt is None:
            self.log_msg("НЕКУДА КЛИКАТЬ: Ответить не найдена. Сначала 'Проверить поиск' или захвати вручную.")
            return
        self.log_msg(f"Тест-клик Ответить ({pt[0]},{pt[1]}) через 1 сек — НЕ ТРОГАЙ МЫШЬ!")
        time.sleep(1.0)
        try:
            mspeed = float(self.cfg.get("mouse_speed", 0.08))
            human_move_to(int(pt[0]), int(pt[1]), duration=mspeed)
            time.sleep(0.1)
            pyautogui.moveTo(int(pt[0]), int(pt[1]), duration=0)
            pyautogui.click(int(pt[0]), int(pt[1]))
            mx, my = pyautogui.position()
            self.log_msg(f"Тест-клик выполнен, мышь ({mx},{my}). Кнопка нажалась? Смотри браузер.")
        except Exception as e:
            self.log_msg(f"Тест-клик ошибка: {e}")

    def test_random(self):
        self.save_ui_to_cfg()
        seq = [choose_answer(self.cfg, None) for _ in range(10)]
        # цепочка с avoid_repeat для наглядности
        seq2 = []
        last = None
        for _ in range(10):
            c = choose_answer(self.cfg, last)
            seq2.append(c)
            last = c
        self.log_msg(f"Тест рандома (без истории): {seq}")
        self.log_msg(f"Тест рандома (с anti-repeat): {seq2}")

    def reset_stats(self):
        self.stats = {4: 0, 5: 0, 6: 0, 7: 0}
        self.last_choice = None
        try:
            self.lblLast.set("Последний выбор: —")
            self.lblStats.set("Статистика: 4:0  5:0  6:0  7:0")
        except Exception:
            pass
        self.log_msg("Статистика сброшена.")

    # ---------- калибровка ----------
    def countdown_capture_point(self, name):
        self.log_msg(f"{name}: за 3 сек наведи мышь на нужную точку и НЕ ДВИГАЙ...")
        for i in [3, 2, 1]:
            self.status_var.set(f"Наведи мышь... {i}")
            self.root.update()
            time.sleep(1)
        if pyautogui is None:
            messagebox.showerror("Нет pyautogui", "Установи зависимости: pip install -r requirements.txt")
            return None
        x, y = pyautogui.position()
        self.log_msg(f"{name}: захвачено ({x}, {y})")
        return [int(x), int(y)]

    def cal_single(self, num):
        threading.Thread(target=self._cal_single, args=(num,), daemon=True).start()

    def _cal_single(self, num):
        p = self.countdown_capture_point(f"Кнопка {num}")
        if p:
            if self.cfg.get("buttons") is None:
                self.cfg["buttons"] = {}
            self.cfg["buttons"][str(num)] = p
            if str(num) == "5":
                self.cfg["btn5"] = p
            save_config(self.cfg)
            self.refresh_labels()
            try:
                self.status_var.set(f"Кнопка {num} сохранена {p}.")
            except Exception:
                pass

    def cal_all4567(self):
        threading.Thread(target=self._cal_all4567, daemon=True).start()

    def _cal_all4567(self):
        if self.cfg.get("buttons") is None:
            self.cfg["buttons"] = {}
        for num in ("4", "5", "6", "7"):
            p = self.countdown_capture_point(f"Кнопка {num} — наведи и замри")
            if p is None:
                self.log_msg(f"Захват {num} отменен (нет pyautogui).")
                return
            self.cfg["buttons"][str(num)] = p
            if str(num) == "5":
                self.cfg["btn5"] = p
            save_config(self.cfg)
            self.refresh_labels()
        try:
            self.status_var.set("Все 4-5-6-7 сохранены.")
        except Exception:
            pass
        self.log_msg(f"Готово 4-5-6-7: {self.cfg['buttons']}")

    # для совместимости со старым вызовом
    def cal_btn5(self):
        self.cal_single("5")

    def _cal_btn5(self):
        self._cal_single("5")

    def cal_answer(self):
        threading.Thread(target=self._cal_answer, daemon=True).start()

    def _cal_answer(self):
        p = self.countdown_capture_point("Кнопка Ответить")
        if p:
            self.cfg["btn_answer"] = p
            save_config(self.cfg)
            self.refresh_labels()
            self.status_var.set("Кнопка Ответить сохранена.")

    def cal_timer(self):
        r = RegionSelector(self.root).select()
        if r:
            self.cfg["timer_rect"] = r
            save_config(self.cfg)
            self.refresh_labels()
            self.log_msg(f"Область таймера: {r}")

    def cal_question(self):
        r = RegionSelector(self.root).select()
        if r:
            self.cfg["question_rect"] = r
            save_config(self.cfg)
            self.refresh_labels()
            self.log_msg(f"Область вопроса: {r}")

    # ---------- вкл/выкл ----------
    def toggle(self):
        if self.enabled:
            self.disable()
        else:
            self.enable()

    def enable(self):
        if self.enabled:
            return
        if pyautogui is None or Image is None or np is None:
            messagebox.showerror("Нет зависимостей",
                                 "Установи:\npip install -r requirements.txt\nнужны pyautogui pillow numpy + opencv-python для авто")
            return
        self.save_ui_to_cfg()
        if self.cfg.get("auto_mode", True) and not effective_buttons(self.cfg):
            self.log_msg("АВТО: кнопок пока нет — пробую найти...")
            try:
                res = auto_find_all(self.cfg)
                self.refresh_labels()
                if res.get("ok"):
                    self.log_msg(f"АВТО нашел: {res.get('msg')}")
                else:
                    self.log_msg(f"АВТО пока не нашел: {res.get('msg')} — включусь, открой тест!")
            except Exception as e:
                self.log_msg(f"АВТО ошибка: {e}")
        btns = effective_buttons(self.cfg)
        if not btns:
            if not messagebox.askyesno("Кнопки не найдены",
                                       "Кнопки 4-5-6-7 не найдены.\nВключить цикл вслепую? Открой тест и жми Да."):
                return
        self.enabled = True
        self.stop_event.clear()
        try:
            self.big_btn.configure(text="■ ВЫКЛЮЧИТЬ (F7)", bg="#e74c3c")
        except Exception:
            pass
        wb = float(self.cfg.get("wait_before", 5.0))
        wa = float(self.cfg.get("wait_after", 6.0))
        self.status_var.set(f"Статус: РАБОТАЕТ по кругу {wb:.0f}с -> ответ -> Ответить -> {wa:.0f}с...")
        mode_txt = "рандом 4-5-6-7" if self.cfg.get("mode") == "random" else "только 5"
        self.log_msg(f"=== ВКЛЮЧЕНО БЕЗ ТАЙМЕРА ({mode_txt}, {wb}с до / {wa}с после). F7 — выключить. ===")
        self.last_question_hash = None
        self.worker = threading.Thread(target=self.loop, daemon=True)
        self.worker.start()

    def disable(self):
        if not self.enabled:
            # все равно обновить UI (для глобального хоткея)
            try:
                self.big_btn.configure(text="▶ ВКЛЮЧИТЬ", bg="#2ecc71")
                self.status_var.set("Статус: ВЫКЛЮЧЕНО (F7 — выключить, F6 — включить)")
            except Exception:
                pass
            return
        self.enabled = False
        self.stop_event.set()
        try:
            self.big_btn.configure(text="▶ ВКЛЮЧИТЬ", bg="#2ecc71")
            self.status_var.set("Статус: ВЫКЛЮЧЕНО (F7 — выключить, F6 — включить)")
        except Exception:
            pass
        # вызываться может из hotkey-потока, log через очередь безопасен
        self.log_q.put(f"[{time.strftime('%H:%M:%S')}] === ВЫКЛЮЧЕНО (F7) ===")

    def on_close(self):
        try:
            self.disable()
        except Exception:
            pass
        try:
            if keyboard_lib is not None:
                keyboard_lib.unhook_all_hotkeys()
        except Exception:
            pass
        self.root.destroy()

    # ---------- главный цикл БЕЗ ТАЙМЕРА: 5с -> ответ -> Ответить -> 6с -> круг ----------
    def _sleep_break(self, secs, status_fmt=None):
        """Спать secs с проверкой F7/стоп каждые 0.05с. Возвращает False если надо выйти."""
        t0 = time.time()
        while time.time() - t0 < secs:
            if self.stop_event.is_set():
                return False
            if status_fmt:
                try:
                    left = max(0.0, secs - (time.time() - t0))
                    self.status_var.set(status_fmt.format(left))
                except Exception:
                    pass
            time.sleep(0.05)
        return True

    def loop(self):
        cfg = self.cfg
        self.log_msg(f"Старт БЕЗ ТАЙМЕРА. buttons={effective_buttons(cfg)}")

        while not self.stop_event.is_set():
            try:
                # live-поиск если кнопок нет
                btns_cached = effective_buttons(cfg)
                if cfg.get("auto_mode", True) and not btns_cached and cv2 is not None:
                    try:
                        res = auto_find_all(cfg)
                        try:
                            self.refresh_labels()
                        except Exception:
                            pass
                        if not res.get("ok"):
                            try:
                                self.status_var.set("Статус: ищу шкалу 1-10... открой тест!")
                            except Exception:
                                pass
                            if not self._sleep_break(1.0):
                                return
                            continue
                    except Exception:
                        pass

                # 1) фиксированная пауза ДО ответа вместо таймера (5 сек)
                wb = float(cfg.get("wait_before", 5.0))
                jitter = float(cfg.get("wait_jitter", 0.6))
                wait_b = max(0.5, wb + random.uniform(-jitter / 2, jitter / 2))
                self.log_msg(f"Жду {wait_b:.1f} сек до ответа (вместо таймера)...")
                if not self._sleep_break(wait_b, "Статус: жду до ответа {0:.1f}с..."):
                    return

                # 2) рандом 4-5-6-7
                choice = choose_answer(cfg, self.last_choice)
                if choice is None:
                    self.log_msg("Нет кнопок 4-7 — жду 2 сек и повторяю поиск.")
                    if not self._sleep_break(2.0):
                        return
                    continue
                btns_now = effective_buttons(cfg)
                if not btns_now:
                    btns_now = get_buttons(cfg)
                if choice not in btns_now:
                    choice = sorted(btns_now.keys())[0]
                x, y = btns_now[choice]
                self.last_choice = choice
                try:
                    self.stats[choice] = self.stats.get(choice, 0) + 1
                except Exception:
                    pass
                try:
                    self.status_var.set(f"Статус: выпало {choice} — кликаю...")
                    self.lblLast.set(f"Последний выбор: {choice}")
                    s = self.stats
                    self.lblStats.set(f"Статистика: 4:{s.get(4,0)}  5:{s.get(5,0)}  6:{s.get(6,0)}  7:{s.get(7,0)}")
                except Exception:
                    pass

                mspeed = float(cfg.get("mouse_speed", 0.08))
                self.log_msg(f"Цифра {choice} ({x},{y}) — доводка {mspeed}с...")
                if not human_move_to(x, y, duration=mspeed):
                    self.log_msg("Не смог двинуть мышь.")
                    continue
                if self.stop_event.is_set():
                    return

                # клик по цифре (обязательно по твоей схеме)
                if bool(cfg.get("click_choice", True)):
                    time.sleep(random.uniform(0.05, 0.15))
                    try:
                        pyautogui.click(x, y)
                    except Exception as e:
                        self.log_msg(f"Клик не вышел: {e}")
                    self.log_msg(f"Нажал {choice}.")
                else:
                    self.log_msg(f"Навел на {choice} без клика.")
                if self.stop_event.is_set():
                    return

                # 3) подтвердить через Ответить — ищем LIVE каждый круг
                if bool(cfg.get("click_answer", True)):
                    # всегда свежий поиск (окно могло проскроллиться)
                    live_ans = None
                    live_msg = ""
                    if cv2 is not None:
                        try:
                            pil2, arr2 = _grab_full()
                            if arr2 is not None:
                                ans2, _, msg2 = detect_answer_button(pil2, arr2)
                                live_msg = msg2
                                if ans2:
                                    live_ans = [int(ans2[0]), int(ans2[1]), int(ans2[2]), int(ans2[3])]
                                    cfg["auto_answer"] = [int(x) for x in live_ans]
                                    try:
                                        save_config(cfg)
                                    except Exception:
                                        pass
                        except Exception as e:
                            live_msg = f"live error {e}"
                    ans_pt = None
                    if live_ans:
                        ans_pt = [live_ans[0], live_ans[1]]
                        self.log_msg(f"Ответить LIVE: {live_ans} ({live_msg})")
                    else:
                        ans_pt = effective_answer(cfg)
                        if ans_pt is None:
                            ans_pt = cfg.get("btn_answer")
                        self.log_msg(f"Ответить из кэша: {ans_pt} (live: {live_msg})")
                    if ans_pt:
                        time.sleep(random.uniform(0.15, 0.3))
                        ax, ay = int(ans_pt[0]), int(ans_pt[1])
                        self.log_msg(f"Веду на Ответить ({ax},{ay}) и кликаю...")
                        human_move_to(ax, ay, duration=mspeed)
                        if self.stop_event.is_set():
                            return
                        time.sleep(random.uniform(0.05, 0.12))
                        try:
                            pyautogui.moveTo(ax, ay, duration=0)
                            time.sleep(0.05)
                            pyautogui.click(ax, ay)
                            # контроль: где мышь после клика
                            mx, my = pyautogui.position()
                            self.log_msg(f"Клик по Ответить ({ax},{ay}), мышь сейчас ({mx},{my}).")
                        except Exception as e:
                            self.log_msg(f"Клик Ответить не вышел: {e}")
                        self.log_msg("Нажал Ответить — вопрос подтвержден.")
                    else:
                        self.log_msg("ОТВЕТЕР НЕ НАЙДЕН ни live ни кэш. Нажми 'НАЙТИ АВТОМАТИЧЕСКИ' или захвати вручную. Жду 3 сек.")
                        if not self._sleep_break(3.0):
                            return
                else:
                    self.log_msg("Авто-клик Ответить выключен в настройках — подтверди сам.")

                # 4) фиксированная пауза ПОСЛЕ Ответить (6 сек) и снова по кругу
                wa = float(cfg.get("wait_after", 6.0))
                wait_a = max(0.5, wa + random.uniform(-jitter / 2, jitter / 2))
                self.log_msg(f"Жду {wait_a:.1f} сек после Ответить — и снова по кругу...")
                if not self._sleep_break(wait_a, "Статус: после Ответить жду {0:.1f}с..."):
                    return

            except Exception as e:
                try:
                    self.log_msg(f"Ошибка цикла: {e}")
                except Exception:
                    print(e)
                time.sleep(0.5)


def main():
    root = tk.Tk()
    # стиль
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
