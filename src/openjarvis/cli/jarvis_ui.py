"""J.A.R.V.I.S. visual interface — particle cluster + floating command menu."""

from __future__ import annotations

import math
import queue
import random
import threading
import time
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple


class JarvisState(Enum):
    IDLE = auto()
    LISTENING = auto()
    THINKING = auto()
    SPEAKING = auto()


# Particle / face colours
_BG = (6, 8, 12)
_CYAN = (0, 200, 255)
_CYAN_DIM = (0, 60, 100)
_CYAN_GLOW = (0, 140, 200)
_AMBER = (255, 180, 40)
_BLUE_BRIGHT = (80, 180, 255)
_TEXT_DIM = (80, 100, 120)

# iOS-style menu palette — translucent dark-glass with SF-Blue accents
_ACCENT = (10, 132, 255)            # SF System Blue
_ACCENT_SOFT = (50, 150, 255)
_GLASS_FILL = (18, 22, 32, 215)     # dark translucent panel body
_GLASS_FILL_TOP = (28, 34, 48, 215)  # slight lighter top for gradient
_GLASS_STROKE = (255, 255, 255, 28) # hairline highlight on top edge
_ITEM_BG = (255, 255, 255, 10)      # cell background (very faint)
_ITEM_BG_HOVER = (255, 255, 255, 32) # cell on hover
_TEXT_PRIMARY = (240, 242, 247)     # near-white
_TEXT_SECONDARY = (170, 178, 192)
_TEXT_TERTIARY = (120, 130, 148)
_SHADOW = (0, 0, 0, 70)

_PARTICLE_COUNT = 300


def _draw_rounded_shadow(
    surface, rect, radius: int, offset: int = 6, layers: int = 4
) -> None:
    """Draw a soft drop shadow under a rounded rectangle.

    Approximates a Gaussian blur by stacking several translucent rounded rects
    with increasing offset and decreasing alpha.
    """
    import pygame

    for i in range(layers, 0, -1):
        alpha = int(18 * (i / layers))
        grow = i * 2
        shadow_rect = pygame.Rect(
            rect.x - grow,
            rect.y - grow + offset,
            rect.w + grow * 2,
            rect.h + grow * 2,
        )
        shadow_surf = pygame.Surface(
            (shadow_rect.w, shadow_rect.h), pygame.SRCALPHA
        )
        pygame.draw.rect(
            shadow_surf,
            (0, 0, 0, alpha),
            shadow_surf.get_rect(),
            border_radius=radius + grow,
        )
        surface.blit(shadow_surf, shadow_rect.topleft)


def _draw_glass_panel(surface, rect, radius: int = 22) -> None:
    """Draw an iOS-style translucent glass panel with subtle gradient + border."""
    import pygame

    _draw_rounded_shadow(surface, rect, radius)

    panel = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)

    # Vertical gradient: lighter top → darker bottom
    top = _GLASS_FILL_TOP
    bot = _GLASS_FILL
    for y in range(rect.h):
        mix = y / max(rect.h - 1, 1)
        r = int(top[0] * (1 - mix) + bot[0] * mix)
        g = int(top[1] * (1 - mix) + bot[1] * mix)
        b = int(top[2] * (1 - mix) + bot[2] * mix)
        a = int(top[3] * (1 - mix) + bot[3] * mix)
        pygame.draw.line(panel, (r, g, b, a), (0, y), (rect.w, y))

    # Mask to rounded rect
    mask = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    pygame.draw.rect(
        mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius
    )
    panel.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)

    surface.blit(panel, rect.topleft)

    # Hairline highlight on top edge
    highlight = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    pygame.draw.rect(
        highlight,
        _GLASS_STROKE,
        highlight.get_rect(),
        width=1,
        border_radius=radius,
    )
    surface.blit(highlight, rect.topleft)


def _draw_rounded_fill(
    surface, rect, color, radius: int, alpha: int | None = None
) -> None:
    """Draw a rounded rect fill with optional alpha."""
    import pygame

    if alpha is None and len(color) == 3:
        pygame.draw.rect(surface, color, rect, border_radius=radius)
        return
    if alpha is None:
        alpha = color[3] if len(color) == 4 else 255
    rgb = color[:3]
    surf = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    pygame.draw.rect(
        surf, (*rgb, alpha), surf.get_rect(), border_radius=radius
    )
    surface.blit(surf, rect.topleft)

# -----------------------------------------------------------------------
# Command menu categories
# -----------------------------------------------------------------------
MENU_CATEGORIES: Dict[str, List[Tuple[str, str]]] = {
    "Lights": [
        ("All On", "turn on all lights"),
        ("All Off", "turn off all lights"),
        ("Desk Blue", "set desk to blue"),
        ("Desk Red", "set desk to red"),
        ("Gaming", "set desk to gaming"),
        ("Bedroom Off", "turn off bedroom lights"),
        ("Bedroom On", "turn on bedroom lights"),
        ("Lounge On", "turn on lounge lights"),
        ("Lounge Off", "turn off lounge lights"),
        ("Hallway On", "turn on hallway lights"),
        ("Dim All", "dim the lights"),
        ("Bright All", "brighten the lights"),
    ],
    "Music": [
        ("Play/Pause", "play pause music"),
        ("Next Track", "next song"),
        ("Prev Track", "previous song"),
        ("Volume Up", "volume up"),
        ("Volume Down", "volume down"),
        ("Mute", "mute"),
    ],
    "Sonos": [
        ("Play", "play sonos"),
        ("Pause", "pause sonos"),
        ("Next", "skip sonos"),
        ("Previous", "previous sonos"),
        ("Volume Up", "volume up sonos"),
        ("Volume Down", "volume down sonos"),
        ("Now Playing", "what is playing on sonos"),
        ("Favourites", "favourites sonos"),
        ("Play Bedroom", "play bedroom sonos"),
        ("Pause Bedroom", "pause bedroom sonos"),
        ("Play Lounge", "play lounge sonos"),
        ("Play Dining", "play dining room sonos"),
    ],
    "Macros": [
        ("Rust Profile", "activate rust"),
        ("Default Profile", "activate default"),
        ("Reload", "reload"),
        ("Inventory", "inventory"),
        ("Heal Up", "heal up"),
        ("Quick Med", "quick med"),
        ("Flashlight", "flashlight"),
        ("Crafting", "crafting"),
        ("Map", "map"),
    ],
    "Apps": [
        ("Discord", "launch discord"),
        ("Steam", "launch steam"),
        ("Rust (Game)", "launch rust"),
        ("Blender", "launch blender"),
        ("Spotify", "launch spotify"),
    ],
    "Weather": [
        ("Current", "what is the weather"),
        ("London", "weather in london"),
    ],
    "Crypto": [
        ("Top Coins", "top crypto"),
        ("Trending", "trending crypto"),
        ("Bitcoin", "bitcoin price"),
        ("Gainers", "biggest crypto gainers today"),
        ("Market Overview", "crypto market overview"),
    ],
    "Lutron": [
        ("List Devices", "list lutron devices"),
        ("All On", "turn on all lutron"),
        ("All Off", "turn off all lutron"),
        ("Raise Shades", "raise lutron shades"),
        ("Lower Shades", "lower lutron shades"),
    ],
    "Calendar": [
        ("Today", "what is my schedule today"),
        ("Tomorrow", "what is my schedule tomorrow"),
        ("This Week", "what is my schedule this week"),
        ("Login", "login to calendar"),
    ],
    "Code": [
        ("Flask TODO API", "build me a program that serves a Flask TODO API with SQLite"),
        ("CLI Weather", "write a script that shows the weather for a given city using wttr.in"),
        ("File Organiser", "write a script that organises my Downloads folder by file type"),
        ("Markdown to PDF", "write a script that converts a markdown file to PDF"),
        ("Port Scanner", "build me a program that is a simple TCP port scanner in Python"),
    ],
}


class _Particle:
    """A particle orbiting the centre at a varying radius."""

    __slots__ = (
        "angle", "radius", "base_radius", "speed", "size",
        "brightness", "drift", "phase",
    )

    def __init__(self, cx: float, cy: float, max_r: float) -> None:
        self.angle = random.uniform(0, 2 * math.pi)
        self.base_radius = random.gauss(max_r * 0.45, max_r * 0.18)
        self.base_radius = max(10.0, min(self.base_radius, max_r * 0.85))
        self.radius = self.base_radius
        self.speed = random.uniform(0.1, 0.6) * random.choice([-1, 1])
        self.size = random.uniform(1.0, 3.5)
        self.brightness = random.uniform(0.3, 1.0)
        self.drift = random.uniform(-0.3, 0.3)
        self.phase = random.uniform(0, 2 * math.pi)


class JarvisUI:
    """Pygame particle cluster window with floating command menu."""

    def __init__(self, width: int = 800, height: int = 800) -> None:
        self._width = width
        self._height = height
        self._state = JarvisState.IDLE
        self._energy = 0.0
        self._status_text = ""
        self._running = True
        self._ready = threading.Event()
        self._command_queue: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def set_state(self, state: JarvisState, status: str = "") -> None:
        self._state = state
        if status:
            self._status_text = status
        elif state == JarvisState.IDLE:
            self._status_text = "Ready"
        elif state == JarvisState.LISTENING:
            self._status_text = "Listening..."
        elif state == JarvisState.THINKING:
            self._status_text = "Thinking..."
        elif state == JarvisState.SPEAKING:
            self._status_text = "Speaking..."

    def set_energy(self, energy: float) -> None:
        self._energy = max(0.0, min(1.0, energy))

    def poll_command(self) -> Optional[str]:
        try:
            return self._command_queue.get_nowait()
        except queue.Empty:
            return None

    def post_command(self, text: str) -> None:
        """Inject a command into the main loop's queue (thread-safe)."""
        self._command_queue.put(text)

    def close(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=3)

    def _run(self) -> None:
        import pygame

        pygame.init()
        screen = pygame.display.set_mode((self._width, self._height))
        pygame.display.set_caption("J.A.R.V.I.S.")
        clock = pygame.time.Clock()

        # iOS-style typography — Segoe UI is Windows' closest equivalent to SF Pro.
        # pygame will fall back cleanly if the family is missing.
        font_status = pygame.font.SysFont("segoeui", 17)
        font_title = pygame.font.SysFont("segoeui", 12, bold=False)
        font_menu = pygame.font.SysFont("segoeui", 15)
        font_menu_bold = pygame.font.SysFont("segoeui", 15, bold=True)
        font_cat = pygame.font.SysFont("segoeui", 16, bold=True)
        font_header = pygame.font.SysFont("segoeui", 18, bold=True)

        cx = self._width / 2
        cy = self._height / 2
        unit = min(self._width, self._height) / 2
        max_r = unit * 0.85

        particles: List[_Particle] = [
            _Particle(cx, cy, max_r) for _ in range(_PARTICLE_COUNT)
        ]

        glow_surf = pygame.Surface((self._width, self._height), pygame.SRCALPHA)

        t_start = time.monotonic()
        smooth_energy = 0.0

        # Menu state
        menu_open = False
        active_category: Optional[str] = None
        scroll_offset = 0
        menu_anim = 0.0           # 0 = closed, 1 = fully open (lerped)
        sub_anim = 0.0            # 0 = grid visible, 1 = command panel visible
        hover_cat_idx = -1
        hover_cmd_idx = -1

        # Floating action button (FAB)
        btn_radius = 30
        btn_x = self._width - 56
        btn_y = self._height - 56

        # Category grid layout (iOS-size touch targets)
        cat_names = list(MENU_CATEGORIES.keys())
        cat_cols = 3
        cat_w = 150
        cat_h = 58
        cat_pad = 10
        grid_padding = 18         # padding inside the glass panel
        cat_rows = math.ceil(len(cat_names) / cat_cols)
        grid_w = cat_cols * (cat_w + cat_pad) - cat_pad + grid_padding * 2
        grid_h = cat_rows * (cat_h + cat_pad) - cat_pad + grid_padding * 2 + 30
        grid_x = self._width - grid_w - 36
        grid_y = self._height - grid_h - 110

        # Command list panel
        cmd_panel_w = 260
        cmd_item_h = 46
        cmd_pad = 6
        cmd_padding = 14

        self._ready.set()

        while self._running:
            mouse_pos = pygame.mouse.get_pos()
            mouse_clicked = False

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._running = False
                    break
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mouse_clicked = True
                elif event.type == pygame.MOUSEWHEEL:
                    if active_category:
                        scroll_offset -= event.y * 2

            if not self._running:
                break

            t = time.monotonic() - t_start
            dt = 1 / 60
            state = self._state
            smooth_energy += (self._energy - smooth_energy) * 0.25

            # --- Draw background + glow ---
            screen.fill(_BG)
            glow_surf.fill((0, 0, 0, 0))

            if state == JarvisState.IDLE:
                base_color = _CYAN_DIM
                speed_mult = 0.3
                radius_pulse = 0.0
                scatter = 0.0
                breathe = 8.0 * math.sin(t * 0.8)
            elif state == JarvisState.LISTENING:
                base_color = _CYAN
                speed_mult = 0.6 + smooth_energy * 1.5
                radius_pulse = smooth_energy * 50.0
                scatter = smooth_energy * 30.0
                breathe = 5.0 * math.sin(t * 1.5)
            elif state == JarvisState.THINKING:
                base_color = _AMBER
                speed_mult = 1.5 + 0.5 * math.sin(t * 2)
                radius_pulse = 15.0 * math.sin(t * 3)
                scatter = 10.0
                breathe = 10.0 * math.sin(t * 2.5)
            else:  # SPEAKING
                base_color = _BLUE_BRIGHT
                speed_mult = 0.8 + smooth_energy * 2.0
                radius_pulse = smooth_energy * 60.0
                scatter = smooth_energy * 20.0
                breathe = 12.0 * math.sin(t * 2)

            # Central glow behind the cluster
            glow_radius = int(max_r * 0.15 + smooth_energy * max_r * 0.1)
            for layer in range(5):
                r = glow_radius + layer * 20
                alpha = int(max(0, min(255, 25 - layer * 5)))
                color = (*base_color, alpha)
                pygame.draw.circle(glow_surf, color, (int(cx), int(cy)), r)
            screen.blit(glow_surf, (0, 0))

            # --- Update + draw orbital particles ---
            for p in particles:
                p.angle += p.speed * speed_mult * dt
                target_r = (
                    p.base_radius
                    + breathe
                    + radius_pulse * math.sin(p.phase + t * 2)
                    + scatter * math.sin(p.phase * 3 + t * 5)
                )
                p.radius += (target_r - p.radius) * 0.1
                drift_y = p.drift * math.sin(t * 0.5 + p.phase) * 10
                px = cx + p.radius * math.cos(p.angle)
                py = cy + p.radius * math.sin(p.angle) + drift_y

                if state == JarvisState.IDLE:
                    b = p.brightness * (0.3 + 0.2 * math.sin(t * 0.7 + p.phase))
                elif state == JarvisState.LISTENING:
                    b = p.brightness * (0.4 + smooth_energy * 0.6)
                elif state == JarvisState.THINKING:
                    chase = math.sin(t * 6 - p.angle * 2)
                    b = p.brightness * (0.2 + 0.6 * max(0, chase))
                else:  # SPEAKING
                    wave = math.sin(t * 4 + p.angle * 1.5)
                    b = p.brightness * (0.5 + smooth_energy * 0.5 * max(0, wave))

                b = max(0.05, min(1.0, b))

                color = (
                    int(base_color[0] * b),
                    int(base_color[1] * b),
                    int(base_color[2] * b),
                )

                size = p.size
                if state == JarvisState.SPEAKING:
                    size += smooth_energy * 2
                elif state == JarvisState.LISTENING:
                    size += smooth_energy * 1.5

                ix, iy = int(px), int(py)
                isize = max(1, int(size))
                if isize >= 2:
                    gc = (color[0] // 3, color[1] // 3, color[2] // 3)
                    pygame.draw.circle(screen, gc, (ix, iy), isize + 2)
                pygame.draw.circle(screen, color, (ix, iy), isize)
                if isize >= 3 and b > 0.6:
                    core = (
                        min(255, color[0] + 80),
                        min(255, color[1] + 80),
                        min(255, color[2] + 80),
                    )
                    pygame.draw.circle(screen, core, (ix, iy), max(1, isize // 2))

            # --- Status text ---
            status = self._status_text or "Ready"
            text_surface = font_status.render(status, True, _TEXT_DIM)
            text_rect = text_surface.get_rect(center=(self._width // 2, self._height - 50))
            screen.blit(text_surface, text_rect)

            # --- Title ---
            title_surface = font_title.render("J.A.R.V.I.S.", True, _CYAN_DIM)
            title_rect = title_surface.get_rect(center=(self._width // 2, 25))
            screen.blit(title_surface, title_rect)

            # ===========================================================
            # FLOATING MENU (iOS-style)
            # ===========================================================

            # --- Lerp animation values (ease out ~0.2s) ---
            target_menu = 1.0 if menu_open else 0.0
            menu_anim += (target_menu - menu_anim) * 0.22

            target_sub = 1.0 if (menu_open and active_category is not None) else 0.0
            sub_anim += (target_sub - sub_anim) * 0.25

            # --- FAB (Floating Action Button) ---
            btn_dist_sq = (mouse_pos[0] - btn_x) ** 2 + (mouse_pos[1] - btn_y) ** 2
            btn_hovered = btn_dist_sq < btn_radius ** 2
            btn_scale = 1.0 + (0.08 if btn_hovered else 0.0)
            btn_r = int(btn_radius * btn_scale)

            # Shadow under FAB
            for i in range(5, 0, -1):
                shadow_surf = pygame.Surface(
                    ((btn_r + i * 2) * 2, (btn_r + i * 2) * 2), pygame.SRCALPHA
                )
                pygame.draw.circle(
                    shadow_surf,
                    (0, 0, 0, int(22 * (i / 5))),
                    (btn_r + i * 2, btn_r + i * 2),
                    btn_r + i * 2,
                )
                screen.blit(
                    shadow_surf,
                    (btn_x - (btn_r + i * 2), btn_y - (btn_r + i * 2) + 4),
                )

            # FAB body (filled accent colour)
            fab_color = _ACCENT_SOFT if btn_hovered else _ACCENT
            pygame.draw.circle(screen, fab_color, (btn_x, btn_y), btn_r)
            # Inner highlight for depth
            pygame.draw.circle(
                screen, (255, 255, 255, 0),
                (btn_x, btn_y - btn_r // 4), btn_r // 3,
            )

            # Plus / X icon rotates as menu opens (0deg closed, 45deg open)
            icon_rot = menu_anim * math.pi / 4  # 0 to π/4
            arm_len = int(btn_r * 0.45)
            thickness = 3
            for offset_angle in (0, math.pi / 2):
                a = offset_angle + icon_rot
                dx = int(math.cos(a) * arm_len)
                dy = int(math.sin(a) * arm_len)
                pygame.draw.line(
                    screen, (255, 255, 255),
                    (btn_x - dx, btn_y - dy),
                    (btn_x + dx, btn_y + dy),
                    thickness,
                )

            if mouse_clicked and btn_hovered:
                menu_open = not menu_open
                active_category = None
                scroll_offset = 0
                mouse_clicked = False

            # Skip rendering panels when fully closed
            if menu_anim > 0.02:
                # Scale effect: panel grows from 0.92 -> 1.0 as it opens
                scale = 0.92 + menu_anim * 0.08
                global_alpha = int(255 * menu_anim)

                # Decide which panel is primary (grid or command list)
                show_grid = sub_anim < 0.5

                # =====================================================
                # CATEGORY GRID
                # =====================================================
                if show_grid:
                    # Compute scaled rect (centered around original)
                    sw = int(grid_w * scale)
                    sh = int(grid_h * scale)
                    sx = grid_x + (grid_w - sw) // 2
                    sy = grid_y + (grid_h - sh) // 2
                    panel_rect = pygame.Rect(sx, sy, sw, sh)

                    # Build the panel onto an offscreen surface so we can
                    # modulate its overall alpha for the open animation.
                    panel_canvas = pygame.Surface((sw, sh), pygame.SRCALPHA)
                    _draw_glass_panel(panel_canvas, panel_canvas.get_rect(), radius=22)

                    # Header text
                    header = font_header.render(
                        "Commands", True, _TEXT_PRIMARY
                    )
                    panel_canvas.blit(
                        header, (grid_padding, grid_padding - 4)
                    )

                    # Categories grid
                    new_hover_cat_idx = -1
                    for idx, cat in enumerate(cat_names):
                        row = idx // cat_cols
                        col = idx % cat_cols
                        rx = grid_padding + col * (cat_w + cat_pad)
                        ry = grid_padding + 28 + row * (cat_h + cat_pad)
                        rect = pygame.Rect(rx, ry, cat_w, cat_h)

                        # Convert panel-local rect to screen coords for hit test
                        screen_rect = pygame.Rect(sx + rx, sy + ry, cat_w, cat_h)
                        hovered = screen_rect.collidepoint(mouse_pos)
                        if hovered:
                            new_hover_cat_idx = idx

                        # Background — more opaque on hover
                        _draw_rounded_fill(
                            panel_canvas,
                            rect,
                            _ITEM_BG_HOVER if hovered else _ITEM_BG,
                            radius=14,
                        )
                        if hovered:
                            # Thin accent border on hover
                            accent_surf = pygame.Surface(
                                (rect.w, rect.h), pygame.SRCALPHA
                            )
                            pygame.draw.rect(
                                accent_surf,
                                (*_ACCENT, 180),
                                accent_surf.get_rect(),
                                width=2,
                                border_radius=14,
                            )
                            panel_canvas.blit(accent_surf, rect.topleft)

                        label_color = _TEXT_PRIMARY if hovered else _TEXT_SECONDARY
                        label = font_cat.render(cat, True, label_color)
                        label_rect = label.get_rect(center=rect.center)
                        panel_canvas.blit(label, label_rect)

                        if mouse_clicked and hovered:
                            active_category = cat
                            scroll_offset = 0
                            mouse_clicked = False

                    hover_cat_idx = new_hover_cat_idx

                    # Apply global alpha for fade-in
                    if global_alpha < 255:
                        panel_canvas.set_alpha(global_alpha)
                    screen.blit(panel_canvas, (sx, sy))

                # =====================================================
                # COMMAND LIST PANEL
                # =====================================================
                if not show_grid and active_category is not None:
                    commands = MENU_CATEGORIES.get(active_category, [])
                    panel_h = min(
                        len(commands) * (cmd_item_h + cmd_pad)
                        + 2 * cmd_padding + 50,
                        self._height - 160,
                    )
                    panel_x = self._width - cmd_panel_w - 36
                    panel_y = self._height - panel_h - 110

                    sw = int(cmd_panel_w * scale)
                    sh = int(panel_h * scale)
                    sx = panel_x + (cmd_panel_w - sw) // 2
                    sy = panel_y + (panel_h - sh) // 2

                    panel_canvas = pygame.Surface((sw, sh), pygame.SRCALPHA)
                    _draw_glass_panel(panel_canvas, panel_canvas.get_rect(), radius=22)

                    # Header with back chevron
                    header_rect_local = pygame.Rect(
                        0, 0, sw, 44
                    )
                    header_screen = pygame.Rect(sx, sy, sw, 44)
                    header_hovered = header_screen.collidepoint(mouse_pos)
                    chevron_color = _ACCENT if header_hovered else _TEXT_SECONDARY

                    # Chevron "<"
                    cx0 = cmd_padding + 6
                    cy0 = 22
                    pygame.draw.line(
                        panel_canvas, chevron_color,
                        (cx0 + 6, cy0 - 6), (cx0, cy0), 2
                    )
                    pygame.draw.line(
                        panel_canvas, chevron_color,
                        (cx0, cy0), (cx0 + 6, cy0 + 6), 2
                    )

                    back_label = font_menu.render(
                        "Back",
                        True,
                        _ACCENT if header_hovered else _TEXT_SECONDARY,
                    )
                    panel_canvas.blit(
                        back_label, (cmd_padding + 20, cy0 - back_label.get_height() // 2)
                    )

                    # Centered title
                    title = font_header.render(
                        active_category, True, _TEXT_PRIMARY
                    )
                    title_rect = title.get_rect(center=(sw // 2, 22))
                    panel_canvas.blit(title, title_rect)

                    # Separator under header
                    pygame.draw.line(
                        panel_canvas, (255, 255, 255, 20),
                        (cmd_padding, 44),
                        (sw - cmd_padding, 44),
                        1,
                    )

                    if mouse_clicked and header_hovered:
                        active_category = None
                        mouse_clicked = False

                    # Scrollable command list
                    max_scroll = max(
                        0,
                        len(commands) * (cmd_item_h + cmd_pad)
                        - (sh - 44 - cmd_padding),
                    )
                    scroll_offset = max(0, min(scroll_offset, max_scroll))

                    new_hover_cmd = -1
                    for i, (label_text, cmd_text) in enumerate(commands):
                        iy = 52 + i * (cmd_item_h + cmd_pad) - scroll_offset
                        if iy + cmd_item_h < 44 or iy > sh - cmd_padding // 2:
                            continue

                        item_rect = pygame.Rect(
                            cmd_padding, iy,
                            sw - cmd_padding * 2, cmd_item_h,
                        )
                        item_screen = pygame.Rect(
                            sx + cmd_padding, sy + iy,
                            sw - cmd_padding * 2, cmd_item_h,
                        )
                        item_hovered = item_screen.collidepoint(mouse_pos)
                        if item_hovered:
                            new_hover_cmd = i

                        _draw_rounded_fill(
                            panel_canvas,
                            item_rect,
                            _ITEM_BG_HOVER if item_hovered else _ITEM_BG,
                            radius=12,
                        )

                        # Label
                        label_color = (
                            _TEXT_PRIMARY if item_hovered else _TEXT_SECONDARY
                        )
                        txt = font_menu_bold.render(
                            label_text, True, label_color
                        )
                        panel_canvas.blit(
                            txt,
                            (item_rect.x + 16, item_rect.y + cmd_item_h // 2 - txt.get_height() // 2),
                        )

                        # Right chevron indicator
                        cx1 = item_rect.right - 14
                        cy1 = item_rect.y + cmd_item_h // 2
                        chev = _ACCENT if item_hovered else _TEXT_TERTIARY
                        pygame.draw.line(
                            panel_canvas, chev,
                            (cx1 - 4, cy1 - 4), (cx1, cy1), 2,
                        )
                        pygame.draw.line(
                            panel_canvas, chev,
                            (cx1, cy1), (cx1 - 4, cy1 + 4), 2,
                        )

                        if mouse_clicked and item_hovered:
                            self._command_queue.put(cmd_text)
                            menu_open = False
                            active_category = None
                            mouse_clicked = False

                    hover_cmd_idx = new_hover_cmd

                    if global_alpha < 255:
                        panel_canvas.set_alpha(global_alpha)
                    screen.blit(panel_canvas, (sx, sy))

                # =====================================================
                # Click outside to close
                # =====================================================
                if mouse_clicked:
                    if show_grid:
                        outside_rect = pygame.Rect(grid_x, grid_y, grid_w, grid_h)
                    else:
                        outside_rect = pygame.Rect(
                            self._width - cmd_panel_w - 36,
                            self._height - 300 - 110,
                            cmd_panel_w,
                            300,
                        )
                    if not outside_rect.collidepoint(mouse_pos) and not btn_hovered:
                        if show_grid:
                            menu_open = False
                        else:
                            active_category = None

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()


__all__ = ["JarvisState", "JarvisUI"]
