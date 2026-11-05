"""Генерация фото-карточки подарочного чека (PNG)."""
import io
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 560
YELLOW = (255, 221, 45)
DARK_TOP = (24, 25, 31)
DARK_BOTTOM = (15, 16, 20)
WHITE = (245, 245, 245)
MUTED = (150, 152, 160)

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    r"/System/Library/Fonts/Helvetica.ttc",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _gradient() -> Image.Image:
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        color = tuple(int(DARK_TOP[i] + (DARK_BOTTOM[i] - DARK_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (W, y)], fill=color)
    return img


def make_cheque_image(currency: str, amount_str: str, code_display: str,
                      bot_username: str = "tbank_bot") -> bytes:
    """Рисует карточку чека и возвращает PNG-байты."""
    img = _gradient()
    draw = ImageDraw.Draw(img)

    # Жёлтая рамка-скругление карточки.
    margin = 28
    draw.rounded_rectangle([margin, margin, W - margin, H - margin],
                           radius=36, outline=YELLOW, width=4)

    # Верхняя плашка.
    draw.rounded_rectangle([margin + 18, margin + 18, W - margin - 18, margin + 110],
                           radius=24, fill=YELLOW)
    draw.text((margin + 48, margin + 40), "TBank · подарочный чек",
              font=_font(38), fill=(23, 23, 23))

    # Сумма по центру.
    unit = "STARS" if currency == "STARS" else "GRAM"
    draw.text((W // 2, H // 2 - 60), amount_str, font=_font(110), fill=WHITE, anchor="mm")
    draw.text((W // 2, H // 2 + 30), unit, font=_font(44), fill=MUTED, anchor="mm")

    # Код.
    draw.text((W // 2, H - margin - 130), code_display, font=_font(52),
              fill=YELLOW, anchor="mm")
    draw.text((W // 2, H - margin - 62),
              f"Активировать: @{bot_username} → Чек",
              font=_font(26), fill=MUTED, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    # Быстрый просмотр: py imggen.py -> cheque_preview.png
    png = make_cheque_image("STARS", "100", "K7QX-M92T-4LPD")
    with open("cheque_preview.png", "wb") as f:
        f.write(png)
    print("cheque_preview.png записан,", len(png), "байт")
