"""
Icon generation script for MediRoute.
Reads the source icon, resizes to all required sizes, and writes:
  - frontend/public/icons/  (PWA + web)
  - frontend/android/app/src/main/res/mipmap-*/  (Android)
  - frontend/public/favicon.ico
"""

import sys
import shutil
from pathlib import Path
from PIL import Image

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("source_icon.png")
FRONTEND = Path("C:/Users/mv250058/Videos/MediRoute/MediRoute/frontend")
ICONS_DIR = FRONTEND / "public" / "icons"
ICONS_DIR.mkdir(parents=True, exist_ok=True)

img = Image.open(SRC).convert("RGBA")

# ── Ensure square ──────────────────────────────────────────────────────────────
w, h = img.size
if w != h:
    side = max(w, h)
    bg = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    bg.paste(img, ((side - w) // 2, (side - h) // 2))
    img = bg

# ── PWA / web icon sizes ───────────────────────────────────────────────────────
web_sizes = {
    "icon-48.png":   48,
    "icon-72.png":   72,
    "icon-96.png":   96,
    "icon-144.png":  144,
    "icon-192.png":  192,
    "icon-512.png":  512,
    "icon-1024.png": 1024,
}

for name, size in web_sizes.items():
    resized = img.resize((size, size), Image.LANCZOS)
    out = ICONS_DIR / name
    resized.save(out, "PNG", optimize=True)
    print(f"  ✓ {out}")

# Maskable icon (512) — add 10% safe-zone padding with white bg
maskable_size = 512
padding = int(maskable_size * 0.10)
inner = maskable_size - 2 * padding
bg = Image.new("RGBA", (maskable_size, maskable_size), (255, 255, 255, 255))
resized_inner = img.resize((inner, inner), Image.LANCZOS)
bg.paste(resized_inner, (padding, padding), resized_inner)
maskable_path = ICONS_DIR / "icon-512-maskable.png"
bg.save(maskable_path, "PNG", optimize=True)
print(f"  ✓ {maskable_path}")

# ── favicon.ico (multi-size) ───────────────────────────────────────────────────
favicon_path = FRONTEND / "public" / "favicon.ico"
ico_imgs = [img.resize((s, s), Image.LANCZOS).convert("RGBA") for s in [16, 32, 48]]
ico_imgs[0].save(
    favicon_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)],
    append_images=ico_imgs[1:]
)
print(f"  ✓ {favicon_path}")

# Also write a 32x32 PNG favicon
png_fav = ICONS_DIR / "favicon-32.png"
img.resize((32, 32), Image.LANCZOS).save(png_fav, "PNG")
print(f"  ✓ {png_fav}")

# ── Android mipmap sizes ───────────────────────────────────────────────────────
android_res = FRONTEND / "android" / "app" / "src" / "main" / "res"

# Legacy icon sizes (px) per density bucket
mipmap_sizes = {
    "mipmap-mdpi":    48,
    "mipmap-hdpi":    72,
    "mipmap-xhdpi":   96,
    "mipmap-xxhdpi":  144,
    "mipmap-xxxhdpi": 192,
}

# Adaptive icon foreground canvas sizes (108dp × density)
# Icon art is scaled to 80 % of canvas; the remaining 20 % is transparent safe zone.
adaptive_canvas = {
    "mipmap-mdpi":    108,
    "mipmap-hdpi":    162,
    "mipmap-xhdpi":   216,
    "mipmap-xxhdpi":  324,
    "mipmap-xxxhdpi": 432,
}

for folder, size in mipmap_sizes.items():
    mipmap_dir = android_res / folder
    if not mipmap_dir.exists():
        print(f"  ⚠ Skipping {folder} (directory not found)")
        continue

    resized = img.resize((size, size), Image.LANCZOS)

    # ic_launcher.png — legacy launcher icon
    launcher = mipmap_dir / "ic_launcher.png"
    resized.save(launcher, "PNG")
    print(f"  ✓ {launcher}")

    # ic_launcher_round.png — circular launcher icon (Android 7.1+)
    round_launcher = mipmap_dir / "ic_launcher_round.png"
    resized.save(round_launcher, "PNG")
    print(f"  ✓ {round_launcher}")

    # ic_launcher_foreground.png — adaptive icon foreground layer (API 26+)
    # Canvas = 108dp × density; icon art fills 80 % with 10 % transparent padding all around.
    canvas_px = adaptive_canvas[folder]
    inner_px  = int(canvas_px * 0.80)
    offset    = (canvas_px - inner_px) // 2
    foreground = Image.new("RGBA", (canvas_px, canvas_px), (0, 0, 0, 0))
    art = img.resize((inner_px, inner_px), Image.LANCZOS)
    foreground.paste(art, (offset, offset), art)
    fg_path = mipmap_dir / "ic_launcher_foreground.png"
    foreground.save(fg_path, "PNG", optimize=True)
    print(f"  ✓ {fg_path}")

# Copy 1024 as Play Store icon
play_store_dir = FRONTEND / "public" / "icons"
play_store_icon = play_store_dir / "play-store-icon-1024.png"
img.resize((1024, 1024), Image.LANCZOS).save(play_store_icon, "PNG", optimize=True)
print(f"  ✓ {play_store_icon}  ← Upload this to Play Console")

print("\n✅ All icons generated successfully!")
