#!/usr/bin/env python3
"""
Generate Omniscrobble logo assets:
- icon.svg & icon-512.png, icon-192.png, favicon.png
- omniscrobble-banner.svg & omniscrobble-banner.png (README hero header)
- social-preview.png (1280x640 OpenGraph card)
"""

import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "docs" / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# 1. Core Square App Icon SVG (512x512)
ICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">
  <defs>
    <linearGradient id="arrowGradTop" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#0284c7" />
      <stop offset="100%" stop-color="#0369a1" />
    </linearGradient>
    <linearGradient id="arrowGradBottom" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#0284c7" />
      <stop offset="100%" stop-color="#0369a1" />
    </linearGradient>
    <linearGradient id="highlightGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#7dd3fc" />
      <stop offset="100%" stop-color="#0284c7" />
    </linearGradient>
    <linearGradient id="innerDiscGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#1e293b" />
      <stop offset="100%" stop-color="#0f172a" />
    </linearGradient>
    <filter id="subtleDrop" x="-15%" y="-15%" width="130%" height="130%">
      <feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="#000000" flood-opacity="0.4" />
    </filter>
  </defs>

  <g filter="url(#subtleDrop)">
    <!-- OUTER CIRCULAR SYNC LOOP -->
    <!-- Top-Right Clockwise Arrow -->
    <path d="M 405 285 C 418 200 360 98 256 98 C 190 98 135 135 110 188 L 86 160 L 98 238 L 174 220 L 148 194 C 168 152 210 126 256 126 C 340 126 388 206 376 280 Z" fill="url(#arrowGradTop)" />
    
    <!-- Bottom-Left Clockwise Arrow -->
    <path d="M 107 227 C 94 312 152 414 256 414 C 322 414 377 377 402 324 L 426 352 L 414 274 L 338 292 L 364 318 C 344 360 302 386 256 386 C 172 386 124 306 136 232 Z" fill="url(#arrowGradBottom)" />

    <!-- Motion Echo Arcs -->
    <path d="M 390 220 C 378 160 326 122 260 122" fill="none" stroke="url(#highlightGrad)" stroke-width="6" stroke-linecap="round" opacity="0.75" />
    <path d="M 122 292 C 134 352 186 390 252 390" fill="none" stroke="url(#highlightGrad)" stroke-width="6" stroke-linecap="round" opacity="0.75" />

    <!-- CENTER FILM REEL HOUSING -->
    <circle cx="256" cy="256" r="114" fill="url(#innerDiscGrad)" stroke="#334155" stroke-width="5" />

    <!-- Film Frame Body (Vertical Rounded Rectangle) -->
    <rect x="182" y="174" width="148" height="164" rx="22" fill="#1e293b" stroke="#475569" stroke-width="3.5" />

    <!-- Left Sprocket Perforations -->
    <rect x="193" y="188" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="217" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="247" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="277" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="306" width="14" height="18" rx="3.5" fill="#0f172a" />

    <!-- Right Sprocket Perforations -->
    <rect x="305" y="188" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="217" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="247" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="277" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="306" width="14" height="18" rx="3.5" fill="#0f172a" />

    <!-- Play Button Triangle -->
    <polygon points="242,216 296,256 242,296" fill="#f8fafc" stroke="#f8fafc" stroke-width="6" stroke-linejoin="round" />
  </g>
</svg>'''

# 2. Horizontal Header Banner SVG (880x220)
BANNER_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 880 220" width="100%" height="100%">
  <defs>
    <linearGradient id="cardBgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0b1120" />
      <stop offset="100%" stop-color="#0f172a" />
    </linearGradient>
    <linearGradient id="banArrowGradTop" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#0284c7" />
      <stop offset="100%" stop-color="#0369a1" />
    </linearGradient>
    <linearGradient id="banArrowGradBottom" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#0284c7" />
      <stop offset="100%" stop-color="#0369a1" />
    </linearGradient>
    <linearGradient id="banHighlightGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#7dd3fc" />
      <stop offset="100%" stop-color="#0284c7" />
    </linearGradient>
    <linearGradient id="banInnerDiscGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#1e293b" />
      <stop offset="100%" stop-color="#0f172a" />
    </linearGradient>
    <filter id="banDrop" x="-15%" y="-15%" width="130%" height="130%">
      <feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#000000" flood-opacity="0.35" />
    </filter>
  </defs>

  <!-- Container Card -->
  <rect x="2" y="2" width="876" height="216" rx="20" fill="url(#cardBgGrad)" stroke="#1e293b" stroke-width="2" />

  <g transform="translate(30, 10)">
    <!-- ICON GROUP (Scaled to ~200x200) -->
    <g transform="translate(10, 0) scale(0.39)" filter="url(#banDrop)">
      <path d="M 405 285 C 418 200 360 98 256 98 C 190 98 135 135 110 188 L 86 160 L 98 238 L 174 220 L 148 194 C 168 152 210 126 256 126 C 340 126 388 206 376 280 Z" fill="url(#banArrowGradTop)" />
      <path d="M 107 227 C 94 312 152 414 256 414 C 322 414 377 377 402 324 L 426 352 L 414 274 L 338 292 L 364 318 C 344 360 302 386 256 386 C 172 386 124 306 136 232 Z" fill="url(#banArrowGradBottom)" />
      <path d="M 390 220 C 378 160 326 122 260 122" fill="none" stroke="url(#banHighlightGrad)" stroke-width="6" stroke-linecap="round" opacity="0.75" />
      <path d="M 122 292 C 134 352 186 390 252 390" fill="none" stroke="url(#banHighlightGrad)" stroke-width="6" stroke-linecap="round" opacity="0.75" />
      <circle cx="256" cy="256" r="114" fill="url(#banInnerDiscGrad)" stroke="#334155" stroke-width="5" />
      <rect x="182" y="174" width="148" height="164" rx="22" fill="#1e293b" stroke="#475569" stroke-width="3.5" />
      <rect x="193" y="188" width="14" height="18" rx="3.5" fill="#0f172a" />
      <rect x="193" y="217" width="14" height="18" rx="3.5" fill="#0f172a" />
      <rect x="193" y="247" width="14" height="18" rx="3.5" fill="#0f172a" />
      <rect x="193" y="277" width="14" height="18" rx="3.5" fill="#0f172a" />
      <rect x="193" y="306" width="14" height="18" rx="3.5" fill="#0f172a" />
      <rect x="305" y="188" width="14" height="18" rx="3.5" fill="#0f172a" />
      <rect x="305" y="217" width="14" height="18" rx="3.5" fill="#0f172a" />
      <rect x="305" y="247" width="14" height="18" rx="3.5" fill="#0f172a" />
      <rect x="305" y="277" width="14" height="18" rx="3.5" fill="#0f172a" />
      <rect x="305" y="306" width="14" height="18" rx="3.5" fill="#0f172a" />
      <polygon points="242,216 296,256 242,296" fill="#f8fafc" stroke="#f8fafc" stroke-width="6" stroke-linejoin="round" />
    </g>

    <!-- TYPOGRAPHY -->
    <text x="240" y="90" font-family="'Inter', 'Fira Sans', 'DejaVu Sans', system-ui, sans-serif" font-size="52" font-weight="900" letter-spacing="3" fill="#f8fafc">OMNISCROBBLE</text>
    <text x="242" y="132" font-family="'Inter', 'Fira Sans', 'DejaVu Sans', system-ui, sans-serif" font-size="22" font-weight="600" fill="#38bdf8">Watch anywhere. Track everywhere.</text>
    <text x="242" y="165" font-family="'Inter', 'Fira Sans', 'DejaVu Sans', system-ui, sans-serif" font-size="14" font-weight="500" fill="#94a3b8">Universal Webhook Bridge &amp; Multi-Tracker Scrobbler</text>
  </g>
</svg>'''

# 3. Social Preview / OpenGraph Card (1280x640)
SOCIAL_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 640" width="100%" height="100%">
  <defs>
    <linearGradient id="bgCanvasGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#090d16" />
      <stop offset="50%" stop-color="#0f172a" />
      <stop offset="100%" stop-color="#1e1b4b" />
    </linearGradient>
    <linearGradient id="cardArrowGradTop" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#0284c7" />
      <stop offset="100%" stop-color="#0369a1" />
    </linearGradient>
    <linearGradient id="cardArrowGradBottom" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#0284c7" />
      <stop offset="100%" stop-color="#0369a1" />
    </linearGradient>
    <linearGradient id="cardHighlightGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#7dd3fc" />
      <stop offset="100%" stop-color="#0284c7" />
    </linearGradient>
    <linearGradient id="cardInnerDiscGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#1e293b" />
      <stop offset="100%" stop-color="#0f172a" />
    </linearGradient>
    <filter id="bigGlow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="16" stdDeviation="24" flood-color="#0284c7" flood-opacity="0.3" />
    </filter>
  </defs>

  <!-- Background -->
  <rect width="1280" height="640" fill="url(#bgCanvasGrad)" />
  
  <!-- Subtle decorative radial background circle -->
  <circle cx="280" cy="320" r="280" fill="#0284c7" opacity="0.08" />

  <!-- Left Icon -->
  <g transform="translate(130, 160) scale(0.62)" filter="url(#bigGlow)">
    <path d="M 405 285 C 418 200 360 98 256 98 C 190 98 135 135 110 188 L 86 160 L 98 238 L 174 220 L 148 194 C 168 152 210 126 256 126 C 340 126 388 206 376 280 Z" fill="url(#cardArrowGradTop)" />
    <path d="M 107 227 C 94 312 152 414 256 414 C 322 414 377 377 402 324 L 426 352 L 414 274 L 338 292 L 364 318 C 344 360 302 386 256 386 C 172 386 124 306 136 232 Z" fill="url(#cardArrowGradBottom)" />
    <path d="M 390 220 C 378 160 326 122 260 122" fill="none" stroke="url(#cardHighlightGrad)" stroke-width="6" stroke-linecap="round" opacity="0.75" />
    <path d="M 122 292 C 134 352 186 390 252 390" fill="none" stroke="url(#cardHighlightGrad)" stroke-width="6" stroke-linecap="round" opacity="0.75" />
    <circle cx="256" cy="256" r="114" fill="url(#cardInnerDiscGrad)" stroke="#334155" stroke-width="5" />
    <rect x="182" y="174" width="148" height="164" rx="22" fill="#1e293b" stroke="#475569" stroke-width="3.5" />
    <rect x="193" y="188" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="217" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="247" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="277" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="193" y="306" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="188" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="217" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="247" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="277" width="14" height="18" rx="3.5" fill="#0f172a" />
    <rect x="305" y="306" width="14" height="18" rx="3.5" fill="#0f172a" />
    <polygon points="242,216 296,256 242,296" fill="#f8fafc" stroke="#f8fafc" stroke-width="6" stroke-linejoin="round" />
  </g>

  <!-- Right Typography & Badges -->
  <g transform="translate(500, 210)">
    <text x="0" y="70" font-family="'Inter', 'Fira Sans', 'DejaVu Sans', system-ui, sans-serif" font-size="64" font-weight="900" letter-spacing="4" fill="#f8fafc">OMNISCROBBLE</text>
    <text x="0" y="125" font-family="'Inter', 'Fira Sans', 'DejaVu Sans', system-ui, sans-serif" font-size="28" font-weight="600" fill="#38bdf8">Watch anywhere. Track everywhere.</text>
    <text x="0" y="170" font-family="'Inter', 'Fira Sans', 'DejaVu Sans', system-ui, sans-serif" font-size="18" font-weight="500" fill="#94a3b8">Universal Webhook Bridge &amp; Multi-Tracker Scrobbler</text>
    
    <!-- Platform Pills -->
    <g transform="translate(0, 205)">
      <rect x="0" y="0" width="70" height="30" rx="15" fill="#1e293b" stroke="#334155" />
      <text x="35" y="20" font-family="'Inter', sans-serif" font-size="12" font-weight="700" fill="#e2e8f0" text-anchor="middle">Plex</text>

      <rect x="78" y="0" width="82" height="30" rx="15" fill="#3b0764" stroke="#7e22ce" />
      <text x="119" y="20" font-family="'Inter', sans-serif" font-size="12" font-weight="700" fill="#d8b4fe" text-anchor="middle">Jellyfin</text>

      <rect x="168" y="0" width="74" height="30" rx="15" fill="#064e3b" stroke="#059669" />
      <text x="205" y="20" font-family="'Inter', sans-serif" font-size="12" font-weight="700" fill="#a7f3d0" text-anchor="middle">Emby</text>

      <text x="256" y="21" font-family="'Inter', sans-serif" font-size="16" font-weight="700" fill="#94a3b8">&#8594;</text>

      <rect x="278" y="0" width="74" height="30" rx="15" fill="#7f1d1d" stroke="#dc2626" />
      <text x="315" y="20" font-family="'Inter', sans-serif" font-size="12" font-weight="700" fill="#fca5a5" text-anchor="middle">Trakt</text>

      <rect x="360" y="0" width="74" height="30" rx="15" fill="#0c4a6e" stroke="#0284c7" />
      <text x="397" y="20" font-family="'Inter', sans-serif" font-size="12" font-weight="700" fill="#38bdf8" text-anchor="middle">Simkl</text>

      <rect x="442" y="0" width="80" height="30" rx="15" fill="#042f2e" stroke="#0d9488" />
      <text x="482" y="20" font-family="'Inter', sans-serif" font-size="12" font-weight="700" fill="#2dd4bf" text-anchor="middle">AniList</text>

      <rect x="530" y="0" width="68" height="30" rx="15" fill="#172554" stroke="#2563eb" />
      <text x="564" y="20" font-family="'Inter', sans-serif" font-size="12" font-weight="700" fill="#93c5fd" text-anchor="middle">MAL</text>
    </g>
  </g>
</svg>'''

def main():
    print("Generating SVG files...")
    icon_svg_path = ASSETS_DIR / "icon.svg"
    banner_svg_path = ASSETS_DIR / "banner.svg"
    social_svg_path = ASSETS_DIR / "social-preview.svg"

    icon_svg_path.write_text(ICON_SVG, encoding="utf-8")
    banner_svg_path.write_text(BANNER_SVG, encoding="utf-8")
    social_svg_path.write_text(SOCIAL_SVG, encoding="utf-8")

    print("Rendering PNGs with rsvg-convert...")
    subprocess.run(["rsvg-convert", "-w", "512", "-h", "512", str(icon_svg_path), "-o", str(ASSETS_DIR / "icon-512.png")], check=True)
    subprocess.run(["rsvg-convert", "-w", "192", "-h", "192", str(icon_svg_path), "-o", str(ASSETS_DIR / "icon-192.png")], check=True)
    subprocess.run(["rsvg-convert", "-w", "64", "-h", "64", str(icon_svg_path), "-o", str(ASSETS_DIR / "favicon.png")], check=True)
    subprocess.run(["rsvg-convert", "-w", "880", "-h", "220", str(banner_svg_path), "-o", str(ASSETS_DIR / "banner.png")], check=True)
    subprocess.run(["rsvg-convert", "-w", "1280", "-h", "640", str(social_svg_path), "-o", str(ASSETS_DIR / "social-preview.png")], check=True)

    # Generate multi-size favicon.ico with ImageMagick
    subprocess.run(["magick", str(ASSETS_DIR / "favicon.png"), str(ASSETS_DIR / "favicon.ico")], check=True)

    print("All assets successfully generated in docs/assets/!")

if __name__ == "__main__":
    main()
