from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def main():
    out_dir = Path(__file__).resolve().parent / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    size = 1024

    img = Image.new("RGB", (size, size), (9, 14, 26))
    draw = ImageDraw.Draw(img)

    for y in range(size):
        t = y / size
        draw.line((0, y, size, y), fill=(8, int(18 + 30 * t), int(30 + 56 * t)))

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse((170, 120, 860, 810), fill=(58, 208, 255, 54))
    gdraw.ellipse((300, 250, 980, 930), fill=(255, 145, 70, 18))
    glow = glow.filter(ImageFilter.GaussianBlur(40))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")

    draw = ImageDraw.Draw(img)

    # headset silhouette
    draw.ellipse((208, 176, 818, 786), fill=(12, 26, 42))
    draw.ellipse((285, 240, 742, 698), fill=(16, 34, 56))

    # hoodie and face block
    draw.rounded_rectangle((268, 320, 756, 922), radius=120, fill=(17, 33, 51))
    draw.rounded_rectangle((344, 290, 680, 690), radius=120, fill=(76, 157, 196))
    draw.polygon([(320, 370), (512, 750), (704, 370)], fill=(22, 43, 67))

    # simplified face
    draw.ellipse((368, 286, 654, 596), fill=(82, 176, 214))
    draw.rectangle((388, 432, 634, 570), fill=(82, 176, 214))
    draw.rectangle((450, 558, 570, 650), fill=(70, 154, 194))

    # hair
    draw.polygon(
        [
            (352, 360), (392, 272), (486, 236), (610, 256), (676, 328),
            (640, 348), (614, 324), (566, 340), (520, 320), (470, 356), (418, 350)
        ],
        fill=(20, 22, 31),
    )

    # eyes
    draw.ellipse((430, 410, 484, 444), fill=(230, 246, 255))
    draw.ellipse((540, 410, 594, 444), fill=(230, 246, 255))
    draw.ellipse((448, 419, 472, 442), fill=(10, 18, 30))
    draw.ellipse((558, 419, 582, 442), fill=(10, 18, 30))

    # mouth / nose
    draw.line((514, 460, 506, 506), fill=(36, 90, 118), width=5)
    draw.line((472, 540, 560, 540), fill=(31, 74, 97), width=6)

    # headset cups
    draw.ellipse((184, 350, 340, 560), fill=(18, 27, 37))
    draw.ellipse((684, 350, 840, 560), fill=(18, 27, 37))
    draw.ellipse((210, 376, 314, 534), outline=(255, 146, 73), width=18)
    draw.ellipse((710, 376, 814, 534), outline=(255, 146, 73), width=18)

    # mic
    draw.line((700, 546, 612, 646), fill=(42, 72, 94), width=12)
    draw.ellipse((588, 630, 630, 672), fill=(26, 48, 66))

    # hoodie details
    draw.line((430, 714, 430, 830), fill=(33, 59, 82), width=10)
    draw.line((596, 714, 596, 830), fill=(33, 59, 82), width=10)
    draw.ellipse((414, 700, 446, 732), fill=(33, 59, 82))
    draw.ellipse((580, 700, 612, 732), fill=(33, 59, 82))

    # subtle ring frame for circular crop
    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rdraw = ImageDraw.Draw(ring)
    rdraw.ellipse((36, 36, 988, 988), outline=(92, 224, 255, 72), width=14)
    rdraw.ellipse((58, 58, 966, 966), outline=(255, 160, 86, 28), width=6)
    ring = ring.filter(ImageFilter.GaussianBlur(1))
    img = Image.alpha_composite(img.convert("RGBA"), ring).convert("RGB")

    out_path = out_dir / "profile_icon.png"
    img.save(out_path)
    print(out_path)


if __name__ == "__main__":
    main()
