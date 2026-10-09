import os
import math
from PIL import Image, ImageDraw, ImageFilter

SVG_CONTENT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%" fill="none">
  <defs>
    <!-- Background Gradient -->
    <linearGradient id="osint-bg-grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0a0d14"/>
      <stop offset="50%" stop-color="#0e131f"/>
      <stop offset="100%" stop-color="#080a10"/>
    </linearGradient>

    <!-- Border Gradient -->
    <linearGradient id="osint-border-grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#06b6d4" stop-opacity="0.85"/>
      <stop offset="50%" stop-color="#3b82f6" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="#06b6d4" stop-opacity="0.75"/>
    </linearGradient>

    <!-- Cyan Glow Gradient -->
    <linearGradient id="osint-cyan-grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#22d3ee"/>
      <stop offset="50%" stop-color="#06b6d4"/>
      <stop offset="100%" stop-color="#0284c7"/>
    </linearGradient>

    <!-- Radar Sweep Gradient -->
    <radialGradient id="osint-sweep-grad" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#06b6d4" stop-opacity="0.28"/>
      <stop offset="55%" stop-color="#06b6d4" stop-opacity="0.08"/>
      <stop offset="100%" stop-color="#06b6d4" stop-opacity="0"/>
    </radialGradient>

    <!-- Core Radial Flare -->
    <radialGradient id="osint-core-flare" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="35%" stop-color="#22d3ee"/>
      <stop offset="70%" stop-color="#0891b2" stop-opacity="0.8"/>
      <stop offset="100%" stop-color="#0891b2" stop-opacity="0"/>
    </radialGradient>

    <!-- Glow Filter -->
    <filter id="osint-glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="6" result="blur" />
      <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>
  </defs>

  <!-- Base Squircle Container -->
  <rect x="16" y="16" width="480" height="480" rx="112" fill="url(#osint-bg-grad)" stroke="url(#osint-border-grad)" stroke-width="8"/>

  <!-- Tactical Corner Telemetry Brackets -->
  <path d="M 54 114 L 54 54 L 114 54" stroke="#06b6d4" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" opacity="0.5"/>
  <path d="M 458 114 L 458 54 L 398 54" stroke="#06b6d4" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" opacity="0.5"/>
  <path d="M 54 398 L 54 458 L 114 458" stroke="#06b6d4" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" opacity="0.5"/>
  <path d="M 458 398 L 458 458 L 398 458" stroke="#06b6d4" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" opacity="0.5"/>

  <!-- Outer Radar Sweep Zone -->
  <circle cx="256" cy="256" r="184" fill="url(#osint-sweep-grad)"/>

  <!-- Outer Radar Ring -->
  <circle cx="256" cy="256" r="184" stroke="#06b6d4" stroke-width="6" stroke-opacity="0.4" stroke-dasharray="16 12"/>

  <!-- Middle Radar Ring -->
  <circle cx="256" cy="256" r="128" stroke="#22d3ee" stroke-width="7" stroke-opacity="0.65"/>

  <!-- Inner Reticle Ring -->
  <circle cx="256" cy="256" r="76" stroke="#38bdf8" stroke-width="6" stroke-opacity="0.85" stroke-dasharray="8 8"/>

  <!-- Cardinal Crosshair Ticks -->
  <line x1="256" y1="44" x2="256" y2="92" stroke="#22d3ee" stroke-width="8" stroke-linecap="round"/>
  <line x1="256" y1="420" x2="256" y2="468" stroke="#22d3ee" stroke-width="8" stroke-linecap="round"/>
  <line x1="44" y1="256" x2="92" y2="256" stroke="#22d3ee" stroke-width="8" stroke-linecap="round"/>
  <line x1="420" y1="256" x2="468" y2="256" stroke="#22d3ee" stroke-width="8" stroke-linecap="round"/>

  <!-- Sub-cardinal Precision Ticks -->
  <line x1="256" y1="144" x2="256" y2="164" stroke="#06b6d4" stroke-width="5" stroke-linecap="round" opacity="0.75"/>
  <line x1="256" y1="348" x2="256" y2="368" stroke="#06b6d4" stroke-width="5" stroke-linecap="round" opacity="0.75"/>
  <line x1="144" y1="256" x2="164" y2="256" stroke="#06b6d4" stroke-width="5" stroke-linecap="round" opacity="0.75"/>
  <line x1="348" y1="256" x2="368" y2="256" stroke="#06b6d4" stroke-width="5" stroke-linecap="round" opacity="0.75"/>

  <!-- Radar Sector Sweep Beam -->
  <path d="M 256 256 L 386 126 A 184 184 0 0 1 436 210 Z" fill="#06b6d4" fill-opacity="0.2"/>

  <!-- OSINT Surveillance Eye / Aperture Lens Arcs -->
  <path d="M 166 256 C 196 204 316 204 346 256 C 316 308 196 308 166 256 Z" 
        stroke="url(#osint-cyan-grad)" stroke-width="9" stroke-linecap="round" stroke-linejoin="round" fill="#0c1322" fill-opacity="0.88" filter="url(#osint-glow)"/>

  <!-- Center Core Signal Glow & Target Beacon -->
  <circle cx="256" cy="256" r="42" fill="url(#osint-core-flare)"/>
  <circle cx="256" cy="256" r="22" fill="#06b6d4" stroke="#ffffff" stroke-width="5"/>
  <circle cx="256" cy="256" r="8" fill="#ffffff"/>

  <!-- Satellite / Target Intelligence Blips -->
  <!-- Target 1 (North-East - Active Intercept) -->
  <circle cx="372" cy="170" r="9" fill="#10b981"/>
  <circle cx="372" cy="170" r="16" stroke="#10b981" stroke-width="3" stroke-opacity="0.6"/>

  <!-- Target 2 (South-West - Geopolitical Event) -->
  <circle cx="152" cy="334" r="7" fill="#f59e0b"/>
  <circle cx="152" cy="334" r="13" stroke="#f59e0b" stroke-width="3" stroke-opacity="0.5"/>
</svg>
"""

MANIFEST_CONTENT = """{
  "name": "Real-time OSINT Gathering Service",
  "short_name": "OSINT Realtime",
  "description": "Production-grade real-time OSINT intelligence dashboard with multi-source ingestion and Gemini/Groq AI processing.",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#090a0f",
  "theme_color": "#06b6d4",
  "icons": [
    {
      "src": "/favicon.ico",
      "sizes": "16x16 32x32 48x48",
      "type": "image/x-icon"
    },
    {
      "src": "/icon.svg",
      "sizes": "any",
      "type": "image/svg+xml"
    },
    {
      "src": "/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any maskable"
    },
    {
      "src": "/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any maskable"
    },
    {
      "src": "/apple-touch-icon.png",
      "sizes": "180x180",
      "type": "image/png"
    }
  ]
}
"""

def render_high_res_icon():
    # Render at 1024x1024 with supersampling, then downscale
    size = 1024
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Gradient background simulated via rounded rect
    # Base dark background
    draw.rounded_rectangle([32, 32, 992, 992], radius=224, fill=(12, 17, 28, 255), outline=(6, 182, 212, 210), width=16)

    # Inner subtle glow edge
    draw.rounded_rectangle([44, 44, 980, 980], radius=212, fill=None, outline=(59, 130, 246, 80), width=6)

    cx, cy = 512, 512

    # Corner brackets (NW, NE, SW, SE)
    bracket_w = 12
    bracket_col = (6, 182, 212, 140)
    # NW
    draw.line([(108, 228), (108, 108), (228, 108)], fill=bracket_col, width=bracket_w)
    # NE
    draw.line([(916, 228), (916, 108), (796, 108)], fill=bracket_col, width=bracket_w)
    # SW
    draw.line([(108, 796), (108, 916), (228, 916)], fill=bracket_col, width=bracket_w)
    # SE
    draw.line([(916, 796), (916, 916), (796, 916)], fill=bracket_col, width=bracket_w)

    # Radar sweep tint circle
    r_sweep = 368
    draw.ellipse([cx - r_sweep, cy - r_sweep, cx + r_sweep, cy + r_sweep], fill=(6, 182, 212, 22))

    # Radar Sweep Sector
    draw.pieslice([cx - r_sweep, cy - r_sweep, cx + r_sweep, cy + r_sweep], start=-45, end=15, fill=(6, 182, 212, 50))

    # Outer Radar Ring (dashed simulation)
    outer_r = 368
    num_dashes = 48
    for i in range(num_dashes):
        if i % 2 == 0:
            ang_start = i * (360 / num_dashes)
            ang_end = ang_start + (180 / num_dashes)
            draw.arc([cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r],
                     start=ang_start, end=ang_end, fill=(6, 182, 212, 110), width=12)

    # Middle Radar Ring
    mid_r = 256
    draw.ellipse([cx - mid_r, cy - mid_r, cx + mid_r, cy + mid_r],
                 fill=None, outline=(34, 211, 238, 175), width=14)

    # Inner Radar Ring (dotted)
    inner_r = 152
    num_inner = 36
    for i in range(num_inner):
        if i % 2 == 0:
            ang_start = i * (360 / num_inner)
            ang_end = ang_start + (180 / num_inner)
            draw.arc([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
                     start=ang_start, end=ang_end, fill=(56, 189, 248, 220), width=12)

    # Cardinal Crosshair Ticks (North, South, East, West)
    tick_w = 16
    tick_col = (34, 211, 238, 255)
    # North
    draw.line([(cx, 88), (cx, 184)], fill=tick_col, width=tick_w)
    # South
    draw.line([(cx, 840), (cx, 936)], fill=tick_col, width=tick_w)
    # West
    draw.line([(88, cy), (184, cy)], fill=tick_col, width=tick_w)
    # East
    draw.line([(840, cy), (936, cy)], fill=tick_col, width=tick_w)

    # Sub-cardinal precision ticks
    sub_w = 10
    sub_col = (6, 182, 212, 180)
    draw.line([(cx, 288), (cx, 328)], fill=sub_col, width=sub_w)
    draw.line([(cx, 696), (cx, 736)], fill=sub_col, width=sub_w)
    draw.line([(288, cy), (328, cy)], fill=sub_col, width=sub_w)
    draw.line([(696, cy), (736, cy)], fill=sub_col, width=sub_w)

    # Stylized OSINT Aperture / Eye
    # Curved path from (332, 512) up through (512, 408) to (692, 512) and down through (512, 616)
    eye_pts_top = []
    eye_pts_bot = []
    steps = 40
    for s in range(steps + 1):
        t = s / steps
        # Parabolic arc
        x = 332 + t * (692 - 332)
        # y goes from 512 -> 408 -> 512
        y_top = 512 - math.sin(t * math.pi) * 104
        y_bot = 512 + math.sin(t * math.pi) * 104
        eye_pts_top.append((x, y_top))
        eye_pts_bot.append((x, y_bot))

    eye_poly = eye_pts_top + eye_pts_bot[::-1]
    draw.polygon(eye_poly, fill=(12, 19, 34, 230), outline=(6, 182, 212, 255))
    # Eye border stroke
    for i in range(len(eye_pts_top) - 1):
        draw.line([eye_pts_top[i], eye_pts_top[i+1]], fill=(34, 211, 238, 255), width=18)
    for i in range(len(eye_pts_bot) - 1):
        draw.line([eye_pts_bot[i], eye_pts_bot[i+1]], fill=(34, 211, 238, 255), width=18)

    # Core Flare (Radial)
    flare_r = 84
    draw.ellipse([cx - flare_r, cy - flare_r, cx + flare_r, cy + flare_r], fill=(34, 211, 238, 80))
    draw.ellipse([cx - 44, cy - 44, cx + 44, cy + 44], fill=(6, 182, 212, 255), outline=(255, 255, 255, 255), width=10)
    draw.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=(255, 255, 255, 255))

    # Satellite Target 1 (NE - Green live intercept)
    t1_x, t1_y = 744, 340
    draw.ellipse([t1_x - 32, t1_y - 32, t1_x + 32, t1_y + 32], outline=(16, 185, 129, 160), width=6)
    draw.ellipse([t1_x - 18, t1_y - 18, t1_x + 18, t1_y + 18], fill=(16, 185, 129, 255))

    # Satellite Target 2 (SW - Amber geopolitical alert)
    t2_x, t2_y = 304, 668
    draw.ellipse([t2_x - 26, t2_y - 26, t2_x + 26, t2_y + 26], outline=(245, 158, 11, 140), width=6)
    draw.ellipse([t2_x - 14, t2_y - 14, t2_x + 14, t2_y + 14], fill=(245, 158, 11, 255))

    return img

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    frontend_dir = os.path.join(base_dir, "frontend")
    public_dir = os.path.join(frontend_dir, "public")
    app_dir = os.path.join(frontend_dir, "src", "app")

    os.makedirs(public_dir, exist_ok=True)
    os.makedirs(app_dir, exist_ok=True)

    # 1. Write SVG icons
    svg_public = os.path.join(public_dir, "icon.svg")
    svg_app = os.path.join(app_dir, "icon.svg")
    with open(svg_public, "w", encoding="utf-8") as f:
        f.write(SVG_CONTENT)
    with open(svg_app, "w", encoding="utf-8") as f:
        f.write(SVG_CONTENT)
    print("Created icon.svg in public/ and src/app/")

    # 2. Write site.webmanifest
    manifest_path = os.path.join(public_dir, "site.webmanifest")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(MANIFEST_CONTENT)
    print("Created site.webmanifest in public/")

    # 3. Render High-Res Raster Graphics
    print("Rendering high-res base image...")
    base_img = render_high_res_icon()

    # 512x512
    img_512 = base_img.resize((512, 512), Image.Resampling.LANCZOS)
    png_512 = os.path.join(public_dir, "icon-512.png")
    img_512.save(png_512, "PNG")
    print(f"Created {png_512}")

    # 192x192
    img_192 = base_img.resize((192, 192), Image.Resampling.LANCZOS)
    png_192 = os.path.join(public_dir, "icon-192.png")
    img_192.save(png_192, "PNG")
    print(f"Created {png_192}")

    # Apple Touch Icon (180x180)
    img_180 = base_img.resize((180, 180), Image.Resampling.LANCZOS)
    apple_public = os.path.join(public_dir, "apple-touch-icon.png")
    apple_app = os.path.join(app_dir, "apple-icon.png")
    img_180.save(apple_public, "PNG")
    img_180.save(apple_app, "PNG")
    print(f"Created apple-touch-icon.png")

    # Favicon.ico with multiple sizes (16, 32, 48, 64)
    ico_public = os.path.join(public_dir, "favicon.ico")
    ico_app = os.path.join(app_dir, "favicon.ico")
    base_img.save(
        ico_public,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    base_img.save(
        ico_app,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    print("Created favicon.ico in public/ and src/app/")

if __name__ == "__main__":
    main()
