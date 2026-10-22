# -*- coding: utf-8 -*-
"""Telegram-бот: присылаешь mp3 с подписью-жанром — получаешь готовое видео для Reels/TikTok.

Запускается GitHub Actions по расписанию (см. .github/workflows/beatbot.yml):
  python bot.py check  — быстро смотрит, есть ли новые биты (без тяжёлых библиотек)
  python bot.py run    — делает видео и отправляет их в Telegram

Переменные окружения (секреты репозитория):
  TELEGRAM_TOKEN  — токен от @BotFather (обязательно)
  PIXABAY_API_KEY — ключ pixabay.com/api/docs для автоподбора фона (очень желательно)
  PEXELS_API_KEY  — старый ключ Pexels, если есть (необязательно)
  OWNER_ID        — твой Telegram ID (чтобы ботом не пользовались чужие), можно несколько через запятую
  PRODUCER_NAME   — подпись под шапкой видео, например "prod. Vsepodryat" (необязательно)
"""
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

try:  # тяжёлые библиотеки нужны только для сборки видео
    import numpy as np
    import cv2
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    np = cv2 = Image = ImageDraw = ImageFilter = ImageFont = None

# ===========================================================================
# styles
# ===========================================================================
# ---------------------------------------------------------------------------
# Шрифты. Скачиваются из Google Fonts (GitHub) при первом запуске.
# Если скачать не удалось — берётся запасной системный шрифт.
# ---------------------------------------------------------------------------
GF = "https://github.com/google/fonts/raw/main/ofl/"
FONTS = {
    "serif_italic": {
        "url": GF + "playfairdisplay/PlayfairDisplay-Italic%5Bwght%5D.ttf",
        "file": "PlayfairDisplay-Italic.ttf",
        "weight": 600,
        "fallback": [
            "/usr/share/fonts/truetype/google-fonts/Lora-Italic-Variable.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        ],
    },
    "luxury": {
        "url": GF + "unbounded/Unbounded%5Bwght%5D.ttf",
        "file": "Unbounded.ttf",
        "weight": 800,
        "fallback": [
            "/usr/share/fonts/opentype/inter/InterDisplay-Black.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
    },
    "mono": {
        "url": GF + "ptmono/PTM55FT.ttf",
        "file": "PTMono.ttf",
        "weight": None,
        "fallback": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
        ],
    },
    "condensed": {
        "url": GF + "oswald/Oswald%5Bwght%5D.ttf",
        "file": "Oswald.ttf",
        "weight": 700,
        "fallback": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
    },
    "heavy": {
        "url": GF + "russoone/RussoOne-Regular.ttf",
        "file": "RussoOne.ttf",
        "weight": None,
        "fallback": [
            "/usr/share/fonts/opentype/inter/Inter-Black.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
    },
    "script": {
        "url": GF + "lobster/Lobster-Regular.ttf",
        "file": "Lobster.ttf",
        "weight": None,
        "fallback": [
            "/usr/share/fonts/truetype/google-fonts/Lora-Italic-Variable.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
        ],
    },
    "ui": {
        "url": GF + "montserrat/Montserrat%5Bwght%5D.ttf",
        "file": "Montserrat.ttf",
        "weight": 700,
        "fallback": [
            "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
    },
}

# ---------------------------------------------------------------------------
# Общие хэштеги и призыв внизу экрана
# ---------------------------------------------------------------------------
BASE_TAGS = ["#бит", "#битдлярэпа", "#инструментал", "#битмейкер", "#рэп", "#typebeat", "#beats"]
CTA_TEXT = "БИТ В ДИРЕКТ"   # маленькая надпись внизу (можно поменять или сделать пустой "")

# ---------------------------------------------------------------------------
# Жанры
#   aliases  — слова, по которым бот понимает жанр из подписи к mp3
#   queries  — что искать на Pixabay/Pexels для фона (на английском)
#   grade    — цветокоррекция ffmpeg
#   cut_sec  — примерная длина одного кадра до смены (подгоняется под доли)
#   speed    — скорость фонового видео (меньше 1 — замедление)
#   xfade    — плавный переход между кадрами (сек), 0 = резкая склейка
#   fx       — эффекты на бочку
#   text     — анимация главного текста
# ---------------------------------------------------------------------------
GENRES = {
    "lyric": {
        "title": "Лирика",
        "label": "LYRIC TYPE BEAT",
        "aliases": ["лирика", "лирик", "лирический", "лиричный", "грусть", "грустный", "sad", "lyric"],
        "queries": [
            "rain window", "rain on glass", "autumn leaves falling", "sad woman window",
            "rainy city night", "foggy forest", "couple silhouette", "empty street rain",
            "autumn park", "lonely man rain", "candle dark", "raindrops", "sea waves cloudy",
        ],
        "grade_variants": [
            # чёрно-белый
            "hue=s=0,eq=contrast=1.12:brightness=-0.03:gamma=0.95,noise=alls=9:allf=t,vignette=angle=PI/4.2",
            # холодный приглушённый
            "eq=contrast=1.08:saturation=0.45:brightness=-0.04,colorbalance=bs=0.10:bm=0.06:rs=-0.04,noise=alls=8:allf=t,vignette=angle=PI/4.2",
        ],
        "cut_sec": 3.0,
        "speed": 0.85,
        "xfade": 0.45,
        "xfade_types": ["fade", "fade", "dissolve", "fadeblack"],
        "fx": {"zoom": 0.012, "flash": 0.07, "flash_color": (235, 240, 255), "flash_on": "bar"},
        "text": {"anim": "rain", "font": "serif_italic", "size": 96, "upper": False,
                 "fill": (245, 247, 252), "stroke": 0, "shadow": 0.75, "glow": (190, 210, 255), "glow_r": 14, "glow_a": 0.35},
        "tags": ["#лирика", "#грустныйбит", "#лирическийбит", "#sadbeat", "#lyricbeat"],
        "messages": [
            "Если ты рэпер — запиши сюда свою боль",
            "Этот бит для тех, кто так и не сказал «прости»",
            "Сможешь зачитать про неё так, чтобы она услышала?",
            "Рэпер, этот бит ждёт твой самый честный куплет",
            "Тут нужен голос того, кто правда любил",
            "Зачитай сюда то, что не смог сказать ей в лицо",
            "Осень, дождь и твой куплет. Сможешь?",
            "Этот бит для разбитых сердец. Твоё подойдёт?",
            "Если листаешь это ночью — бит твой",
            "Положи на этот бит слова, которые болят",
            "Она ушла. Остался только этот бит и ты",
            "Сможешь зачитать сюда так, чтобы у неё дрогнуло сердце?",
        ],
    },
    "trap": {
        "title": "Трэп",
        "label": "TRAP TYPE BEAT",
        "aliases": ["трэп", "трап", "треп", "trap", "лакшери", "luxury"],
        "queries": [
            "luxury car night", "sports car", "gold bars", "money cash counting", "luxury watch",
            "night club lights", "penthouse night city", "private jet", "champagne pouring",
            "diamond jewelry", "luxury lifestyle", "city skyline night drone",
        ],
        "grade_variants": [
            "eq=contrast=1.18:saturation=1.25:brightness=-0.02,colorbalance=rs=0.06:gs=0.02:bs=-0.08:rh=0.05:bh=-0.06,noise=alls=5:allf=t,vignette=angle=PI/4",
            "eq=contrast=1.22:saturation=1.1:brightness=-0.04,colorbalance=rs=0.04:bs=-0.05:rh=0.08:gh=0.04:bh=-0.08,vignette=angle=PI/4",
        ],
        "cut_sec": 1.4,
        "speed": 1.0,
        "xfade": 0,
        "fx": {"zoom": 0.05, "shake": 4, "flash": 0.22, "flash_color": (255, 236, 190), "flash_on": "kick"},
        "text": {"anim": "gold", "font": "luxury", "size": 88, "upper": True,
                 "fill": "gold", "stroke": 4, "stroke_fill": (40, 26, 4), "shadow": 0.85, "glow": (255, 200, 90), "glow_r": 16, "glow_a": 0.35},
        "tags": ["#трэп", "#trapbeat", "#трэпбит", "#luxury", "#808"],
        "messages": [
            "Рэпер, этот бит звучит как первый миллион",
            "Сможешь зачитать сюда так, чтобы залетело в чарты?",
            "Этот бит стоит дороже твоих часов",
            "Бит для тех, кто поднялся из ниоткуда",
            "Если ты листаешь это — твой хит уже здесь",
            "Зачитай сюда и пусть считают твои цифры",
            "Тут нужен флоу, а не слова. Покажешь?",
            "Этот бит не для слабых куплетов",
            "Деньги любят тех, кто не останавливается. Бит твой",
            "Бит заряжен. Осталось нажать запись",
            "Сюда нужен куплет на миллион просмотров",
            "Отметь рэпера, который разнесёт этот бит",
        ],
    },
    "underground": {
        "title": "Андеграунд",
        "label": "OLDSCHOOL TYPE BEAT",
        "aliases": ["андеграунд", "андер", "андерграунд", "олдскул", "oldschool", "underground", "90", "90е", "бумбэп", "boombap", "подъезд"],
        "queries": [
            "graffiti wall", "street basketball", "old apartment stairwell", "breakdance street",
            "concrete city", "vintage street 90s", "boombox", "vinyl record", "subway train night",
            "hooded man street", "skateboard street", "old city courtyard",
        ],
        "grade_variants": [
            "scale=540:960,scale=1080:1920:flags=bilinear,colorchannelmixer=.55:.35:.1:0:.25:.6:.15:0:.2:.35:.45,eq=contrast=1.12:saturation=0.75:brightness=-0.02,rgbashift=rh=-4:bh=4,noise=alls=16:allf=t+u,vignette=angle=PI/3.6",
            "scale=540:960,scale=1080:1920:flags=bilinear,hue=s=0.35,eq=contrast=1.15:brightness=-0.03,colorbalance=rs=0.06:rm=0.05:bs=-0.05,rgbashift=rh=-3:bh=3,noise=alls=18:allf=t+u,vignette=angle=PI/3.6",
        ],
        "cut_sec": 2.2,
        "speed": 1.0,
        "xfade": 0,
        "fx": {"zoom": 0.025, "shake": 2, "flash": 0.0, "scanlines": True, "tracking": True},
        "text": {"anim": "vhs", "font": "mono", "size": 76, "upper": True, "align": "left",
                 "fill": (242, 238, 222), "stroke": 0, "shadow": 1.0, "glow": None},
        "tags": ["#андеграунд", "#олдскул", "#boombap", "#oldschool", "#рэп90х"],
        "messages": [
            "Олдскул не умер. Докажи под этот бит",
            "Рэпер, помнишь, зачем ты начинал? Зачитай об этом",
            "Бит из подъезда для тех, кто читает от души",
            "Тут нужна правда, а не понты. Сможешь?",
            "Этот бит пахнет кассетами и бетоном",
            "Слова решают. Покажи, что тебе есть что сказать",
            "Для тех, кто писал тексты на последней парте",
            "Без автотюна. Только ты, бит и правда",
            "Рэпер, район ждёт твой куплет",
            "Зачитай так, будто это твой последний шанс",
            "Настоящий рэп начинается с такого бита",
            "Отметь того, кто читает по-настоящему",
        ],
    },
    "drill": {
        "title": "Дрилл",
        "label": "DRILL TYPE BEAT",
        "aliases": ["дрилл", "дрил", "drill"],
        "queries": [
            "hooded man night", "dark street night", "tunnel night", "city night rain dark",
            "smoke dark", "night underground parking", "london night street", "man walking night alley",
            "police lights night", "dark staircase",
        ],
        "grade_variants": [
            "eq=contrast=1.28:saturation=0.45:brightness=-0.06,colorbalance=bs=0.16:bm=0.10:rs=-0.06:rm=-0.04,noise=alls=7:allf=t,vignette=angle=PI/3.8",
        ],
        "cut_sec": 1.2,
        "speed": 1.0,
        "xfade": 0,
        "fx": {"zoom": 0.045, "shake": 5, "flash": 0.18, "flash_color": (160, 210, 255), "flash_on": "kick",
               "rgb_split": 10, "glitch": True},
        "text": {"anim": "glitch", "font": "condensed", "size": 112, "upper": True,
                 "fill": (246, 250, 255), "stroke": 0, "shadow": 0.8, "glow": (70, 170, 255), "glow_r": 18, "glow_a": 0.55},
        "tags": ["#дрилл", "#drill", "#drillbeat", "#дриллбит", "#ukdrill"],
        "messages": [
            "Этот бит не прощает слабых. Зачитаешь?",
            "Рэпер, тут нужен голос, от которого холодно",
            "Ночь, улицы и твой куплет. Покажи себя",
            "Бит для тех, кто не отступает",
            "Сможешь удержать флоу на этом бите?",
            "Тут нет места для лишних слов",
            "Холодный бит для холодной подачи",
            "Если ты дриллер — это твой вызов",
            "Зачитай так, чтобы было слышно на весь район",
            "Подача решает всё. Докажи",
            "Отметь того, кто вытащит этот бит",
        ],
    },
    "phonk": {
        "title": "Фонк",
        "label": "PHONK",
        "aliases": ["фонк", "phonk", "дрифт", "drift"],
        "queries": [
            "car drift smoke", "drift car night", "neon city night", "car burnout smoke",
            "night highway car fast", "tokyo night street", "car headlights night", "racing car night",
            "street racing", "smoke red light",
        ],
        "grade_variants": [
            "eq=contrast=1.35:saturation=0.85:brightness=-0.06,colorbalance=rs=0.24:rm=0.14:gs=-0.05:bs=-0.08,noise=alls=12:allf=t,vignette=angle=PI/3.5",
            "eq=contrast=1.3:saturation=1.1:brightness=-0.05,colorbalance=rs=0.18:bs=0.12:gs=-0.08:rm=0.08:bm=0.08,noise=alls=10:allf=t,vignette=angle=PI/3.5",
        ],
        "cut_sec": 0.9,
        "speed": 1.15,
        "xfade": 0,
        "fx": {"zoom": 0.085, "shake": 14, "rot": 1.2, "flash": 0.3, "flash_color": (255, 40, 40), "flash_on": "kick",
               "rgb_split": 6, "invert": True},
        "text": {"anim": "shake", "font": "heavy", "size": 104, "upper": True,
                 "fill": (255, 255, 255), "stroke": 6, "stroke_fill": (12, 0, 0), "shadow": 0.6, "glow": (255, 30, 30), "glow_r": 20, "glow_a": 0.7},
        "tags": ["#фонк", "#phonk", "#drift", "#дрифт", "#phonkmusic"],
        "messages": [
            "Этот бит качает сильнее твоих колонок. Зачитаешь?",
            "Рэпер, дрифтуй по этому биту, если сможешь",
            "Сюда нужен голос, который рвёт динамики",
            "Включи на полную и попробуй удержаться",
            "Бит для ночных трасс и громких куплетов",
            "Сможешь перекричать этот бас?",
            "Газ в пол. Бит уже ждёт",
            "Этот бит качает — твоя очередь",
            "Слабые куплеты сюда не залетают",
            "Отметь того, кто зачитает сюда без страха",
        ],
    },
    "kavkaz": {
        "title": "Кавказский качающий",
        "label": "КАВКАЗСКИЙ БИТ",
        "aliases": ["кавказ", "кавказский", "качающий", "kavkaz", "лезгинка"],
        "queries": [
            "mountains sunset", "night highway driving", "city lights night", "car interior night driving",
            "mountain road", "caucasus mountains", "couple night city", "night city from car window",
            "clouds mountains timelapse", "woman walking night city", "fireworks night",
        ],
        "grade_variants": [
            "eq=contrast=1.1:saturation=1.12:brightness=0.0,colorbalance=rs=0.08:rm=0.06:bs=-0.06:bm=-0.03,noise=alls=5:allf=t,vignette=angle=PI/4.3",
            "eq=contrast=1.12:saturation=0.95:brightness=-0.02,colorbalance=rs=0.1:gs=0.03:bs=-0.08:rh=0.05,noise=alls=5:allf=t,vignette=angle=PI/4.3",
        ],
        "cut_sec": 2.4,
        "speed": 0.92,
        "xfade": 0.35,
        "xfade_types": ["fade", "fade", "smoothup", "dissolve"],
        "fx": {"zoom": 0.03, "flash": 0.08, "flash_color": (255, 210, 150), "flash_on": "bar"},
        "text": {"anim": "glow", "font": "script", "size": 118, "upper": False,
                 "fill": (255, 246, 228), "stroke": 0, "shadow": 0.7, "glow": (255, 170, 70), "glow_r": 22, "glow_a": 0.75},
        "tags": ["#кавказскаямузыка", "#кавказ", "#качающийбит", "#кавказскийбит", "#лезгинка"],
        "messages": [
            "Этот бит про неё. Зачитаешь так, чтобы она поняла?",
            "Брат, тут нужен куплет от сердца",
            "Ночная трасса, горы и твой голос. Сможешь?",
            "Бит, под который вспоминаешь её. Запиши сюда свою историю",
            "Качает так, что хочется петь. Споёшь?",
            "Для тех, кто любит один раз и навсегда",
            "Этот бит ждёт твою лучшую песню о любви",
            "Под такой бит пишут хиты. Твой будет следующим?",
            "Бит для тех, кто чувствует каждой нотой",
            "Спой сюда то, что она должна услышать",
            "Отметь того, кто споёт сюда до мурашек",
        ],
    },
}

GENRE_ORDER = ["lyric", "trap", "underground", "drill", "phonk", "kavkaz"]


def find_genre(word, partial=True):
    """Возвращает ключ жанра по слову из подписи (или None)."""
    w = word.lower().strip(" .,!?:;#-—").replace("ё", "е")
    if not w:
        return None
    for key in GENRE_ORDER:
        for a in GENRES[key]["aliases"]:
            if w == a.replace("ё", "е"):
                return key
    if not partial:
        return None
    # частичное совпадение для длинных слов ("лиричненький", "трэповый")
    for key in GENRE_ORDER:
        for a in GENRES[key]["aliases"]:
            a = a.replace("ё", "е")
            if len(a) >= 4 and len(w) >= 4 and (w.startswith(a[:4]) and a.startswith(w[:4])):
                return key
    return None

# ===========================================================================
# analysis
# ===========================================================================
SR = 22050
HOP = 256
NFFT = 2048
FPS_A = SR / HOP  # кадров анализа в секунду (~43)


def load_audio(path, sr=SR):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    y = np.frombuffer(out, dtype=np.float32).copy()
    if y.size < sr:
        raise ValueError("Файл слишком короткий или не читается")
    return y


def _stft_mag(y):
    win = np.hanning(NFFT).astype(np.float32)
    pad = NFFT // 2
    y = np.pad(y, (pad, pad), mode="reflect")
    n = 1 + (len(y) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    mags = []
    step = 2048  # по кускам, чтобы не съесть память на длинных треках
    for s in range(0, n, step):
        fr = y[idx[s:s + step]] * win
        mags.append(np.abs(np.fft.rfft(fr, axis=1)).astype(np.float32))
    return np.concatenate(mags, axis=0)  # (frames, bins)


def _flux(S, lo_hz, hi_hz):
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    band = (freqs >= lo_hz) & (freqs < hi_hz)
    L = np.log1p(100.0 * S[:, band])
    d = np.diff(L, axis=0, prepend=L[:1])
    f = np.maximum(d, 0).sum(axis=1)
    f = f - _moving_avg(f, int(FPS_A * 0.5))
    f = np.maximum(f, 0)
    m = np.percentile(f, 99) + 1e-9
    return np.clip(f / m, 0, 1.5)


def _moving_avg(x, n):
    n = max(1, n)
    k = np.ones(n) / n
    return np.convolve(x, k, mode="same")


def _tempo(onset):
    """BPM по автокорреляции огибающей атак с приоритетом около 120."""
    o = onset - onset.mean()
    n = len(o)
    ac = np.fft.irfft(np.abs(np.fft.rfft(o, 2 * n)) ** 2)[:n]
    ac = ac / (ac[0] + 1e-9)
    bpms = np.arange(60.0, 200.0, 0.25)
    lags = 60.0 * FPS_A / bpms
    vals = np.interp(lags, np.arange(n), ac)
    # гармоническое усиление: учитываем двойной период
    vals2 = np.interp(lags * 2, np.arange(n), ac)
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    score = (vals + 0.5 * vals2) * prior
    return float(bpms[int(np.argmax(score))])


def _track_beats(onset, bpm, tightness=100.0):
    """Динамическое программирование (Ellis 2007)."""
    period = 60.0 * FPS_A / bpm
    n = len(onset)
    sd = period / 32.0 * 4
    w = np.exp(-0.5 * (np.arange(-int(period), int(period) + 1) / max(sd, 1)) ** 2)
    local = np.convolve(onset, w, mode="same")
    cum = np.zeros(n)
    back = -np.ones(n, dtype=int)
    lo = int(round(period / 2))
    hi = int(round(2 * period))
    for i in range(n):
        a = i - hi
        b = i - lo
        if b < 0:
            cum[i] = local[i]
            continue
        a = max(a, 0)
        prev = np.arange(a, b + 1)
        tx = -tightness * np.log((i - prev) / period) ** 2
        cand = cum[prev] + tx
        j = int(np.argmax(cand))
        cum[i] = local[i] + cand[j]
        back[i] = prev[j]
    # конец: последний максимум среди последних тактов
    tail = max(1, int(period * 2))
    end = n - tail + int(np.argmax(cum[n - tail:]))
    beats = [end]
    while back[beats[-1]] >= 0:
        beats.append(back[beats[-1]])
    beats = np.array(beats[::-1])
    # отбрасываем «пустые» доли в тишине в начале/конце
    thr = 0.05 * np.median(local[beats] + 1e-9)
    beats = beats[local[beats] > thr]
    return beats / FPS_A


def _peaks(env, min_gap, thr):
    out = []
    last = -1e9
    for i in range(1, len(env) - 1):
        if env[i] >= thr and env[i] >= env[i - 1] and env[i] > env[i + 1]:
            if i - last >= min_gap:
                out.append(i)
                last = i
            elif env[i] > env[out[-1]]:
                out[-1] = i
                last = i
    return np.array(out, dtype=int)


def analyze(path):
    y = load_audio(path)
    S = _stft_mag(y)
    onset = _flux(S, 30, 8000)
    low = _flux(S, 30, 160)
    high = _flux(S, 3000, 11000)
    bpm = _tempo(onset)
    beats = _track_beats(onset, bpm)
    # уточняем темп по найденным долям (линейная регрессия) и трекаем ещё раз
    for _ in range(2):
        if len(beats) < 16:
            break
        idx = np.arange(len(beats))
        slope = np.polyfit(idx, beats, 1)[0]
        bpm_est = 60.0 / slope
        if 0.9 < bpm_est / bpm < 1.1:
            bpm = float(bpm_est)
            beats = _track_beats(onset, bpm, tightness=200.0)

    # поправка: центрированное окно STFT «видит» удар чуть раньше
    LAT = 0.022
    beats = beats + LAT

    # сильная доля такта: какая из 4 фаз чаще совпадает с бочкой
    bi = np.clip(np.round((beats - LAT) * FPS_A).astype(int), 0, len(low) - 1)
    best_phase, best = 0, -1
    for ph in range(4):
        # бочка — плюс, малый барабан (высокие частоты на 2 и 4 долях) — минус
        s = low[bi[ph::4]].sum() - 0.6 * high[bi[ph::4]].sum() + 0.15 * onset[bi[ph::4]].sum()
        if s > best:
            best, best_phase = s, ph
    downbeats = beats[best_phase::4]

    # удары бочки (для вспышек и «панчей»)
    kick_idx = _peaks(low, int(FPS_A * 0.11), 0.28)
    kicks = kick_idx / FPS_A + LAT
    kick_str = np.clip(low[kick_idx] / (np.percentile(low[kick_idx], 90) + 1e-9), 0, 1) if len(kick_idx) else np.array([])

    # громкость (RMS) по кадрам анализа
    frames = len(S)
    rms = np.sqrt(np.mean(S ** 2, axis=1))
    rms = rms / (np.percentile(rms, 98) + 1e-9)

    disp = bpm
    while disp < 72:
        disp *= 2
    while disp > 185:
        disp /= 2

    return {
        "duration": len(y) / SR,
        "bpm": float(bpm),
        "bpm_display": int(round(disp)),
        "beats": beats,
        "downbeats": downbeats,
        "kicks": kicks,
        "kick_str": kick_str,
        "onset": onset,
        "low": low,
        "high": high,
        "rms": np.clip(rms, 0, 1.5),
        "env_fps": FPS_A,
    }


def pick_segment(info, target=24.0, min_len=14.0, max_len=30.0):
    """Лучший кусок: самый громкий/плотный, начинается с сильной доли,
    длина — целое число тактов (кратно 4 тактам, если получается)."""
    dur = info["duration"]
    db = info["downbeats"]
    beats = info["beats"]
    if len(beats) < 8 or dur <= min_len + 1:
        return 0.0, min(dur, max_len)
    bar = 4 * 60.0 / info["bpm"]
    # подбираем число тактов
    options = []
    for nb in range(2, 64):
        L = nb * bar
        if min_len <= L <= max_len:
            pref = 0 if nb % 4 == 0 else (1 if nb % 2 == 0 else 2)
            options.append((pref, abs(L - target), nb, L))
    if not options:
        L = min(target, dur)
    else:
        options.sort()
        L = options[0][3]
    if L >= dur - 0.5:
        return 0.0, dur

    fps = info["env_fps"]
    rms = info["rms"]
    on = info["onset"]
    lowe = info["low"]
    dens = 0.6 * rms + 0.25 * _moving_avg(on, int(fps)) + 0.15 * _moving_avg(lowe, int(fps))
    csum = np.concatenate([[0], np.cumsum(dens)])
    best_s, best_v = 0.0, -1
    cands = [d for d in db if d + L <= dur - 0.2]
    if not cands:
        cands = [b for b in beats if b + L <= dur - 0.2] or [0.0]
    for s in cands:
        a = int(s * fps)
        b = int((s + L) * fps)
        v = (csum[b] - csum[a]) / max(1, b - a)
        # лёгкий штраф за самое начало трека (часто там интро)
        if s < 2 * bar:
            v *= 0.93
        if v > best_v:
            best_v, best_s = v, s
    return float(best_s), float(L)


def cut_points(info, start, length, cut_sec, fps, rng):
    """Моменты смены кадров (секунды от начала куска), строго на долях."""
    beats = info["beats"]
    b = beats[(beats >= start - 1e-3) & (beats < start + length - 0.05)] - start
    if len(b) < 2:
        n = max(1, int(length // cut_sec))
        return [i * length / n for i in range(n)]
    beat_len = float(np.median(np.diff(b)))
    k = 1
    for cand in (1, 2, 4, 8, 16):
        if abs(cand * beat_len - cut_sec) < abs(k * beat_len - cut_sec):
            k = cand
    pts = [0.0]
    i = k
    while i < len(b):
        pts.append(float(b[i]))
        i += k
    # убираем слишком короткий хвост
    if length - pts[-1] < 0.5 * k * beat_len and len(pts) > 1:
        pts.pop()
    return pts


def envelopes(info, start, length, fps):
    """Покадровые огибающие для эффектов (кадр видео -> значение 0..1)."""
    n = int(round(length * fps))
    t = np.arange(n) / fps + start
    ef = info["env_fps"]

    def sample(env):
        return np.interp(t * ef, np.arange(len(env)), env)

    # «панч» от бочки: быстрый удар и спад
    kick_pulse = np.zeros(n)
    for kt, ks in zip(info["kicks"], info["kick_str"]):
        if kt < start - 0.5 or kt > start + length:
            continue
        dt = t - kt
        m = dt >= 0
        kick_pulse[m] = np.maximum(kick_pulse[m], ks * np.exp(-dt[m] / 0.13))

    beat_pulse = np.zeros(n)
    for bt in info["beats"]:
        if bt < start - 0.5 or bt > start + length:
            continue
        dt = t - bt
        m = dt >= 0
        beat_pulse[m] = np.maximum(beat_pulse[m], np.exp(-dt[m] / 0.18))

    bar_pulse = np.zeros(n)
    for bt in info["downbeats"]:
        if bt < start - 0.5 or bt > start + length:
            continue
        dt = t - bt
        m = dt >= 0
        bar_pulse[m] = np.maximum(bar_pulse[m], np.exp(-dt[m] / 0.25))

    rms = sample(info["rms"])
    k = max(1, int(fps * 0.4))
    rms = np.convolve(rms, np.ones(k) / k, mode="same")
    beats_rel = [float(b - start) for b in info["beats"] if start - 1e-3 <= b < start + length]
    downs_rel = [float(b - start) for b in info["downbeats"] if start - 1e-3 <= b < start + length]
    kicks_rel = [(float(a - start), float(s)) for a, s in zip(info["kicks"], info["kick_str"]) if start <= a < start + length]
    return {
        "n": n,
        "kick": np.clip(kick_pulse, 0, 1),
        "beat": beat_pulse,
        "bar": bar_pulse,
        "rms": np.clip(rms, 0, 1.2),
        "beats": beats_rel,
        "downbeats": downs_rel,
        "kicks": kicks_rel,
        "beat_len": 60.0 / info["bpm"],
    }

# ===========================================================================
# backgrounds
# ===========================================================================
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
UA = "Mozilla/5.0 (beat-video-bot)"


def probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=30).stdout
        return float(json.loads(out)["format"]["duration"])
    except Exception:
        return 0.0


def own_clips(genre, root="backgrounds"):
    """Свои ролики: backgrounds/lyric/*.mp4 и т.п. + общая папка backgrounds/all/."""
    found = []
    for sub in (genre, "all"):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(VIDEO_EXT):
                    found.append(os.path.join(d, f))
    return found


def _http_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _pick_file(video):
    """Лучший mp4: вертикальный, высота ближе к 1920, не огромный."""
    files = [f for f in video.get("video_files", []) if (f.get("file_type") or "").endswith("mp4")
             and f.get("width") and f.get("height")]
    if not files:
        return None

    def score(f):
        w, h = f["width"], f["height"]
        portrait = h > w
        return (0 if portrait else 1, abs(h - 1920) if h >= 1080 else 5000 + (1080 - h))
    files.sort(key=score)
    return files[0]


def pexels_clips(queries, n, key, workdir, rng, log=print):
    if not key:
        return []
    headers = {"Authorization": key, "User-Agent": UA}
    qs = list(queries)
    rng.shuffle(qs)
    cands, seen = [], set()
    for q in qs[:6]:
        page = rng.randint(1, 3)
        url = ("https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
            {"query": q, "orientation": "portrait", "size": "medium", "per_page": 15, "page": page}))
        try:
            data = _http_json(url, headers)
        except Exception as e:
            log(f"pexels: ошибка поиска '{q}': {e}")
            continue
        vids = data.get("videos", [])
        rng.shuffle(vids)
        cap = max(2, (n + 2) // 3)  # с одного запроса берём немного — так фон разнообразнее
        taken = 0
        for v in vids:
            if taken >= cap:
                break
            if v.get("id") in seen or (v.get("duration") or 0) < 4:
                continue
            f = _pick_file(v)
            if not f:
                continue
            seen.add(v["id"])
            cands.append((v["id"], f["link"]))
            taken += 1
        if len(cands) >= n + 2:
            break
    rng.shuffle(cands)
    return download_all(cands[:n], workdir, "px", log)


def download_all(cands, workdir, prefix, log=print):
    """cands: [(id, url)] -> скачанные пути."""
    os.makedirs(workdir, exist_ok=True)

    def dl(c):
        vid, link = c
        path = os.path.join(workdir, f"{prefix}_{vid}.mp4")
        try:
            req = urllib.request.Request(link, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as out:
                size = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 60 << 20:  # больше 60 МБ — не надо
                        raise ValueError("слишком большой файл")
                    out.write(chunk)
            return path
        except Exception as e:
            log(f"фон: не скачался {vid}: {e}")
            try:
                os.remove(path)
            except OSError:
                pass
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        paths = [p for p in ex.map(dl, cands) if p]
    return paths


# --- Pixabay ------------------------------------------------------------------
CACHE_DIR = os.environ.get("PIXABAY_CACHE", "pxcache")


def _pixabay_search(q, key, page, log=print):
    """Поиск с кэшем на 24 часа (так требуют правила Pixabay)."""
    params = {"key": key, "q": q, "per_page": 30, "page": page, "safesearch": "true", "video_type": "film"}
    h = hashlib.sha1(f"{q}|{page}".encode()).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, f"{h}.json")
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 24 * 3600:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(params)
    data = _http_json(url, {"User-Agent": UA})
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass
    return data


def _pixabay_file(hit):
    """Вертикальные ролики — в приоритете; размер не больше Full HD."""
    vids = hit.get("videos") or {}
    opts = []
    for k in ("medium", "large", "small"):
        v = vids.get(k) or {}
        if not v.get("url") or not v.get("width") or not v.get("height"):
            continue
        if (v.get("size") or 0) > 60 * 1024 * 1024:
            continue
        opts.append(v)
    if not opts:
        return None
    # лучший — где короткая сторона ближе к 1080
    opts.sort(key=lambda v: abs(min(v["width"], v["height"]) - 1080))
    return opts[0]


def pixabay_clips(queries, n, key, workdir, rng, log=print):
    if not key:
        return []
    qs = list(queries)
    rng.shuffle(qs)
    cap = max(2, (n + 2) // 3)  # с одного запроса берём немного — так фон разнообразнее
    portrait, land, seen = [], [], set()
    for q in qs[:6]:
        try:
            data = _pixabay_search(q, key, rng.randint(1, 2), log)
        except Exception as e:
            log(f"pixabay: ошибка поиска '{q}': {e}")
            continue
        hits = data.get("hits", [])
        rng.shuffle(hits)
        taken = 0
        for hit in hits:
            if taken >= cap:
                break
            if hit.get("id") in seen or (hit.get("duration") or 0) < 4:
                continue
            f = _pixabay_file(hit)
            if not f:
                continue
            seen.add(hit["id"])
            (portrait if f["height"] > f["width"] else land).append((hit["id"], f["url"]))
            taken += 1
        if len(portrait) + len(land) >= n + 2:
            break
    rng.shuffle(portrait)
    rng.shuffle(land)
    cands = (portrait + land)[:n]
    rng.shuffle(cands)
    return download_all(cands, workdir, "pb", log)


# --- запасной фон: плавные градиенты + зерно в цветах жанра ------------------
FALLBACK_COLORS = {
    "lyric": ["0x0b0f1a", "0x2b3442", "0x5b6b80", "0x1a1f2b"],
    "trap": ["0x1a1200", "0x6b4a00", "0xc9952b", "0x0d0a05"],
    "underground": ["0x1b1a17", "0x4a3f33", "0x7a6a55", "0x22201c"],
    "drill": ["0x02060f", "0x0a2a5a", "0x1b4f9c", "0x050a14"],
    "phonk": ["0x120000", "0x5a0000", "0xb01010", "0x1a0505"],
    "kavkaz": ["0x140a02", "0x5a2e08", "0xd98a2b", "0x1a0f05"],
}


def make_fallback_clips(genre, n, workdir, rng, seconds=9):
    """Генерирует n разных анимированных фонов (ffmpeg gradients)."""
    os.makedirs(workdir, exist_ok=True)
    cols = FALLBACK_COLORS.get(genre, FALLBACK_COLORS["lyric"])
    paths = []
    types = ["linear", "radial", "circular", "spiral"]
    for i in range(n):
        c = cols[:]
        rng.shuffle(c)
        t = types[i % len(types)]
        path = os.path.join(workdir, f"fb_{genre}_{i}.mp4")
        src = (f"gradients=s=540x960:r=30:d={seconds}:n=4:c0={c[0]}:c1={c[1]}:c2={c[2]}:c3={c[3]}"
               f":speed={0.008 + 0.01 * rng.random():.4f}:type={t}:seed={rng.randint(1, 10**6)}")
        vf = "noise=alls=10:allf=t,scale=1080:1920:flags=bicubic,format=yuv420p"
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-vf", vf,
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", path]
        try:
            subprocess.run(cmd, check=True, timeout=120)
            paths.append(path)
        except Exception:
            # совсем простой вариант
            cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                   f"color=c={c[1]}:s=1080x1920:r=30:d={seconds}", "-vf", "noise=alls=12:allf=t,format=yuv420p",
                   "-c:v", "libx264", "-preset", "ultrafast", path]
            subprocess.run(cmd, check=True, timeout=120)
            paths.append(path)
    return paths


def gather(genre, style, n_needed, workdir, rng, pexels_key=None, pixabay_key=None, log=print):
    """Собирает список роликов для монтажа. Возвращает [(path, duration)]."""
    clips = own_clips(genre)
    rng.shuffle(clips)
    own = len(clips)
    want = max(0, min(10, n_needed) - len(clips))
    if want and pixabay_key:
        pb = pixabay_clips(style["queries"], want, pixabay_key, workdir, rng, log=log)
        log(f"фон: с Pixabay {len(pb)}")
        clips += pb
    want = max(0, min(10, n_needed) - len(clips))
    if want and pexels_key:
        px = pexels_clips(style["queries"], want, pexels_key, workdir, rng, log=log)
        log(f"фон: с Pexels {len(px)}")
        clips += px
    if own:
        log(f"фон: своих роликов {own}")
    out = []
    for p in clips:
        d = probe_duration(p)
        if d >= 1.0:
            out.append((p, d))
    if not out:
        log("фон: роликов нет, делаю сгенерированный фон")
        for p in make_fallback_clips(genre, 3, workdir, rng):
            out.append((p, probe_duration(p)))
    return out

# ===========================================================================
# textfx
# ===========================================================================
W, H = 1080, 1920
FONT_DIR = os.environ.get("FONT_DIR", "fonts")

# ---------------------------------------------------------------------------
# Шрифты
# ---------------------------------------------------------------------------
_font_cache = {}


def ensure_fonts(log=print):
    os.makedirs(FONT_DIR, exist_ok=True)
    for key, f in FONTS.items():
        path = os.path.join(FONT_DIR, f["file"])
        if os.path.exists(path) and os.path.getsize(path) > 10000:
            continue
        try:
            req = urllib.request.Request(f["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            if len(data) > 10000:
                with open(path, "wb") as out:
                    out.write(data)
        except Exception as e:
            log(f"шрифт {key}: не скачался ({e}), будет запасной")


def _candidates(key):
    f = FONTS[key]
    c = [os.path.join(FONT_DIR, f["file"])] + list(f["fallback"]) + [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    return [p for p in c if os.path.exists(p)]


def _open(path, size, weight):
    font = ImageFont.truetype(path, size)
    if weight:
        try:
            axes = font.get_variation_axes()
            vals = []
            for ax in axes:
                name = ax.get("name", b"")
                name = name.decode() if isinstance(name, bytes) else str(name)
                if name.lower().startswith("weight") or name.lower() == "wght":
                    vals.append(max(ax["minimum"], min(ax["maximum"], weight)))
                else:
                    vals.append(ax.get("default", ax["minimum"]))
            if vals:
                font.set_variation_by_axes(vals)
        except Exception:
            pass
    return font


def _glyph(font, ch):
    """Картинка одного символа (для проверки, есть ли он в шрифте)."""
    try:
        bb = font.getbbox(ch)
    except Exception:
        return None
    w, h = max(1, bb[2] - bb[0]), max(1, bb[3] - bb[1])
    if bb[2] <= bb[0] or bb[3] <= bb[1]:
        return b""
    img = Image.new("L", (w + 4, h + 4), 0)
    ImageDraw.Draw(img).text((2 - bb[0], 2 - bb[1]), ch, font=font, fill=255)
    return img.tobytes() + bytes(str((w, h)), "ascii")


def _missing(font, text):
    notdef = {_glyph(font, "\U0010FFFD"), _glyph(font, "\uE0FF")}
    notdef.discard(None)
    miss = set()
    for ch in set(text):
        if ch.isspace():
            continue
        g = _glyph(font, ch)
        if g is None or g == b"" or g in notdef:
            miss.add(ch)
    return miss


def get_font(key, size, text=""):
    """Шрифт, в котором есть все буквы текста. Возвращает (font, cleaned_text)."""
    weight = FONTS[key].get("weight")
    best = None
    for path in _candidates(key):
        ck = (path, size)
        font = _font_cache.get(ck) or _open(path, size, weight)
        _font_cache[ck] = font
        miss = _missing(font, text)
        letters_missing = [c for c in miss if c.isalnum()]
        if not letters_missing:
            clean = "".join(c for c in text if c not in miss)
            return font, " ".join(clean.split())
        if best is None or len(miss) < best[1]:
            best = (font, len(miss), miss)
    font, _, miss = best
    clean = "".join(c for c in text if c not in miss)
    return font, " ".join(clean.split())


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------
def ease_out_cubic(p):
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


def ease_out_back(p, s=1.6):
    p = min(1.0, max(0.0, p)) - 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2


def ease_out_bounce(p):
    p = min(1.0, max(0.0, p))
    n1, d1 = 7.5625, 2.75
    if p < 1 / d1:
        return n1 * p * p
    if p < 2 / d1:
        p -= 1.5 / d1
        return n1 * p * p + 0.75
    if p < 2.5 / d1:
        p -= 2.25 / d1
        return n1 * p * p + 0.9375
    p -= 2.625 / d1
    return n1 * p * p + 0.984375


_alpha_cache = {}


def with_alpha(img, a, cache=True):
    """Копия RGBA с умноженной прозрачностью (с кэшем по 24 уровням)."""
    if a >= 0.999:
        return img
    a = max(0.0, a)
    q = int(a * 24 + 0.5)
    key = (id(img), q)
    if cache and key in _alpha_cache:
        return _alpha_cache[key][1]
    r, g, b, al = img.split()
    al = al.point(lambda v: int(v * q / 24))
    out = Image.merge("RGBA", (r, g, b, al))
    if cache and img.width * img.height < 400_000:
        _alpha_cache[key] = (img, out)  # держим ссылку на img, чтобы id не переиспользовался
    return out


def paste(base, img, x, y, alpha=1.0):
    if img is None or alpha <= 0.003:
        return
    if alpha < 0.999:
        img = with_alpha(img, alpha)
    base.paste(img, (int(round(x)), int(round(y))), img)


GOLD_STOPS = [(0.0, (110, 72, 14)), (0.30, (255, 238, 165)), (0.48, (222, 168, 52)),
              (0.62, (150, 98, 18)), (0.80, (250, 214, 112)), (1.0, (118, 78, 16))]


def gold_rows(t):
    t = np.clip(t, 0, 1)
    xs = [s[0] for s in GOLD_STOPS]
    out = np.zeros((len(t), 3), np.float32)
    for c in range(3):
        out[:, c] = np.interp(t, xs, [s[1][c] for s in GOLD_STOPS])
    return out


def stylize(fill_mask, full_mask, row_t, st):
    """Маски (L) -> (main RGBA, glow RGBA или None)."""
    fm = np.asarray(fill_mask, np.float32) / 255.0
    am = np.asarray(full_mask, np.float32) / 255.0
    h, w = fm.shape
    # заливка
    if st.get("fill") == "gold":
        rows = gold_rows(row_t)
        fill = np.broadcast_to(rows[:, None, :], (h, w, 3))
    else:
        fill = np.broadcast_to(np.array(st.get("fill", (255, 255, 255)), np.float32), (h, w, 3))
    stroke_col = np.array(st.get("stroke_fill", (0, 0, 0)), np.float32)
    # обводка = полная маска минус заливка
    rgb = fill * fm[..., None] + stroke_col * np.clip(am - fm, 0, 1)[..., None]
    rgb = rgb / np.maximum(am, 1e-4)[..., None]
    alpha = am.copy()
    # тень
    sh = float(st.get("shadow", 0) or 0)
    if sh > 0:
        sm = full_mask.filter(ImageFilter.GaussianBlur(7))
        s = np.asarray(sm, np.float32) / 255.0
        s = np.roll(s, 6, axis=0) * sh
        # склеиваем: текст поверх тени
        out_a = alpha + s * (1 - alpha)
        rgb = (rgb * alpha[..., None]) / np.maximum(out_a, 1e-4)[..., None]
        alpha = out_a
    main = np.dstack([np.clip(rgb, 0, 255), np.clip(alpha * 255, 0, 255)]).astype(np.uint8)
    main_img = Image.fromarray(main, "RGBA")
    glow_img = None
    if st.get("glow"):
        r = int(st.get("glow_r", 14))
        g = full_mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(r))
        ga = np.clip(np.asarray(g, np.float32) / 255.0 * 1.8 * float(st.get("glow_a", 0.5)), 0, 1)
        col = np.broadcast_to(np.array(st["glow"], np.float32), (h, w, 3))
        glow = np.dstack([col, ga * 255]).astype(np.uint8)
        glow_img = Image.fromarray(glow, "RGBA")
    return main_img, glow_img


def glue(text):
    """Тире и короткие предлоги не остаются в начале/конце строки."""
    import re
    text = " ".join(text.split())
    text = re.sub(r" ([—–-]) ", "\u00a0\\1 ", text)
    text = re.sub(r"(^|\s)([а-яА-ЯёЁa-zA-Z]{1,2}) ", lambda m: m.group(1) + m.group(2) + "\u00a0", text)
    return text


def wrap(text, font, max_w):
    words = [w for w in text.split(" ") if w]
    lines, cur = [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if font.getlength(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def balance(lines, font, max_w):
    """Делает строки примерно одинаковыми по длине (красивее по центру)."""
    if len(lines) < 2:
        return lines
    words = [w for w in " ".join(lines).split(" ") if w]
    n = len(lines)
    best, best_score = lines, 1e18
    # простой перебор разбиений для коротких текстов
    if len(words) <= 16:
        import itertools
        for cuts in itertools.combinations(range(1, len(words)), n - 1):
            parts, prev = [], 0
            for c in list(cuts) + [len(words)]:
                parts.append(" ".join(words[prev:c]))
                prev = c
            ws = [font.getlength(p) for p in parts]
            if max(ws) > max_w:
                continue
            score = np.var(ws) + 0.0
            if score < best_score:
                best, best_score = parts, score
    return best


# ---------------------------------------------------------------------------
# Блок текста: целиком и по буквам
# ---------------------------------------------------------------------------
class TextBlock:
    def __init__(self, text, st, center=(540, 900), max_w=800, max_lines=4):
        self.st = st
        if st.get("upper"):
            text = text.upper()
        size = int(st.get("size", 96))
        while True:
            font, clean = get_font(st["font"], size, text)
            clean = glue(clean)
            lines = wrap(clean, font, max_w)
            if (len(lines) <= max_lines and all(font.getlength(l) <= max_w for l in lines)) or size <= 44:
                break
            size -= 4
        lines = [l.replace("\u00a0", " ") for l in balance(lines, font, max_w)]
        clean = clean.replace("\u00a0", " ")
        self.font, self.size, self.lines, self.text = font, size, lines, clean
        asc, desc = font.getmetrics()
        self.asc, self.desc = asc, desc
        self.cell_h = asc + desc
        self.line_h = int(size * (1.22 if st["font"] == "script" else 1.12))
        stroke = int(st.get("stroke", 0) or 0)
        self.stroke = stroke
        self.pad = pad = int(st.get("glow_r", 10) * 2.4 + stroke + 14)
        lw = [font.getlength(l) for l in lines]
        self.over = int(size * 0.35)
        bw = int(max(lw) + 2 * pad + 2 * self.over)
        bh = int((len(lines) - 1) * self.line_h + self.cell_h + 2 * pad)
        self.bw, self.bh = bw, bh
        self.left = center[0] - bw / 2
        self.top = center[1] - bh / 2
        self.cx, self.cy = center

        fill_m = Image.new("L", (bw, bh), 0)
        full_m = Image.new("L", (bw, bh), 0)
        df, da = ImageDraw.Draw(fill_m), ImageDraw.Draw(full_m)
        self.chars = []
        widx = 0
        for j, line in enumerate(lines):
            shift = 0 if st.get("align") == "left" else (max(lw) - lw[j]) / 2
            x0 = pad + self.over + shift
            base = pad + asc + j * self.line_h
            df.text((x0, base), line, font=font, fill=255, anchor="ls")
            da.text((x0, base), line, font=font, fill=255, anchor="ls", stroke_width=stroke, stroke_fill=255)
            for i, ch in enumerate(line):
                if ch == " ":
                    widx += 1
                    continue
                xi = x0 + font.getlength(line[:i])
                self.chars.append({"ch": ch, "x": xi, "line": j, "word": widx,
                                   "adv": font.getlength(ch)})
            widx += 1
        rows = np.arange(bh, dtype=np.float32) - pad
        jj = np.clip(np.floor(rows / self.line_h), 0, len(lines) - 1)
        row_t = (rows - jj * self.line_h) / self.cell_h
        self.fill_mask = fill_m
        self.main, self.glow = stylize(fill_m, full_m, row_t, st)
        self.fill_arr = np.asarray(fill_m, np.float32) / 255.0
        self.n_words = widx

    def build_chars(self):
        """Спрайты каждой буквы (для побуквенной анимации)."""
        st, font, pad, asc = self.st, self.font, self.pad, self.asc
        for c in self.chars:
            cw = int(c["adv"] + 2 * pad + 2 * self.over)
            ch = int(self.cell_h + 2 * pad)
            fm = Image.new("L", (cw, ch), 0)
            am = Image.new("L", (cw, ch), 0)
            ImageDraw.Draw(fm).text((pad + self.over, pad + asc), c["ch"], font=font, fill=255, anchor="ls")
            ImageDraw.Draw(am).text((pad + self.over, pad + asc), c["ch"], font=font, fill=255, anchor="ls",
                                    stroke_width=self.stroke, stroke_fill=255)
            row_t = (np.arange(ch, dtype=np.float32) - pad) / self.cell_h
            c["main"], c["glow"] = stylize(fm, am, row_t, st)
            # позиция спрайта на кадре (левый верх)
            c["sx"] = self.left + c["x"] - pad - self.over
            c["sy"] = self.top + c["line"] * self.line_h
            bb = fm.getbbox() or (0, 0, cw, ch)
            c["ink_bottom"] = c["sy"] + bb[3]
            c["ink_l"] = c["sx"] + bb[0]
            c["ink_r"] = c["sx"] + bb[2]
            c["mono"] = fm

    # трансформированный блок (масштаб/поворот) вокруг центра
    def transformed(self, img, scale=1.0, rot=0.0):
        if img is None:
            return None, 0, 0
        out = img
        if abs(scale - 1) > 0.004:
            out = out.resize((max(1, int(self.bw * scale)), max(1, int(self.bh * scale))), Image.BILINEAR)
        if abs(rot) > 0.15:
            out = out.rotate(rot, resample=Image.BILINEAR, expand=True)
        return out, out.width, out.height

    def combined(self, glow_alpha, main=None):
        """Свечение + текст в одной картинке (готовые уровни свечения кэшируются)."""
        if self.glow is None:
            return main if main is not None else self.main
        if main is not None:
            return Image.alpha_composite(with_alpha(self.glow, glow_alpha, cache=False), main)
        lvl = round(max(0.0, min(1.0, glow_alpha)) * 8) / 8
        if not hasattr(self, "_comb"):
            self._comb = {}
        if lvl not in self._comb:
            self._comb[lvl] = Image.alpha_composite(with_alpha(self.glow, lvl, cache=False), self.main)
        return self._comb[lvl]

    def draw_block(self, base, scale=1.0, rot=0.0, dx=0, dy=0, alpha=1.0, glow_alpha=1.0, main=None):
        img = self.combined(glow_alpha, main)
        m, mw, mh = self.transformed(img, scale, rot)
        if alpha < 0.999:
            m = with_alpha(m, alpha, cache=False)
        paste(base, m, self.cx + dx - mw / 2, self.cy + dy - mh / 2)


def simple_text(text, key, size, fill=(255, 255, 255), shadow=0.7, spacing=0):
    font, clean = get_font(key, size, text)
    if spacing:
        w = sum(font.getlength(c) for c in clean) + spacing * (len(clean) - 1)
    else:
        w = font.getlength(clean)
    asc, desc = font.getmetrics()
    pad = 14
    img_m = Image.new("L", (int(w + 2 * pad), asc + desc + 2 * pad), 0)
    d = ImageDraw.Draw(img_m)
    if spacing:
        x = pad
        for c in clean:
            d.text((x, pad + asc), c, font=font, fill=255, anchor="ls")
            x += font.getlength(c) + spacing
    else:
        d.text((pad, pad + asc), clean, font=font, fill=255, anchor="ls")
    st = {"fill": fill, "shadow": shadow}
    main, _ = stylize(img_m, img_m, np.zeros(img_m.height), st)
    return main


def pill(text, key, size, fg=(255, 255, 255), bg=(0, 0, 0, 150)):
    t = simple_text(text, key, size, fill=fg, shadow=0, spacing=3)
    pw, ph = t.width + 36, t.height + 6
    img = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, pw - 1, ph - 1), radius=ph // 2, fill=bg)
    img.alpha_composite(t, (18, 3))
    return img


def soft_dot(r, color, hardness=1.6):
    s = int(r * 2 + 4)
    yy, xx = np.mgrid[0:s, 0:s]
    d = np.sqrt((xx - s / 2) ** 2 + (yy - s / 2) ** 2) / r
    a = np.clip(1 - d, 0, 1) ** hardness
    arr = np.dstack([np.full((s, s), color[0]), np.full((s, s), color[1]), np.full((s, s), color[2]), a * 255])
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def star(r, color):
    s = int(r * 2 + 4)
    yy, xx = np.mgrid[0:s, 0:s]
    x = (xx - s / 2) / r
    y = (yy - s / 2) / r
    a = np.exp(-(np.abs(x) * 9) ** 1.2) * np.clip(1 - np.abs(y), 0, 1) + \
        np.exp(-(np.abs(y) * 9) ** 1.2) * np.clip(1 - np.abs(x), 0, 1)
    a += np.exp(-(x * x + y * y) * 18) * 0.8
    a = np.clip(a, 0, 1)
    arr = np.dstack([np.full((s, s), color[0]), np.full((s, s), color[1]), np.full((s, s), color[2]), a * 255])
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


# ---------------------------------------------------------------------------
# Сцена: главный текст + шапка + призыв
# ---------------------------------------------------------------------------
class TextScene:
    def __init__(self, message, genre, env, fps, header=None, producer=None, seed=0, cta=None, cuts=None):
        self.genre = genre
        self.style = GENRES[genre]
        self.st = self.style["text"]
        self.anim = self.st["anim"]
        self.env = env
        self.fps = fps
        self.n = env["n"]
        self.T = self.n / fps
        self.rng = random.Random(seed)
        self.bl = env["beat_len"]
        self.block = TextBlock(message, self.st, center=(540, 905))
        if self.anim in ("rain", "vhs", "glow"):
            self.block.build_chars()
        # шапка и призыв
        self.header = None
        if header:
            self.header = simple_text(header, "ui", 34, fill=(255, 255, 255), shadow=0.8, spacing=4)
        self.prod = simple_text(producer, "ui", 30, fill=(235, 235, 235), shadow=0.8, spacing=2) if producer else None
        cta = CTA_TEXT if cta is None else cta
        self.cta = pill(cta, "ui", 32) if cta else None
        self.cuts = cuts or []
        self._setup()

    # ---- подготовка эффектов -------------------------------------------
    def _setup(self):
        r = self.rng
        a = self.anim
        if a == "rain":
            self.streaks = [(r.uniform(-100, W + 100), r.uniform(0, H + 300), r.uniform(1500, 2400),
                             r.uniform(35, 110), r.randint(22, 70), r.choice((1, 1, 2))) for _ in range(120)]
            self.drop_order = list(range(len(self.block.chars)))
            self.drop_start = {}
            for k, i in enumerate(self.drop_order):
                self.drop_start[i] = 0.12 + 0.045 * k + r.uniform(0, 0.12)
            self.drips = []
            land = max(self.drop_start.values()) + 0.8 if self.drop_start else 1.0
            ink = [i for i, c in enumerate(self.block.chars) if c["ch"].isalpha()]
            for bt in self.env["beats"]:
                if bt < land or not ink:
                    continue
                for _ in range(1 if r.random() < 0.75 else 2):
                    if r.random() < 0.8:
                        ci = r.choice(ink)
                        c = self.block.chars[ci]
                        x = r.uniform(c["ink_l"] + 4, max(c["ink_l"] + 5, c["ink_r"] - 4))
                        self.drips.append({"t0": bt + r.uniform(0, 0.12), "x": x, "y": c["ink_bottom"] - 3,
                                           "r": r.uniform(4.5, 7.5), "g": r.uniform(700, 1100)})
            self.phase = [r.uniform(0, 6.28) for _ in self.block.chars]
        elif a == "gold":
            bx0, bx1 = self.block.left + self.block.pad, self.block.left + self.block.bw - self.block.pad
            by0, by1 = self.block.top + self.block.pad, self.block.top + self.block.bh - self.block.pad
            self.stars = []
            for _ in range(18):
                ang = r.uniform(0, 6.28)
                rx = (bx1 - bx0) / 2 + r.uniform(10, 90)
                ry = (by1 - by0) / 2 + r.uniform(10, 80)
                self.stars.append({"x": (bx0 + bx1) / 2 + math.cos(ang) * rx, "y": (by0 + by1) / 2 + math.sin(ang) * ry,
                                   "f": r.uniform(0.25, 0.7), "ph": r.uniform(0, 6.28), "s": r.choice((0, 1, 1, 2))})
            self.star_spr = [star(9, (255, 240, 200)), star(14, (255, 230, 170)), star(22, (255, 245, 215))]
            h, w = self.block.bh, self.block.bw
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            self.diag = xx + yy * 0.45
            self.sweeps = [d for k, d in enumerate(self.env["downbeats"]) if k % 2 == 1]
        elif a == "vhs":
            n = len(self.block.chars)
            self.t_type0 = 0.2
            self.dt_char = max(0.035, min(self.bl / 4, 0.42 * self.T / max(1, n)))
            self.red = []
            self.cyan = []
            for c in self.block.chars:
                m = c["main"]
                al = m.getchannel("A")
                self.red.append(Image.merge("RGBA", (Image.new("L", m.size, 255), Image.new("L", m.size, 30),
                                                    Image.new("L", m.size, 40), al.point(lambda v: v * 0.55))))
                self.cyan.append(Image.merge("RGBA", (Image.new("L", m.size, 20), Image.new("L", m.size, 220),
                                                     Image.new("L", m.size, 255), al.point(lambda v: v * 0.55))))
            cur = Image.new("RGBA", (int(self.block.size * 0.55), int(self.block.size * 0.8)), (242, 238, 222, 235))
            self.cursor = cur
            self.osd_play = simple_text("PLAY ►", "mono", 46, fill=(240, 240, 240), shadow=1.0)
            self.osd_sp = simple_text("SP", "mono", 46, fill=(240, 240, 240), shadow=1.0)
            import datetime
            d = datetime.date.today()
            mon = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"][d.month - 1]
            self.osd_date = simple_text(f"{mon}. {d.day:02d} 1997", "mono", 42, fill=(240, 240, 240), shadow=1.0)
            self.tc_cache = {}
        elif a == "glitch":
            self.flicker = [r.random() for _ in range(self.n)]
        elif a == "shake":
            self.kick_sign = 1
        elif a == "glow":
            nw = max(1, self.block.n_words)
            step = self.bl / 2 if nw > 6 else self.bl
            step = min(step, 0.38 * self.T / nw)
            self.word_t = [0.1 + k * step for k in range(nw)]
            self.bokeh = []
            sprites = [soft_dot(rr, col, 1.3) for rr, col in
                       ((14, (255, 190, 110)), (24, (255, 170, 80)), (36, (255, 215, 160)), (54, (255, 150, 60)))]
            self.bokeh_spr = sprites
            for _ in range(22):
                self.bokeh.append({"x": r.uniform(0, W), "y": r.uniform(0, H), "v": r.uniform(18, 60),
                                   "sw": r.uniform(10, 40), "f": r.uniform(0.1, 0.35), "ph": r.uniform(0, 6.28),
                                   "a": r.uniform(0.12, 0.4), "s": r.randint(0, 3)})

    # ---- общий слой: шапка, призыв ----------------------------------------
    def _chrome(self, base, i, t):
        fade = ease_out_cubic((t - 0.6) / 0.6)
        if self.header is not None:
            paste(base, self.header, 540 - self.header.width / 2, 300, 0.85 * fade)
        if self.prod is not None:
            paste(base, self.prod, 540 - self.prod.width / 2, 348, 0.75 * fade)
        if self.cta is not None:
            f2 = ease_out_cubic((t - 1.4) / 0.5)
            if f2 > 0:
                s = 1 + 0.06 * self.env["bar"][i]
                c = self.cta
                if s > 1.003:
                    c = c.resize((int(c.width * s), int(c.height * s)), Image.BILINEAR)
                paste(base, c, 540 - c.width / 2, 1300 - c.height / 2, f2)

    # ---- главная функция: рисует всё на кадре -----------------------------
    def render(self, base, i):
        t = i / self.fps
        getattr(self, "_" + self.anim)(base, i, t)
        self._chrome(base, i, t)

    # ---- ЛИРИКА: буквы падают каплями, с них стекают капли, идёт дождь -----
    def _rain(self, base, i, t):
        d = ImageDraw.Draw(base, "RGBA")
        for (x0, y0, v, ln, al, wd) in self.streaks:
            y = (y0 + v * t) % (H + 300) - 150
            x = x0 + (y - y0) * 0.0 - y * 0.08 + 80
            d.line((x, y, x - ln * 0.08, y + ln), fill=(220, 230, 255, al), width=wd)
        bar = self.env["bar"][i]
        ga = 0.55 + 0.45 * bar
        for k, c in enumerate(self.block.chars):
            t0 = self.drop_start[k]
            p = (t - t0) / 0.75
            if p <= 0:
                continue
            fall = 1 - ease_out_bounce(p)
            y = c["sy"] - 300 * fall + 2.2 * math.sin(6.283 * 0.38 * t + self.phase[k]) * min(1, max(0, p - 1))
            x = c["sx"]
            a = min(1.0, p * 2.2)
            paste(base, c["glow"], x, y, a * ga)
            paste(base, c["main"], x, y, a)
        # капли, стекающие с букв
        for dr in self.drips:
            dt = t - dr["t0"]
            if dt < 0 or dt > 2.2:
                continue
            hang = 0.45
            if dt < hang:
                rr = dr["r"] * (0.3 + 0.7 * dt / hang)
                cy = dr["y"] + rr * 0.6
                d.ellipse((dr["x"] - rr * 0.8, cy - rr, dr["x"] + rr * 0.8, cy + rr * 1.15), fill=(230, 238, 255, 190))
            else:
                ft = dt - hang
                yy = dr["y"] + 0.5 * dr["g"] * ft * ft + 8
                if yy > H:
                    continue
                fade = max(0.0, 1 - ft / 1.7)
                # след
                seg = 6
                for s in range(seg):
                    ya = dr["y"] + (yy - dr["y"]) * s / seg
                    yb = dr["y"] + (yy - dr["y"]) * (s + 1) / seg
                    d.line((dr["x"], ya, dr["x"], yb), fill=(225, 235, 255, int(55 * fade * (s + 1) / seg)), width=2)
                rr = dr["r"]
                d.ellipse((dr["x"] - rr * 0.75, yy - rr * 1.5, dr["x"] + rr * 0.75, yy + rr * 0.9),
                          fill=(232, 240, 255, int(200 * fade)))
                d.ellipse((dr["x"] - rr * 0.3, yy - rr * 0.9, dr["x"] + rr * 0.05, yy - rr * 0.4),
                          fill=(255, 255, 255, int(220 * fade)))

    # ---- ТРЭП: золото, удар на бочку, блик, искры --------------------------
    def _gold(self, base, i, t):
        k = self.env["kick"][i]
        # искры
        for s in self.stars:
            a = max(0.0, math.sin(6.283 * s["f"] * t + s["ph"])) ** 5
            a = a * ease_out_cubic((t - 0.4) / 0.5)
            if a > 0.03:
                spr = self.star_spr[s["s"]]
                paste(base, spr, s["x"] - spr.width / 2, s["y"] - spr.height / 2, a)
        # влёт
        p = t / 0.3
        intro = 1 + 1.4 * (1 - ease_out_cubic(p))
        alpha = min(1.0, t / 0.12)
        scale = intro * (1 + 0.075 * k)
        rr = random.Random(i // 2)
        rot = (rr.random() - 0.5) * 2.4 * k
        dx = (rr.random() - 0.5) * 10 * k
        dy = (rr.random() - 0.5) * 10 * k
        main = self.block.main
        # блик золота
        for sw in self.sweeps:
            q = (t - sw) / 0.6
            if 0 <= q <= 1:
                pos = -200 + (self.block.bw + 500) * ease_out_cubic(q)
                band = np.exp(-((self.diag - pos) / 38.0) ** 2)
                arr = np.asarray(main).astype(np.float32)
                add = band * self.block.fill_arr * 210
                arr[..., :3] = np.clip(arr[..., :3] + add[..., None], 0, 255)
                main = Image.fromarray(arr.astype(np.uint8), "RGBA")
                break
        self.block.draw_block(base, scale=scale, rot=rot, dx=dx, dy=dy, alpha=alpha,
                              glow_alpha=0.6 + 0.4 * k, main=main)

    # ---- АНДЕГРАУНД: печатная машинка, VHS-экран --------------------------
    def _vhs(self, base, i, t):
        n_typed = int((t - self.t_type0) / self.dt_char) + 1 if t >= self.t_type0 else 0
        typing = n_typed < len(self.block.chars)
        beat = self.env["beat"][i]
        rr = random.Random(i)
        jit = {}
        for j in range(len(self.block.lines)):
            jit[j] = (rr.uniform(-4, 4) * (beat > 0.55)) + rr.uniform(-1, 1)
        last = None
        for k, c in enumerate(self.block.chars[:n_typed]):
            x = c["sx"] + jit[c["line"]]
            y = c["sy"]
            paste(base, self.red[k], x - 4, y)
            paste(base, self.cyan[k], x + 4, y)
            paste(base, c["main"], x, y)
            last = c
        # курсор
        show = typing or ((t % self.bl) < self.bl / 2)
        if show:
            if last is None:
                c0 = self.block.chars[0] if self.block.chars else None
                cx = (c0["sx"] + self.block.pad + self.block.over) if c0 else 540
                cy = (c0["sy"] + self.block.pad) if c0 else 900
            else:
                cx = last["sx"] + self.block.pad + self.block.over + last["adv"] + 6 + jit[last["line"]]
                cy = last["sy"] + self.block.pad + self.block.asc - self.cursor.height
            paste(base, self.cursor, cx, cy)
        # экранное меню видеомагнитофона
        if (t % 1.0) < 0.75 or t > 3:
            paste(base, self.osd_play, 60, 215)
        paste(base, self.osd_sp, W - 60 - self.osd_sp.width, 215)
        paste(base, self.osd_date, 60, 1385)
        sec = int(t) + 12
        key = sec
        if key not in self.tc_cache:
            self.tc_cache[key] = simple_text(f"0:{sec // 60:02d}:{sec % 60:02d}", "mono", 42, fill=(240, 240, 240), shadow=1.0)
        tc = self.tc_cache[key]
        paste(base, tc, W - 60 - tc.width, 1385)

    # ---- ДРИЛЛ: глитч на бочку, холодное свечение -------------------------
    def _glitch(self, base, i, t):
        k = self.env["kick"][i]
        # появление миганием
        if t < 0.5:
            if self.flicker[i] > t * 2.2:
                return
        alpha = 1.0
        main = self.block.main
        if k > 0.5:
            g = (k - 0.5) * 2
            rr = random.Random(i * 7 + 3)
            arr = np.asarray(main).copy()
            h = arr.shape[0]
            nb = rr.randint(5, 9)
            edges = sorted(rr.sample(range(8, h - 8), nb))
            prev = 0
            for e in edges + [h]:
                sh = int(rr.uniform(-1, 1) * 46 * g)
                arr[prev:e] = np.roll(arr[prev:e], sh, axis=1)
                prev = e
            s = int(14 * g)
            if s > 0:
                out = arr.copy()
                out[:, :, 0] = np.roll(arr[:, :, 0], -s, axis=1)
                out[:, :, 2] = np.roll(arr[:, :, 2], s, axis=1)
                out[:, :, 3] = np.maximum(np.maximum(np.roll(arr[:, :, 3], -s, axis=1), np.roll(arr[:, :, 3], s, axis=1)), arr[:, :, 3])
                arr = out
            main = Image.fromarray(arr, "RGBA")
        fl = 0.8 + 0.2 * self.flicker[i]
        self.block.draw_block(base, scale=1 + 0.045 * k, alpha=alpha, glow_alpha=fl * (0.7 + 0.3 * k), main=main)

    # ---- ФОНК: тряска, красное свечение, эхо ------------------------------
    def _shake(self, base, i, t):
        k = self.env["kick"][i]
        rms = self.env["rms"][i]
        p = t / 0.24
        intro_s = 1 + 2.0 * (1 - ease_out_cubic(p))
        intro_r = -14 * (1 - ease_out_cubic(p))
        rr = random.Random(i)
        amp = 24 * k + 3 * rms
        dx = rr.uniform(-1, 1) * amp
        dy = rr.uniform(-1, 1) * amp
        rot = intro_r + rr.uniform(-1, 1) * 4.5 * k
        scale = intro_s * (1 + 0.14 * k)
        if k > 0.75:
            # красное эхо
            g, gw, gh = self.block.transformed(self.block.glow, scale * 1.08, rot)
            paste(base, g, 540 + dx * 2 - gw / 2, 905 + dy * 2 - gh / 2, 0.8)
        self.block.draw_block(base, scale=scale, rot=rot, dx=dx, dy=dy, alpha=min(1, t / 0.1),
                              glow_alpha=0.55 + 0.45 * k)

    # ---- КАВКАЗ: слова всплывают на долях, тёплое дыхание, боке -----------
    def _glow(self, base, i, t):
        for b in self.bokeh:
            y = (b["y"] - b["v"] * t) % (H + 160) - 80
            x = b["x"] + math.sin(6.283 * b["f"] * t + b["ph"]) * b["sw"]
            a = b["a"] * (0.6 + 0.4 * math.sin(6.283 * b["f"] * 1.7 * t + b["ph"])) * min(1, t / 0.8)
            spr = self.bokeh_spr[b["s"]]
            paste(base, spr, x - spr.width / 2, y - spr.height / 2, a)
        bp = self.env["beat"][i]
        breath = 0.55 + 0.45 * bp
        fl = 7 * math.sin(6.283 * t / (self.bl * 8))
        for c in self.block.chars:
            t0 = self.word_t[min(c["word"], len(self.word_t) - 1)]
            p = (t - t0) / 0.45
            if p <= 0:
                continue
            e = ease_out_cubic(p)
            y = c["sy"] + 34 * (1 - e) + fl
            paste(base, c["glow"], c["sx"], y, e * breath)
            paste(base, c["main"], c["sx"], y, e)

# ===========================================================================
# render
# ===========================================================================
W, H, FPS = 1080, 1920, 30


def log_default(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------
# 1. Фон: склейка роликов по долям + цветокор (ffmpeg -> сырые кадры в pipe)
# ---------------------------------------------------------------------------
def make_scrim(path, strength=0.4):
    """Мягкое затемнение за текстом (чтобы читался на светлых кадрах)."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    a = strength * np.exp(-((yy - 905) / 430.0) ** 2 - ((xx - 540) / 760.0) ** 2)
    arr = np.zeros((H, W, 4), np.uint8)
    arr[..., 3] = np.clip(a * 255, 0, 255).astype(np.uint8)
    Image.fromarray(arr, "RGBA").save(path)
    return path


def build_bg_command(segments, style, grade, n_frames, scrim=None):
    """segments: [(path, clip_dur, n_frames_visible, extra_frames)]"""
    sp = float(style.get("speed", 1.0))
    xf = float(style.get("xfade", 0) or 0)
    fade_frames = int(round(xf * FPS / 2)) * 2 if xf > 0 and len(segments) > 1 else 0
    half = fade_frames // 2
    args = ["ffmpeg", "-v", "error", "-y"]
    filters = []
    rng = random.Random(len(segments) * 31 + n_frames)
    lens = []
    for k, (path, cdur, nf, _) in enumerate(segments):
        if fade_frames:
            if k == 0:
                L = nf + half
            elif k == len(segments) - 1:
                L = nf + half
            else:
                L = nf + fade_frames
        else:
            L = nf
        lens.append(L)
        need = L / FPS * sp + 0.25
        if cdur > need + 0.3:
            off = rng.uniform(0.1, cdur - need - 0.1)
            args += ["-ss", f"{off:.3f}", "-t", f"{need:.3f}", "-i", path]
        else:
            args += ["-stream_loop", "-1", "-t", f"{need:.3f}", "-i", path]
        filters.append(
            f"[{k}:v]setpts=(PTS-STARTPTS)/{sp:.4f},fps={FPS},"
            f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=bicubic,crop={W}:{H},setsar=1,"
            f"format=yuv420p,trim=end_frame={L},setpts=PTS-STARTPTS[v{k}]")
    if len(segments) == 1:
        filters.append("[v0]null[vc]")
    elif not fade_frames:
        filters.append("".join(f"[v{k}]" for k in range(len(segments))) + f"concat=n={len(segments)}:v=1:a=0[vc]")
    else:
        types = style.get("xfade_types") or ["fade"]
        acc = "v0"
        pos = 0  # кадр начала текущего сегмента в выходе
        starts = []
        cur = 0
        for k, (_, _, nf, _) in enumerate(segments):
            starts.append(cur)
            cur += nf
        for k in range(1, len(segments)):
            off = (starts[k] - half) / FPS
            out = f"x{k}" if k < len(segments) - 1 else "vc"
            tp = types[rng.randrange(len(types))]
            filters.append(f"[{acc}][v{k}]xfade=transition={tp}:duration={fade_frames / FPS:.4f}:offset={off:.4f}[{out}]")
            acc = out
    if scrim:
        args += ["-loop", "1", "-framerate", str(FPS), "-i", scrim]
        filters.append(f"[vc]{grade},scale={W}:{H}[g];[g][{len(segments)}:v]overlay=0:0:shortest=1,format=rgb24[out]")
    else:
        filters.append(f"[vc]{grade},scale={W}:{H},format=rgb24[out]")
    args += ["-filter_complex", ";".join(filters), "-map", "[out]", "-frames:v", str(n_frames),
             "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    return args


# ---------------------------------------------------------------------------
# 2. Эффекты фона на бочку / такт (покадрово, OpenCV)
# ---------------------------------------------------------------------------
class BgFX:
    def __init__(self, style, env, cut_frames, n, seed):
        self.fx = style.get("fx", {})
        self.env = env
        self.n = n
        self.cuts = cut_frames
        self.rng = random.Random(seed)
        c = self.fx.get("flash_color", (255, 255, 255))
        self.flash_img = np.empty((H, W, 3), np.uint8)
        self.flash_img[:] = c
        # моменты инверсии (фонк) — каждый 4-й такт, кроме первого
        self.invert_frames = set()
        if self.fx.get("invert"):
            for k, d in enumerate(env["downbeats"]):
                if k > 0 and k % 4 == 0:
                    f0 = int(round(d * FPS))
                    self.invert_frames.update({f0, f0 + 1})
        # срывы «трекинга» VHS
        self.track = []
        if self.fx.get("tracking"):
            for k, d in enumerate(env["downbeats"]):
                if k % 2 == 1:
                    f0 = int(round((d + self.rng.uniform(0, env["beat_len"] * 3)) * FPS))
                    self.track.append((f0, self.rng.randint(4, 7), self.rng.randint(300, 1500), self.rng.randint(60, 220)))
        self.seg_of = np.zeros(n, int)
        self.seg_pos = np.zeros(n)
        bounds = list(cut_frames) + [n]
        for s in range(len(bounds) - 1):
            a, b = bounds[s], bounds[s + 1]
            self.seg_of[a:b] = s
            self.seg_pos[a:b] = (np.arange(a, b) - a) / max(1, b - a)
        self.kb_dir = [self.rng.choice((-1, 1)) for _ in bounds]

    def apply(self, frame, i):
        fx = self.fx
        env = self.env
        k = env["kick"][i]
        bar = env["bar"][i]
        out = frame
        # зум-панч + тряска + поворот + медленный наезд внутри кадра
        zoom = float(fx.get("zoom", 0))
        shake = float(fx.get("shake", 0))
        rot = float(fx.get("rot", 0))
        pos = self.seg_pos[i]
        kb = 0.035 * pos if fx.get("zoom", 0) < 0.05 else 0.02 * pos
        base = 1.0 + kb + (0.03 if shake or rot else 0)
        s = base + zoom * k
        rr = random.Random(i * 13 + 1)
        dx = (rr.random() - 0.5) * 2 * shake * k
        dy = (rr.random() - 0.5) * 2 * shake * k
        ang = (rr.random() - 0.5) * 2 * rot * k
        if abs(s - 1) > 0.002 or abs(dx) > 0.5 or abs(dy) > 0.5 or abs(ang) > 0.05:
            M = cv2.getRotationMatrix2D((W / 2, H / 2), ang, s)
            M[0, 2] += dx
            M[1, 2] += dy
            out = cv2.warpAffine(out, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        # RGB-расслоение
        rs = int(float(fx.get("rgb_split", 0)) * k)
        if rs >= 2:
            o = out.copy()
            o[:, :-rs, 0] = out[:, rs:, 0]
            o[:, rs:, 2] = out[:, :-rs, 2]
            out = o
        # глитч-полосы (дрилл)
        if fx.get("glitch") and k > 0.6:
            o = out if out is not frame else out.copy()
            nb = rr.randint(2, 5)
            for _ in range(nb):
                y0 = rr.randint(0, H - 40)
                h = rr.randint(16, 110)
                sh = int(rr.uniform(-1, 1) * 70 * k)
                o[y0:y0 + h] = np.roll(o[y0:y0 + h], sh, axis=1)
            out = o
        # VHS: срыв трекинга
        for (f0, dur, y0, h) in self.track:
            if f0 <= i < f0 + dur:
                o = out if out is not frame else out.copy()
                sh = rr.randint(-40, 40)
                o[y0:y0 + h] = np.roll(o[y0:y0 + h], sh, axis=1)
                ln = rr.randint(y0, y0 + h)
                o[ln:ln + 3] = np.clip(o[ln:ln + 3].astype(np.int16) + 120, 0, 255).astype(np.uint8)
                out = o
        # вспышка
        fl = float(fx.get("flash", 0))
        if fl > 0:
            src = env["kick"] if fx.get("flash_on") == "kick" else env["bar"]
            a = fl * src[i]
            if a > 0.01:
                out = cv2.addWeighted(out, 1 - a, self.flash_img, a, 0)
        # инверсия (фонк)
        if i in self.invert_frames:
            out = 255 - out
        # строки развёртки (VHS)
        if fx.get("scanlines"):
            o = out if out is not frame else out.copy()
            o[::3] = (o[::3].astype(np.uint16) * 200 >> 8).astype(np.uint8)
            out = o
        return out


# ---------------------------------------------------------------------------
# 3. Главная функция
# ---------------------------------------------------------------------------
def render(beat_path, genre, out_path, message=None, seed=None, pexels_key=None,
           producer=None, workdir="work", log=log_default, target_len=24.0, pixabay_key=None):
    t_start = time.time()
    if seed is None:
        seed = random.randrange(10 ** 9)
    rng = random.Random(seed)
    style = GENRES[genre]
    os.makedirs(workdir, exist_ok=True)
    ensure_fonts(log=log)

    info = analyze(beat_path)
    start, length = pick_segment(info, target=target_len)
    n = int(round(length * FPS))
    length = n / FPS
    env = envelopes(info, start, length, FPS)
    cuts = cut_points(info, start, length, style["cut_sec"], FPS, rng)
    cut_frames = sorted(set(int(round(c * FPS)) for c in cuts if c * FPS < n - 8))
    if not cut_frames or cut_frames[0] != 0:
        cut_frames = [0] + cut_frames
    seg_frames = [b - a for a, b in zip(cut_frames, cut_frames[1:] + [n])]
    log(f"BPM {info['bpm_display']} | кусок {start:.1f}–{start + length:.1f} c | кадров фона: {len(seg_frames)}")

    clips = gather(genre, style, len(seg_frames), os.path.join(workdir, "bg"), rng,
                               pexels_key=pexels_key, pixabay_key=pixabay_key, log=log)
    # раздаём ролики по кадрам, не повторяя один и тот же подряд
    order = []
    pool = []
    for _ in seg_frames:
        if not pool:
            pool = clips[:]
            rng.shuffle(pool)
            if order and len(pool) > 1 and pool[-1][0] == order[-1][0]:
                pool[0], pool[-1] = pool[-1], pool[0]
        order.append(pool.pop())
    segments = [(p, d, nf, 0) for (p, d), nf in zip(order, seg_frames)]
    grade = rng.choice(style["grade_variants"])
    scrim = make_scrim(os.path.join(workdir, "scrim.png"), float(style.get("scrim", 0.4)))
    bg_cmd = build_bg_command(segments, style, grade, n, scrim=scrim)

    if not message:
        message = rng.choice(style["messages"])
    header = f"{style['label']} · {info['bpm_display']} BPM"
    scene = TextScene(message, genre, env, FPS, header=header, producer=producer, seed=seed,
                             cuts=cut_frames)
    fx = BgFX(style, env, cut_frames, n, seed)

    enc_cmd = ["ffmpeg", "-v", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
               "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", beat_path,
               "-map", "0:v", "-map", "1:a",
               "-af", f"afade=t=in:d=0.02,afade=t=out:st={max(0, length - 0.6):.3f}:d=0.6",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", "9M", "-bufsize", "18M",
               "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
               "-movflags", "+faststart", "-t", f"{length:.3f}", out_path]

    errlog = open(os.path.join(workdir, "ffmpeg.log"), "wb")
    dec = subprocess.Popen(bg_cmd, stdout=subprocess.PIPE, stderr=errlog, bufsize=W * H * 3 * 2)
    enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=errlog)
    fsize = W * H * 3
    last = None
    t_fx = 0.0
    try:
        for i in range(n):
            buf = dec.stdout.read(fsize)
            if buf is not None and len(buf) == fsize:
                frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
                last = frame
            elif last is not None:
                frame = last
            else:
                frame = np.zeros((H, W, 3), np.uint8)
            t0 = time.time()
            frame = fx.apply(frame, i)
            img = Image.fromarray(frame if frame.flags["C_CONTIGUOUS"] else np.ascontiguousarray(frame))
            if img.mode != "RGB":
                img = img.convert("RGB")
            scene.render(img, i)
            t_fx += time.time() - t0
            enc.stdin.write(img.tobytes())
    finally:
        try:
            enc.stdin.close()
        except Exception:
            pass
        dec.stdout.close()
        dec.kill()
        enc.wait()
        dec.wait()
        errlog.close()
    if enc.returncode != 0 or not os.path.exists(out_path):
        with open(os.path.join(workdir, "ffmpeg.log"), "r", errors="ignore") as f:
            tail = f.read()[-1500:]
        raise RuntimeError("ffmpeg не собрал видео: " + tail)
    log(f"готово за {time.time() - t_start:.0f} c (эффекты {t_fx:.0f} c), {os.path.getsize(out_path) / 1e6:.1f} МБ")
    return {
        "bpm": info["bpm_display"],
        "message": message,
        "genre": genre,
        "length": length,
        "start": start,
        "seed": seed,
    }


def post_caption(result, producer=None):
    st = GENRES[result["genre"]]
    lines = [result["message"], "",
             f"🎧 {st['title']} · {result['bpm']} BPM",
             "Нужен этот бит? Пиши в директ 📩",
             "Зачитай под него и отметь меня 🔥", ""]
    tags = st["tags"] + BASE_TAGS
    lines.append(" ".join(dict.fromkeys(tags)))
    return "\n".join(lines)

# ===========================================================================
# bot
# ===========================================================================


TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}/"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}/"
OWNERS = {x.strip() for x in os.environ.get("OWNER_ID", "").replace(";", ",").split(",") if x.strip()}
TIME_BUDGET = float(os.environ.get("TIME_BUDGET_MIN", "40")) * 60  # сколько минут работать за один запуск
MAX_VIDEOS_PER_BEAT = 6
USED_MESSAGES = set()  # чтобы в одном запуске фразы не повторялись
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus")


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------
# Telegram API (только стандартная библиотека)
# ---------------------------------------------------------------------------
def tg(method, params=None, timeout=60):
    data = urllib.parse.urlencode({k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                                   for k, v in (params or {}).items()}).encode()
    req = urllib.request.Request(API + method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        res = json.loads(e.read().decode() or "{}")
    if not res.get("ok"):
        raise RuntimeError(f"Telegram {method}: {res.get('description')}")
    return res["result"]


def tg_upload(method, params, field, path, filename=None):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    with open(path, "rb") as f:
        blob = f.read()
    fname = filename or os.path.basename(path)
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{fname}\"\r\n"
                  f"Content-Type: video/mp4\r\n\r\n").encode() + blob + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(API + method, data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                res = json.loads(r.read().decode())
            if res.get("ok"):
                return res["result"]
            raise RuntimeError(res.get("description"))
        except Exception as e:
            if attempt == 2:
                raise
            log(f"повтор отправки: {e}")
            time.sleep(5)


def send(chat_id, text, reply_to=None):
    try:
        p = {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
        if reply_to:
            p["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return tg("sendMessage", p)
    except Exception as e:
        log(f"не отправилось сообщение: {e}")


def get_updates(offset=None):
    p = {"timeout": 0, "allowed_updates": ["message"], "limit": 100}
    if offset is not None:
        p["offset"] = offset
    return tg("getUpdates", p)


def ack(update_id):
    """Подтверждает Telegram, что всё до update_id включительно обработано."""
    try:
        tg("getUpdates", {"offset": update_id + 1, "timeout": 0, "limit": 1})
    except Exception as e:
        log(f"ack: {e}")


# ---------------------------------------------------------------------------
# Разбор сообщений
# ---------------------------------------------------------------------------
HELP = """🎬 Бит → видео для Reels / TikTok

Пришли mp3 с битом и подпиши его жанром:
• лирика — дождь, осень, ч/б, буквы падают каплями
• трэп — лакшери, золото, вспышки на бочку
• андер — олдскул, 90-е, VHS, печатная машинка
• дрилл — ночь, холод, глитч
• фонк — дрифт, красный неон, тряска
• кавказ — горы, трасса, тёплое свечение

Примеры подписи:
  лирика
  трэп Сможешь зачитать сюда?   ← своё послание
  лирика x3   ← 3 разных видео
  лирика трэп кавказ   ← по видео в каждом жанре
  все   ← по видео во всех жанрах

Без подписи жанр выберу сам по темпу.
Чтобы переделать: ответь (reply) на свой mp3 новой подписью.

Видео приходит через 5–20 минут: бот просыпается по расписанию.
/id — твой Telegram ID"""


def parse_caption(caption):
    """'трэп кавказ x2 Твоё послание' -> (['trap','kavkaz'], 2, 'Твоё послание')"""
    caption = (caption or "").strip()
    genres, count = [], 1
    words = caption.split()
    i = 0
    while i < len(words):
        w = words[i]
        wl = w.lower().strip(",.;:!")
        m = re.fullmatch(r"[xхx×*]?(\d{1,2})(шт|видео)?", wl)
        if "," in wl and all(find_genre(p, partial=False) for p in wl.split(",") if p):
            for p in wl.split(","):
                g = find_genre(p, partial=False) if p else None
                if g and g not in genres:
                    genres.append(g)
            i += 1
            continue
        if wl in ("все", "всё", "all", "весь"):
            genres = list(GENRE_ORDER)
            i += 1
            continue
        g = find_genre(wl, partial=(i == 0))
        if g:
            if g not in genres:
                genres.append(g)
            i += 1
            continue
        if m and not genres and i == 0 and len(words) == 1:
            count = int(m.group(1))
            i += 1
            continue
        if m and (genres or i > 0):
            count = int(m.group(1))
            i += 1
            continue
        break
    message = " ".join(words[i:]).strip(" -—:")
    count = max(1, min(count, MAX_VIDEOS_PER_BEAT))
    return genres, count, message or None


def audio_of(msg):
    """file_id, file_size, имя — если в сообщении есть аудио."""
    if not msg:
        return None
    if msg.get("audio"):
        a = msg["audio"]
        return a["file_id"], a.get("file_size", 0), a.get("file_name") or a.get("title") or "beat.mp3"
    if msg.get("document"):
        d = msg["document"]
        name = (d.get("file_name") or "").lower()
        if (d.get("mime_type") or "").startswith("audio/") or name.endswith(AUDIO_EXT):
            return d["file_id"], d.get("file_size", 0), d.get("file_name") or "beat.mp3"
    if msg.get("voice"):
        v = msg["voice"]
        return v["file_id"], v.get("file_size", 0), "voice.ogg"
    return None


def allowed(msg):
    if not OWNERS:
        return True
    uid = str((msg.get("from") or {}).get("id", ""))
    cid = str(msg["chat"]["id"])
    return uid in OWNERS or cid in OWNERS


def collect(updates):
    """Делит апдейты на задания (видео) и простые команды."""
    jobs, simple = [], []
    group_caption = {}
    for u in updates:
        msg = u.get("message")
        if not msg:
            simple.append((u, None))
            continue
        a = audio_of(msg)
        if a:
            cap = msg.get("caption")
            gid = msg.get("media_group_id")
            if gid:
                if cap:
                    group_caption[gid] = cap
                cap = cap or group_caption.get(gid)
            jobs.append((u, {"msg": msg, "audio": a, "caption": cap}))
            continue
        rep = msg.get("reply_to_message")
        ra = audio_of(rep)
        if ra and msg.get("text") and not msg["text"].startswith("/"):
            jobs.append((u, {"msg": msg, "audio": ra, "caption": msg["text"]}))
            continue
        simple.append((u, None))
    # подписи у альбома могут прийти позже первого файла
    for u, j in jobs:
        gid = j["msg"].get("media_group_id")
        if gid and not j["caption"] and gid in group_caption:
            j["caption"] = group_caption[gid]
    return jobs, simple


def handle_simple(u):
    msg = u.get("message")
    if not msg:
        return
    chat = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    if text.startswith("/id"):
        uid = (msg.get("from") or {}).get("id")
        send(chat, f"Твой Telegram ID: {uid}\nДобавь его в секрет OWNER_ID, чтобы ботом мог пользоваться только ты.")
        return
    if not allowed(msg):
        send(chat, "Это личный бот 🙂")
        return
    if text.startswith("/start") or text.startswith("/help") or text.startswith("/genres"):
        send(chat, HELP)
        return
    if msg.get("video") or msg.get("photo"):
        send(chat, "Фоны бот находит сам 👌 Если хочешь свои — загрузи ролики в папку backgrounds/<жанр> в репозитории.")
        return
    if text:
        g, _, _ = parse_caption(text)
        if g:
            send(chat, "Жанр понял 👍 Теперь пришли mp3 с этой подписью — или ответь (reply) этим текстом на уже присланный бит.")
        else:
            send(chat, "Пришли mp3 с битом 🎧 Подпиши жанром, например: лирика\n\n/help — все жанры и примеры")


# ---------------------------------------------------------------------------
# Режимы запуска
# ---------------------------------------------------------------------------
def gh_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{key}={value}\n")
    log(f"{key}={value}")


def cmd_check():
    if not TOKEN:
        log("нет TELEGRAM_TOKEN")
        gh_output("has_work", "false")
        return
    updates = get_updates()
    jobs, simple = collect(updates)
    jobs = [j for j in jobs if allowed(j[1]["msg"])]
    if jobs:
        gh_output("has_work", "true")
        return
    # заданий нет — отвечаем на команды и подтверждаем всё прочитанное
    for u, _ in simple:
        handle_simple(u)
    for u, j in collect(updates)[0]:
        if j and not allowed(j["msg"]):
            send(j["msg"]["chat"]["id"], "Это личный бот 🙂")
    if updates:
        ack(updates[-1]["update_id"])
    gh_output("has_work", "false")


def guess_genre(path):
    info = analyze(path)
    bpm = info["bpm_display"]
    if bpm <= 96:
        return "lyric"
    if bpm <= 126:
        return "kavkaz"
    return "trap"


def download(file_id, size, workdir):
    if size and size > 20 * 1024 * 1024:
        raise ValueError("файл больше 20 МБ — Telegram не даёт ботам скачивать такие. Пришли mp3 поменьше (320 kbps норм).")
    f = tg("getFile", {"file_id": file_id})
    url = FILE_API + f["file_path"]
    ext = os.path.splitext(f["file_path"])[1] or ".mp3"
    path = os.path.join(workdir, f"beat_{uuid.uuid4().hex[:8]}{ext}")
    with urllib.request.urlopen(url, timeout=120) as r, open(path, "wb") as out:
        out.write(r.read())
    return path


def process_job(job, workdir):
    msg = job["msg"]
    chat = msg["chat"]["id"]
    mid = msg["message_id"]
    file_id, size, name = job["audio"]
    genres, count, message = parse_caption(job["caption"])
    beat = download(file_id, size, workdir)
    auto = False
    if not genres:
        genres = [guess_genre(beat)]
        auto = True
    plan = [(g, k) for g in genres for k in range(count)][:MAX_VIDEOS_PER_BEAT]
    titles = ", ".join(GENRES[g]["title"] for g in dict.fromkeys(g for g, _ in plan))
    note = f"🎬 Делаю {len(plan)} видео: {titles}"
    if auto:
        note += "\n(жанр не указан — выбрал по темпу; подпиши mp3, если нужен другой)"
    send(chat, note, reply_to=mid)
    used_msgs = USED_MESSAGES
    for g, k in plan:
        try:
            tg("sendChatAction", {"chat_id": chat, "action": "upload_video"})
        except Exception:
            pass
        seed = random.randrange(10 ** 9)
        msg_text = message
        if not msg_text:
            pool = [m for m in GENRES[g]["messages"] if m not in used_msgs] or GENRES[g]["messages"]
            msg_text = random.Random(seed).choice(pool)
            used_msgs.add(msg_text)
        out = os.path.join(workdir, f"{g}_{seed}.mp4")
        res = render(beat, g, out, message=msg_text, seed=seed,
                            pexels_key=os.environ.get("PEXELS_API_KEY") or None,
                            pixabay_key=os.environ.get("PIXABAY_API_KEY") or None,
                            producer=os.environ.get("PRODUCER_NAME") or None,
                            workdir=os.path.join(workdir, f"w_{seed}"), log=log)
        caption = f"{GENRES[g]['title']} · {res['bpm']} BPM"
        tg_upload("sendVideo", {"chat_id": chat, "caption": caption, "supports_streaming": "true",
                                "width": 1080, "height": 1920, "duration": int(round(res["length"])),
                                "reply_parameters": {"message_id": mid, "allow_sending_without_reply": True}},
                  "video", out, filename=f"{g}_{res['bpm']}bpm.mp4")
        send(chat, post_caption(res))
        try:
            os.remove(out)
        except OSError:
            pass


def cmd_run():
    if not TOKEN:
        log("нет TELEGRAM_TOKEN")
        return
    t0 = time.time()
    workdir = os.path.abspath("work")
    os.makedirs(workdir, exist_ok=True)
    rounds = 0
    while time.time() - t0 < TIME_BUDGET and rounds < 20:
        rounds += 1
        updates = get_updates()
        if not updates:
            break
        jobs, simple = collect(updates)
        job_by_id = {u["update_id"]: j for u, j in jobs}
        for u in updates:
            if time.time() - t0 > TIME_BUDGET:
                log("время запуска вышло — остальное сделаю в следующий раз")
                return
            uid = u["update_id"]
            j = job_by_id.get(uid)
            ack(uid)  # подтверждаем до начала работы, чтобы не зациклиться на «битом» файле
            if not j:
                handle_simple(u)
                continue
            chat = j["msg"]["chat"]["id"]
            if not allowed(j["msg"]):
                send(chat, "Это личный бот 🙂")
                continue
            try:
                process_job(j, workdir)
            except Exception as e:
                traceback.print_exc()
                send(chat, f"⚠️ Не получилось сделать видео: {str(e)[:300]}\nПопробуй прислать бит ещё раз.",
                     reply_to=j["msg"]["message_id"])


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "check":
        cmd_check()
    else:
        cmd_run()

