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
import base64
import datetime
import hashlib
import io
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
    # главная фраза — Rubik Spray Paint (вариант 5)
    "spray": {
        "family": "Rubik Spray Paint", "weight": 400,
        "urls": [GF + "rubikspraypaint/RubikSprayPaint-Regular.ttf",
                 "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/rubikspraypaint/RubikSprayPaint-Regular.ttf"],
        "file": "RubikSprayPaint-Regular.ttf",
        "fallback": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    },
    # шапка, плашка «Пиши в директ»
    "ui": {
        "family": "Montserrat", "weight": 700,
        "urls": [GF + "montserrat/Montserrat%5Bwght%5D.ttf"],
        "file": "Montserrat-700.ttf",
        "fallback": ["/usr/share/fonts/opentype/inter/Inter-Bold.otf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    },
    # экранное меню VHS (андеграунд)
    "mono": {
        "family": "PT Mono", "weight": 400,
        "urls": [GF + "ptmono/PTM55FT.ttf"],
        "file": "PTMono.ttf",
        "fallback": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"],
    },
}

# Фраза в центре видео и первая строка поста — всегда одна
PHRASE = "Сможешь зачитать на этот бит?"

# ---------------------------------------------------------------------------
# Общие хэштеги и призыв внизу экрана
# ---------------------------------------------------------------------------
BASE_TAGS = ["#бит", "#битдлярэпа", "#инструментал", "#битмейкер", "#рэп", "#typebeat", "#beats"]
CTA_TEXT = "ПИШИ В ДИРЕКТ"   # плашка под фразой, справа от неё 👇🏼, ниже — логотип

# ---------------------------------------------------------------------------
# Жанры
#   aliases  — слова, по которым бот понимает жанр из подписи к mp3
#   queries  — что искать на Pixabay/Pexels для фона (на английском)
#   grade    — цветокоррекция ffmpeg
#   cut_sec  — примерная длина одного кадра до смены (подгоняется под доли)
#   speed    — скорость фонового видео (меньше 1 — замедление)
#   xfade    — плавный переход между кадрами (сек), 0 = резкая склейка
#   fx       — эффекты на бочку (только фон, текст стоит на месте)
#   text     — оформление фразы (цвет свечения)
#   ambient  — атмосфера поверх фона: дождь, огоньки, экран VHS
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
        "text": {"font": "spray", "size": 104, "upper": True, "fill": (255, 255, 255), "stroke": 0,
                 "shadow": 0.85, "glow": (190, 210, 255), "glow_r": 16, "glow_a": 0.3},
        "ambient": "rain",
        "tags": ["#лирика", "#грустныйбит", "#лирическийбит", "#sadbeat", "#lyricbeat"],
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
        "text": {"font": "spray", "size": 104, "upper": True, "fill": (255, 255, 255), "stroke": 0,
                 "shadow": 0.85, "glow": (255, 200, 90), "glow_r": 16, "glow_a": 0.3},
        "ambient": None,
        "tags": ["#трэп", "#trapbeat", "#трэпбит", "#luxury", "#808"],
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
        "text": {"font": "spray", "size": 104, "upper": True, "fill": (255, 255, 255), "stroke": 0,
                 "shadow": 0.85, "glow": (242, 238, 222), "glow_r": 16, "glow_a": 0.3},
        "ambient": "vhs",
        "tags": ["#андеграунд", "#олдскул", "#boombap", "#oldschool", "#рэп90х"],
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
        "text": {"font": "spray", "size": 104, "upper": True, "fill": (255, 255, 255), "stroke": 0,
                 "shadow": 0.85, "glow": (70, 170, 255), "glow_r": 16, "glow_a": 0.3},
        "ambient": None,
        "tags": ["#дрилл", "#drill", "#drillbeat", "#дриллбит", "#ukdrill"],
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
        "text": {"font": "spray", "size": 104, "upper": True, "fill": (255, 255, 255), "stroke": 0,
                 "shadow": 0.85, "glow": (255, 40, 40), "glow_r": 16, "glow_a": 0.3},
        "ambient": None,
        "tags": ["#фонк", "#phonk", "#drift", "#дрифт", "#phonkmusic"],
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
        "text": {"font": "spray", "size": 104, "upper": True, "fill": (255, 255, 255), "stroke": 0,
                 "shadow": 0.85, "glow": (255, 170, 70), "glow_r": 16, "glow_a": 0.3},
        "ambient": "bokeh",
        "tags": ["#кавказскаямузыка", "#кавказ", "#качающийбит", "#кавказскийбит", "#лезгинка"],
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
# assets
# ===========================================================================
LOGO_ALPHA_PNG = """
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
SvHXp6/IzgAAAABJRU5ErkJggg==
"""

EMOJI_POINT_DOWN_PNG = """
iVBORw0KGgoAAAANSUhEUgAAAEEAAABQCAYAAABGfRh7AAARYklEQVR42t1ce3BcV3n/fefcu3df2tX7aVm244ccO4bGSdrYIcaY
EEqgQ4BVGSBQXgGGRyiFFtrCehMItLQBhgwpniktkzCTaEt5NW6aENvKEzdOnEIkx4leli1ZkiWttLva3fs45+sfu3Js41eC5JV9
ZnY0kq527/nd7/t9v+9xRDjHuvNzHVd/4MZrWnqOTlePjo03Eslan2ktV5pV0DJC0UioDRoeiOm0b8DEIBiO685MzmSHhZB6dib1
cqsv1zeRsQc+8O1f7gZAABhlWr934wwQAbypBs3vete2u9/45je9eeXSxgomATCgmaG1Ll7LgKcUiM62BwIzQwiCFAIkBLx8Fu7I
QYylMvjt4OTfffQ7O+/UX/uaoERClwME49QfbI+DkABHqsKbVlb5b26oqcR01vagNTRhbruEVx4fndcncREPAkF7HmeytqoMWb72
1qrPMPM/AtDlsgRxpl9YVoCnUmmVnZ1VQpDBBEMQSSKSgkgIIkHFr3Rer+IfCCIIaVpSCOHL254KWmb1Nz+65b2USOid33urhfMF
9cKAYDXksxnpOQ6REPPshAJGMAKlFfl9hrX58pZ//nzHxs1vu+0hm5kRj0OUHQQGqCLsW24XHHjunM/P12IQAUYoCoBF3va4PhKs
v/nqNf/xnU9u+wgRyUQCmuNxUVYQCOCWiPW6heJi1gwzGIX0V0CAKe8obqgMNm65ovVf/+cbHY996IZ1mymR0PEtW4wL4R6ngiBu
T0BvWhm5LGCKq4QQEGIh7kFDmD74a5oBZggiyjseG4L0quaqaz/1tisfuf2W69+X6OryuLNTXFAQOjtjxAA2L2+8LWTKqkhVxAsF
A0IpPc+Pg8BawQxXw6ptBWsPgkCaIWZmbVUZtgI3XLn0R9/40Jb3UkeH6ozF5EKCcPzN44D4dLKHn1vfsmF1U/hu9jyjbfVq0ba2
nTzHA4mFscoiNwh4uTQIgJBCOJ7SkYDPbKgKvWNJXaT3M/c/9LvOzphMJnt4QUHYU+KCt17R8K3mCusaaVn66jdtFYYVAFgXJdSC
LIYRisLwh6DsLLRrQwpJjtI6GrLMxurwTVKK3V+5c9dQPB4XXV1dvFDuIAjgTauir2+K+DtyBYdXrm0XkZo6KNdbQADmcNAwK6oR
bFkDs6IGmhmCIHK2p5qqQoG/2Lbu37/x4evqvn7H7Tq+AFFDAAB3xggArr+s8WMVlhEMhgN6xfr15Hnegu//FRw8SJ8foSVrEGhY
AZCAAMtM3vaaa8KrVrfU/1BpluvW9RDPc8QQcUBQR1K9ob22yTKNtzu2zW2rVlFFVTW0UrhgKICKyYjW8FfVI9iyhkkaIGYjnXf0
5a1VN3/ro2/8WEdHUiEen18Q1sWKVrChPrK1LmS0kc/SzStWCZ67qTIs1h58oQoKL13Lhj8MrRUMKfia1U13fPadGy8Tt9+uY/MY
McSfJ5MKRVZ+j/I8rqyqpvolS+A6DuZdLr+qEKohrSAFWlbD8IVELm/rZQ3Ruk3tLXdqzdTZ2annS0gJBtAG+Buj1tUeg1pWLKPy
bf73tYQwfPA3LGMyfDKbL+h1bbUdX33/tTcREfM8uYUAgNoVVRslUZMgiaZlK6C1vvCp3Bl5QsEXqqRg/RJopeE3Dd66oe2bGzc2
BbF9O8+HNQgAuLotukmCZXVdjQ5FKoi1voCEeD4W4cGK1MOM1IpcwdZtDdH1t2278jYi4vmQ1QIAllZVrPcJoLqxUZuWdbxytJgW
ExCoXQIyDNJKcVN1+JMfuGFDPWKxP1g7CADBgqNWCgIq6+pJGmbZosLZcy4NaQXhr24W2ZzNyxsql267ouXLRMTb/1BL2NhWs9Tz
vGVmIIhQZRVpz1tErnCiVxSJ0qpqgi9SQ7btcHtr7Ue2rm1sw/YEM792bhBXLY/W+ARVG5bFoVCQ1CJ0hVPBCNQtJU2Gqqmwoh9/
5zV/TwTGnvhr1g2ivTFaUxk0/VIayu/3kdY8z5Wk+SYHBcMfgK9miXQ8pdtbqm/50ruvfTttTXj7fnir+ZpAGEkVmgUY0ueHEQgD
WmGRmwJYK/ir6klUNFDYktZNf7zi7huuWrH+qk/scDs7Y/LV1ijl+uZorD5sbK5uauLWVauE8rzFbQkn5BlmKEr5bFpHTFX1J2tb
/3R1a2XfJ76y82BXF5iZRU9Pj+jpOXcNwpASrQyCafkIzIuTFM/CD+GW1SI91KPrwoXlW69o+9mPv3jT9599cfS7RDQEAMwskskO
6u5O8vZEsUNEp3SKDGiYBEIgGATjIlvMgJAIt6wW+fFDOuCmjM1rm/+yrT7y4WvWN/0g+XR/koien7s8cRw7gn7gPZI6khoA0xdv
WPXMkqj/qg3Xb+HVGzaQ6zgXgTucahECBIY9M6ELk8Mw2BOWQRgcz0wF/b4XB8fT+x7e37enubriueUtdVMf+/YvZwFoZiYiYkMK
CvIcqhfrYg0G4KusF2a4CnZmiguzKdXaZFVLVdgUDVRvWtNS+TkphMoUnJn7/ubPfjOdy91PRPcCIHndyppPhS2jvrGtDTWNjaSV
uvgs4QQwiATMYAWZFTUC/gqtzKDWEBDQBNYiYIrAioboqmUNVTdfvqxWP7i3r8u4+DYKMM6kZeh4UQYADF9AGFYQvooasNZQrg1v
dprTqTHtg8KVK+oT3//0tscvOhCkIQEieK5bao2fBQzWgC5GPBIShj8IM1hB/midnB7sVg1Rg0Km1SEujqfPEFLCzmXx/J5H9bHh
YfZbPpCU4HNx2XGQGGAGew7ItGBV1pPjKWZCk7hIPAAEQCvGUF+feOwXP6PfPfmE8gp5mD4fiIr9zXMBwszHpZCUxIIEFWx38GJx
B5ZSkAJmxnLek6ZSW1/a/1xgaGBQtW/cKJpal5I/HIZmhue6xWbRKUklEcEwTUhpID+b1eOHBnHgQC8ODEz8p5H39GEpaD1Ai94S
pof76VcvHPni5XXR2vba4D+EU6lr9/36EYSjUb10bbuubWwWdY0NQlgBnJxbEzzX0amxMZ0aHxUjA4NifHgY3UdS39rx1NDjhuPq
yeLwUX5e6nULyQvatbGysqLyJ08NPA7gxo9fv+wjrZXBL3mpmZbZp58WVjAIXzCkw1XVyuezSGsNIYk92xb5TEa6uVkxOZVCxlbD
Ayknce/eoX+LxyEMZh4GGNlMjhe3PiAApMdSs4VYDLKzk7NE9L1wGD953+uXf7ClOnSjcyyzrsKabSlMTwldogchCHnbw7GsPRG0
fPuP5tSD9+wauw/ITBIBiQRgLKmu6CbWKOQLxRGSRZhEEREL0iQEzzzWOzFCfaS2E4l4HJRIYGLHEwN3Abjrj5ZEVr799S1tIWkG
hiZm6wVBL6kJTeRYOf97bPbIruf7eubeMx6HSBQTKjaO2YUBz9FuZT5ngPUidQmCcm1AswbglpIhRnETFN8Cecdj5O0/ku7dfyTd
e8biCRG+ej0biS6oROKVaTnjZ88c7n//xiX9nM+syU6OKX9lvdSee7IiO24dZZ25PFkJnXB3iS54AFMcoHWxGHWPj9O6+noGgO7x
cQKAnvouTiZZF689pZ5weDI/YvmM36Qz+TWjfS9h+euiIOl7pcJEBJIG2HMh4AEkwGSAWS02o+EEwEgmX315DQD2Hpq8d3q2gKmx
ccoceRH21NGi8BAC2rXZnhzGzKED2L9nF/r3PwM3lwYJo5R58ivWMjcPS+K8AOLzSl65JIbUudXha1wGA0S/G9+1oTH66MSx1Lam
1LQK2jlpp0aLxQetSCgPE5Np7nnhJaqpiaIqYiHcshJGqBKYq06Xxnm0W4CkIrVoYZaGhE8UL1wCieEjQEGcX7NHayzU0KvYHo8T
EfFAavbTIxMzY0eHJ6Rp+lxlF5Rn57RyHCWEVJZlkWkaEFKClYPcSC8Kxw7Dy2fgFWbhZqaQHx/EzGA3nn14J7qf2I3c6ABr1y5a
DVEpkTHAykV6uI/3d/0ak6MjEFKW1Y+MRCKh44BI7Bs5OLO+6RaFoR3hsH/Z6sua4SoNQwoozRhJZZ7P2e7yKiAKCGjlIn9sCMIw
Ufzeg4RCZtbFSwOjCAUDqKupIGXnYFY2wheqAEBwchmomTHkp1N0oKcfsCKoa2nFuQu8C0fIRincFIF44egjD79wdPMX8t7nM67e
bPqtSp8p0tm893jHN3/1T5/detmPGgqFt3nKY59pCkgB1gookSQZJhiONg1JhiGJyQA7eRTG+mDPpbdgCCKYPh8i4QDE+WgSImjP
KbnVAoFwEhDAyF27ev/6rl29oWAwGMnlchkAWQBQ4Ac9rW8qFDzP8pniuKwoRU6ttfYbhjClgNZ83P/phDYAHb+eoTXDzmXPzZ4l
SlggDE5uUiSKzEOdsZgURLO5XO6oIMr+8NZbzXgc4ge7+3/SO5Z+fHoiZQohFTMrZigACsQ6ErBEpuAOZPPOUc+12XEcFkRnjADM
4EI+X/bYerp6Anckk0ozUzwOoZnpEzt2uIkEmAjpgyOTtzy2v//pyWNTsiLkl5GgT/p9hjQNKfqOTj/XO6Pf+OJY+h4oRXbB1sUh
UD5daGRDCmI+H61BIFagBeIF46ziI3HSpzIzaOeB6UM7D0y/ZSSdv+XDN2++sbk6FDo0kZl+6sDIL2+/94lOAPYtm1f8eDJbuC01
la2pq6/0AMi5ijYDbEoBSIhMrlAgI+0/n+oQuzZY6wsOwmmBKfFG9r69h++5b+/995x6QTy+xUgkuoZuWt/4wXBg7IHm5ppwdVUY
tutpQ0ohpSDLlHh4/+DdfUfGW667svpmIlI4Ybp2MbjDWdccb8RikLvjWwzeHTd2x7cYpWkRSiS6vDggHnxhdOev/m/gXY/uPbB3
cHQm7ykWE+m8ncoW+p/oPvy3t373oc9WBP15VgradfncmevCSfTXWl7jZBIqiS4g0VX6UddJQMVikMlk+pHHep/f9e6j+Te849qV
S5/vHR397s+f3QdgShBBM5nKLcDJTsOM1IDVaQZE5iSzchesH7JgNcZkEioOiDuI1E+fPLjnp08ePF7r0w88IKmjo+Thc7nG2Xoq
GtDe4nGHV+s6mplisZjcHd9idMZikpkFuruLxQxBjqsYs3kXpROHF5U7vErXSaoTE9wdR//LBKCrguYItIbyXKYypuVl7TsYQnrM
GlqdZViMCNAK7LkLVvYrKwjClEK5LnLZLIQQZeuMlxUE11OOWgSF3bKAMHIwzAAwPJM7ks45cO2CJKIz2AGBWRVfCxQjy2sJ2nBd
10MhX6Az7Y8AaM1grRHwGVkA01prumRAkAYXP/8cOoFKmZNlijwAhWSHmM8qS3ktwVYuSMDLZ6BdG3Qu0cS4dNyhp754rK9/onDE
VWyz61DxvNUZLIEZVJpLunQ4oaScPKk8ELQuzQ2ctqxEBFYutOec01IuSnfQDgshBDKzNmzHLdYby6AVygoCC6EAsKDy9rJEmbxB
A8Azg9P9UlKa7RxYeWUbDSirJeTzeSYi5AoOnNI/q+DfE0oM5RawkAnWohjcEkRnVc68wMcPyg0CEUnkc3m4jnuWw6ji0gSh9OBT
PkMUPNeDVu6ZLcHzToiZlw4I/LXi6ZQcEWdJEGbT6dO03otdLF1Ik2Lwy6MzgwBeywjC4uaEganZ5wQJPjowUNIJJ2hkYcLLZeHm
s2AQ7Xv56H8DQPfll/MlBcJE2r5fEdHwoUM8fuQQm5YPTAQyDCgnh9xovxfySTF4bOawPcm/AIDt2xOXBgiJBHQ8Hhf3PzP80GjG
vttHLJ/ZtYfHXn5BOakRnTnyss4OdbsBqYxUzkHvSOrL33n4N1OdnTFJ89yPK/fQEYHAYOALb1l1V63f+ExLfZW5bFkjmhurIaXA
0LHM4LP9Y1/5q3/Zdf/cSZUFIunyAlFq8PPn3rz6OrfgvEmRWN/evmx6aWPlvvse/e2jP3+qt2+hAACA/wdOuVgVATWvXQAAAABJ
RU5ErkJggg==
"""


def load_png(b64):
    from PIL import Image
    return Image.open(io.BytesIO(base64.b64decode("".join(b64.split())))).copy()

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
# Шрифты: Google Fonts (с кириллицей), запасной путь — GitHub
# ---------------------------------------------------------------------------
_font_cache = {}
GF_CSS = "https://fonts.googleapis.com/css?family={spec}&subset=cyrillic,latin"
TEST_CYR = "СМОЖЕШЬЗАЧИТАТЬНАЭТОТБИТсможешьзачитать"
TEST_LAT = "TYPEBEATBPM"


def _fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _coverage(path):
    """(есть кириллица, есть латиница) — или None, если файл не шрифт."""
    try:
        f = ImageFont.truetype(path, 40)
    except Exception:
        return None
    cyr = not [c for c in _missing(f, TEST_CYR) if c.isalnum()]
    lat = not [c for c in _missing(f, TEST_LAT) if c.isalnum()]
    return cyr, lat


def ensure_fonts(log=print):
    os.makedirs(FONT_DIR, exist_ok=True)
    for key, f in FONTS.items():
        path = os.path.join(FONT_DIR, f["file"])
        cov = _coverage(path) if os.path.exists(path) else None
        if cov and cov[0]:
            continue
        urls = []
        if f.get("family"):
            spec = f["family"].replace(" ", "+") + ":" + str(f.get("weight") or 400) + ("i" if f.get("italic") else "")
            try:
                css = _fetch(GF_CSS.format(spec=spec)).decode("utf-8", "ignore")
                urls += re.findall(r"url\((https://fonts\.gstatic\.com/[^)\s]+)\)", css)
            except Exception as e:
                log(f"шрифт {key}: Google Fonts не ответил ({e})")
        urls += list(f.get("urls", []))
        best = None
        for k, u in enumerate(urls):
            tmp = f"{path}.{k}.tmp"
            try:
                data = _fetch(u)
                with open(tmp, "wb") as out:
                    out.write(data)
                cov = _coverage(tmp)
            except Exception as e:
                log(f"шрифт {key}: не скачался {u[:60]}… ({e})")
                cov = None
            if cov and cov[0] and cov[1]:
                best = tmp
                break
            if cov and cov[0] and best is None:
                best = tmp
                continue
            try:
                os.remove(tmp)
            except OSError:
                pass
        if best:
            os.replace(best, path)
            log(f"шрифт {key}: {f.get('family')} ок")
        else:
            log(f"шрифт {key}: не получилось скачать, будет запасной")
        for k in range(len(urls)):
            try:
                os.remove(f"{path}.{k}.tmp")
            except OSError:
                pass


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
    notdef = {_glyph(font, "\U0010FFFD"), _glyph(font, "")}
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
def with_alpha(img, a):
    if a >= 0.999:
        return img
    r, g, b, al = img.split()
    al = al.point(lambda v: int(v * max(0.0, a)))
    return Image.merge("RGBA", (r, g, b, al))


def paste(base, img, x, y, alpha=1.0):
    if img is None or alpha <= 0.003:
        return
    if alpha < 0.999:
        img = with_alpha(img, alpha)
    base.paste(img, (int(round(x)), int(round(y))), img)


def stylize(fill_mask, full_mask, st):
    """Маски (L) -> (текст RGBA с тенью, свечение RGBA или None)."""
    fm = np.asarray(fill_mask, np.float32) / 255.0
    am = np.asarray(full_mask, np.float32) / 255.0
    h, w = fm.shape
    fill = np.broadcast_to(np.array(st.get("fill", (255, 255, 255)), np.float32), (h, w, 3))
    stroke_col = np.array(st.get("stroke_fill", (0, 0, 0)), np.float32)
    rgb = fill * fm[..., None] + stroke_col * np.clip(am - fm, 0, 1)[..., None]
    rgb = rgb / np.maximum(am, 1e-4)[..., None]
    alpha = am.copy()
    sh = float(st.get("shadow", 0) or 0)
    if sh > 0:
        s = np.asarray(full_mask.filter(ImageFilter.GaussianBlur(7)), np.float32) / 255.0
        s = np.roll(s, 6, axis=0) * sh
        out_a = alpha + s * (1 - alpha)
        rgb = (rgb * alpha[..., None]) / np.maximum(out_a, 1e-4)[..., None]
        alpha = out_a
    main = Image.fromarray(np.dstack([np.clip(rgb, 0, 255), np.clip(alpha * 255, 0, 255)]).astype(np.uint8), "RGBA")
    glow = None
    if st.get("glow"):
        r = int(st.get("glow_r", 14))
        g = full_mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(r))
        ga = np.clip(np.asarray(g, np.float32) / 255.0 * 1.8 * float(st.get("glow_a", 0.5)), 0, 1)
        col = np.broadcast_to(np.array(st["glow"], np.float32), (h, w, 3))
        glow = Image.fromarray(np.dstack([col, ga * 255]).astype(np.uint8), "RGBA")
    return main, glow


def glue(text):
    """Тире и короткие предлоги не остаются в начале/конце строки."""
    text = " ".join(text.split())
    text = re.sub(r" ([—–-]) ", " \\1 ", text)
    text = re.sub(r"(^|\s)([а-яА-ЯёЁa-zA-Z]{1,2}) ", lambda m: m.group(1) + m.group(2) + " ", text)
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
            score = float(np.var(ws))
            if score < best_score:
                best, best_score = parts, score
    return best


class TextBlock:
    """Фраза по центру: подбирает размер, переносит строки, рисует с тенью и свечением."""

    def __init__(self, text, st, center=(540, 900), max_w=820, max_lines=4):
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
        lines = [l.replace(" ", " ") for l in balance(lines, font, max_w)]
        self.text = clean.replace(" ", " ")
        self.font, self.size, self.lines = font, size, lines
        asc, desc = font.getmetrics()
        line_h = int(size * 1.14)
        stroke = int(st.get("stroke", 0) or 0)
        pad = int(st.get("glow_r", 10) * 2.4 + stroke + 14)
        over = int(size * 0.35)
        lw = [font.getlength(l) for l in lines]
        bw = int(max(lw) + 2 * pad + 2 * over)
        bh = int((len(lines) - 1) * line_h + asc + desc + 2 * pad)
        fill_m = Image.new("L", (bw, bh), 0)
        full_m = Image.new("L", (bw, bh), 0)
        df, da = ImageDraw.Draw(fill_m), ImageDraw.Draw(full_m)
        for j, line in enumerate(lines):
            x0 = pad + over + (max(lw) - lw[j]) / 2
            y = pad + asc + j * line_h
            df.text((x0, y), line, font=font, fill=255, anchor="ls")
            da.text((x0, y), line, font=font, fill=255, anchor="ls", stroke_width=stroke, stroke_fill=255)
        main, glow = stylize(fill_m, full_m, st)
        self.image = Image.alpha_composite(glow, main) if glow is not None else main
        # реальные границы букв — чтобы ставить плашку под текстом
        bb = full_m.getbbox() or (0, 0, bw, bh)
        self.left = int(center[0] - bw / 2)
        self.top = int(center[1] - bh / 2)
        self.ink_bottom = self.top + bb[3]
        self.ink_top = self.top + bb[1]


def simple_text(text, key, size, fill=(255, 255, 255), shadow=0.7, spacing=0):
    font, clean = get_font(key, size, text)
    w = (sum(font.getlength(c) for c in clean) + spacing * (len(clean) - 1)) if spacing else font.getlength(clean)
    asc, desc = font.getmetrics()
    pad = 14
    m = Image.new("L", (int(w + 2 * pad), asc + desc + 2 * pad), 0)
    d = ImageDraw.Draw(m)
    if spacing:
        x = pad
        for c in clean:
            d.text((x, pad + asc), c, font=font, fill=255, anchor="ls")
            x += font.getlength(c) + spacing
    else:
        d.text((pad, pad + asc), clean, font=font, fill=255, anchor="ls")
    main, _ = stylize(m, m, {"fill": fill, "shadow": shadow})
    return main


def cta_pill(text, size=40):
    """Плашка «ПИШИ В ДИРЕКТ 👇🏼»."""
    t = simple_text(text, "ui", size, shadow=0, spacing=3)
    bb = t.getbbox() or (0, 0, t.width, t.height)
    t = t.crop((bb[0], bb[1], bb[2], bb[3]))
    em = load_png(EMOJI_POINT_DOWN_PNG).convert("RGBA")
    eh = int(size * 1.35)
    em = em.resize((max(1, int(em.width * eh / em.height)), eh), Image.LANCZOS)
    padx, pady, gap = 30, 16, 14
    ph = max(t.height, em.height) + 2 * pady
    pw = padx + t.width + gap + em.width + padx
    img = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, pw - 1, ph - 1), radius=ph // 2, fill=(0, 0, 0, 165))
    img.alpha_composite(t, (padx, (ph - t.height) // 2))
    img.alpha_composite(em, (padx + t.width + gap, (ph - em.height) // 2))
    return img


def logo_image(width=430):
    """Логотип Farta Muzik белым, с мягкой тенью."""
    a = load_png(LOGO_ALPHA_PNG).convert("L")
    a = a.resize((width, int(a.height * width / a.width)), Image.LANCZOS)
    pad = 24
    full = Image.new("L", (a.width + 2 * pad, a.height + 2 * pad), 0)
    full.paste(a, (pad, pad))
    shadow = full.filter(ImageFilter.GaussianBlur(8)).point(lambda v: int(v * 0.75))
    sh = Image.merge("RGBA", (Image.new("L", full.size, 0),) * 3 + (shadow,))
    sh = sh.transform(sh.size, Image.AFFINE, (1, 0, 0, 0, 1, -5))
    white = Image.merge("RGBA", (Image.new("L", full.size, 255),) * 3 + (full,))
    return Image.alpha_composite(sh, white)


def soft_dot(r, color, hardness=1.6):
    s = int(r * 2 + 4)
    yy, xx = np.mgrid[0:s, 0:s]
    d = np.sqrt((xx - s / 2) ** 2 + (yy - s / 2) ** 2) / r
    a = np.clip(1 - d, 0, 1) ** hardness
    arr = np.dstack([np.full((s, s), color[0]), np.full((s, s), color[1]), np.full((s, s), color[2]), a * 255])
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


# ---------------------------------------------------------------------------
# Сцена
# ---------------------------------------------------------------------------
class TextScene:
    def __init__(self, message, genre, env, fps, header=None, producer=None, seed=0, cta=None, cuts=None):
        self.genre = genre
        self.style = GENRES[genre]
        self.env = env
        self.fps = fps
        self.n = env["n"]
        self.rng = random.Random(seed)
        self.ambient = self.style.get("ambient")

        # --- статичный слой: шапка, фраза, плашка, логотип --------------------
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if header:
            hd = simple_text(header, "ui", 34, shadow=0.8, spacing=4)
            layer.alpha_composite(with_alpha(hd, 0.88), (int(540 - hd.width / 2), 300))
        if producer:
            pr = simple_text(producer, "ui", 30, fill=(235, 235, 235), shadow=0.8, spacing=2)
            layer.alpha_composite(with_alpha(pr, 0.8), (int(540 - pr.width / 2), 348))
        self.block = TextBlock(message, self.style["text"], center=(540, 860))
        layer.alpha_composite(self.block.image, (max(0, self.block.left), max(0, self.block.top)))
        cta = CTA_TEXT if cta is None else cta
        y = max(self.block.ink_bottom + 70, 1130)
        if cta:
            p = cta_pill(cta)
            layer.alpha_composite(p, (int(540 - p.width / 2), int(y)))
            y += p.height + 18
        lg = logo_image(460)
        layer.alpha_composite(lg, (int(540 - lg.width / 2), int(y)))
        bb = layer.getbbox() or (0, 0, W, H)
        self.layer = layer.crop(bb)
        self.layer_xy = (bb[0], bb[1])
        self._setup_ambient()

    def _setup_ambient(self):
        r = self.rng
        if self.ambient == "rain":
            self.streaks = [(r.uniform(-100, W + 100), r.uniform(0, H + 300), r.uniform(1500, 2400),
                             r.uniform(35, 110), r.randint(22, 70), r.choice((1, 1, 2))) for _ in range(120)]
        elif self.ambient == "bokeh":
            self.bokeh_spr = [soft_dot(rr, col, 1.3) for rr, col in
                              ((14, (255, 190, 110)), (24, (255, 170, 80)), (36, (255, 215, 160)), (54, (255, 150, 60)))]
            self.bokeh = [{"x": r.uniform(0, W), "y": r.uniform(0, H), "v": r.uniform(18, 60),
                           "sw": r.uniform(10, 40), "f": r.uniform(0.1, 0.35), "ph": r.uniform(0, 6.28),
                           "a": r.uniform(0.12, 0.4), "s": r.randint(0, 3)} for _ in range(22)]
        elif self.ambient == "vhs":
            col = (240, 240, 240)
            self.osd_play = simple_text("PLAY ►", "mono", 46, fill=col, shadow=1.0)
            self.osd_sp = simple_text("SP", "mono", 46, fill=col, shadow=1.0)
            d = datetime.date.today()
            mon = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"][d.month - 1]
            self.osd_date = simple_text(f"{mon}. {d.day:02d} 1997", "mono", 42, fill=col, shadow=1.0)
            self.tc_cache = {}

    def render(self, base, i):
        t = i / self.fps
        if self.ambient == "rain":
            d = ImageDraw.Draw(base, "RGBA")
            for (x0, y0, v, ln, al, wd) in self.streaks:
                y = (y0 + v * t) % (H + 300) - 150
                x = x0 - y * 0.08 + 80
                d.line((x, y, x - ln * 0.08, y + ln), fill=(220, 230, 255, al), width=wd)
        elif self.ambient == "bokeh":
            for b in self.bokeh:
                y = (b["y"] - b["v"] * t) % (H + 160) - 80
                x = b["x"] + math.sin(6.283 * b["f"] * t + b["ph"]) * b["sw"]
                a = b["a"] * (0.6 + 0.4 * math.sin(6.283 * b["f"] * 1.7 * t + b["ph"]))
                spr = self.bokeh_spr[b["s"]]
                paste(base, spr, x - spr.width / 2, y - spr.height / 2, a)
        elif self.ambient == "vhs":
            paste(base, self.osd_play, 60, 215)
            paste(base, self.osd_sp, W - 60 - self.osd_sp.width, 215)
            paste(base, self.osd_date, 60, 1560)
            sec = int(t) + 12
            if sec not in self.tc_cache:
                self.tc_cache[sec] = simple_text(f"0:{sec // 60:02d}:{sec % 60:02d}", "mono", 42,
                                                 fill=(240, 240, 240), shadow=1.0)
            tc = self.tc_cache[sec]
            paste(base, tc, W - 60 - tc.width, 1560)
        base.paste(self.layer, self.layer_xy, self.layer)

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
        message = PHRASE
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
• лирика — дождь, осень, ч/б
• трэп — лакшери, золото, вспышки на бочку
• андер — олдскул, 90-е, VHS
• дрилл — ночь, холод, глитч
• фонк — дрифт, красный неон, тряска
• кавказ — горы, трасса, тёплые огни

Фраза в видео всегда одна: «Сможешь зачитать на этот бит?»

Примеры подписи:
  лирика
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
    for g, k in plan:
        try:
            tg("sendChatAction", {"chat_id": chat, "action": "upload_video"})
        except Exception:
            pass
        seed = random.randrange(10 ** 9)
        msg_text = PHRASE  # фраза всегда одна
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

