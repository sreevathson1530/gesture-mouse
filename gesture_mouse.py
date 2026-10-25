"""Gesture Mouse - control the cursor with hand gestures seen by a webcam.

Runs as an always-on background agent with a system tray icon. Gestures
    index finger ............ move the cursor
    thumb + index pinch ..... left click (release quickly)
    thumb + index hold ...... click and drag, release pinch to drop
    thumb + middle pinch .... right click
    index + middle up ....... scroll (move hand up/down)
    closed fist ............. pause / resume tracking

Tray menu: pause/resume, show/hide the camera preview, quit. Right-click the
icon for the menu, left-click to pause. Run `python install.py` once to start
it automatically with Windows.
"""

from __future__ import annotations

import ctypes
import logging
import math
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

import cv2
import mediapipe as mp
import pystray
from PIL import Image, ImageDraw

log = logging.getLogger("gesture_mouse")

# ══════════════════════════════════════════════════════════════════════════
#  CONFIG - tweak these values, everything else is implementation detail
# ══════════════════════════════════════════════════════════════════════════

# ── Camera ────────────────────────────────────────────────────────────────
CAMERA_INDEX = 0            # 1, 2, ... if you have more than one webcam
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
MIRROR_CAMERA = True        # flip the image so moving right moves the cursor right

# ── Cursor movement ───────────────────────────────────────────────────────
# Area of the camera frame (x_min, y_min, x_max, y_max - normalized 0..1) that
# is stretched to the whole screen, so you only need to move your hand inside a
# comfortable box instead of across the whole frame. Keep the box centred on
# yourself: it is symmetric, so mirroring needs no adjustment.
ACTIVE_REGION = (0.20, 0.15, 0.80, 0.65)

SENSITIVITY = 1.0           # 1.0 = active region fills the screen, >1 = twitchier
SMOOTHING = 0.65            # 0.0 = raw and jittery, 1.0 = very smooth but laggy
INVERT_Y = False            # flip vertical cursor direction

# ── Pinch / click ─────────────────────────────────────────────────────────
# Pinch distance is measured in "hand units": the distance between the wrist
# and the middle-finger knuckle. That keeps it working whether your hand is
# near or far from the camera. 0.30 means "fingertips about 30% of a hand
# apart". Separate on/off values stop the pinch from flickering.
PINCH_ON = 0.30             # below this ratio a pinch starts
PINCH_OFF = 0.42            # above this ratio a pinch ends

CLICK_COOLDOWN = 0.45       # seconds to wait before another left click
RIGHT_CLICK_COOLDOWN = 0.70 # seconds to wait before another right click
DRAG_HOLD_DELAY = 0.35      # pinch longer than this and it becomes a drag
PAUSE_CLICK_LOCKOUT = 0.50  # click lockout right after a fist pause/resume

# ── Scrolling ─────────────────────────────────────────────────────────────
SCROLL_SPEED = 90.0         # wheel clicks per hand-height of movement
SCROLL_DEADZONE = 0.004     # ignore smaller frame-to-frame movement (jitter)
SCROLL_INVERT = False       # true = move hand down to scroll up

# ── Pause gesture (fist) ──────────────────────────────────────────────────
FIST_DEBOUNCE_FRAMES = 3    # consecutive frames a fist must be held to count
PAUSE_TOGGLE_COOLDOWN = 0.90  # seconds between pause/resume toggles

# ── Tracking quality (raise for precision, lower if the CPU struggles) ────
MODEL_COMPLEXITY = 0        # 0 = fast, 1 = more accurate
MIN_DETECTION_CONFIDENCE = 0.60
MIN_TRACKING_CONFIDENCE = 0.60

# ── Preview window (toggle it from the tray menu) ─────────────────────────
PREVIEW_ON_START = False    # true = camera window opens with the app
SHOW_LANDMARKS = True
PREVIEW_WINDOW_NAME = "Gesture Mouse"

# ── Background agent ──────────────────────────────────────────────────────
CAMERA_RETRY_SECONDS = 3.0  # wait this long before retrying a missing camera
LOG_FILE_NAME = "gesture_mouse.log"  # written next to this file, so it never floods a console

# Landmark indices as defined by MediaPipe Hands.
WRIST = 0
THUMB_TIP = 4
INDEX_PIP, INDEX_TIP = 6, 8
MIDDLE_PIP, MIDDLE_TIP = 10, 12
RING_PIP, RING_TIP = 14, 16
PINKY_PIP, PINKY_TIP = 18, 20
MIDDLE_MCP = 9
FINGER_TIPS = (INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP)
FINGER_PIPS = (INDEX_PIP, MIDDLE_PIP, RING_PIP, PINKY_PIP)
EXTENDED_RATIO = 1.08       # tip must be this much further from the wrist than its knuckle
FIST_MAX_RATIO = 0.90       # every tip must be this much closer to the wrist than its knuckle


# ══════════════════════════════════════════════════════════════════════════
#  Cursor backend
# ══════════════════════════════════════════════════════════════════════════

def _setup_logging() -> None:
    """Log to a file (started via pythonw there is no console) and to stderr if present."""
    handlers: List[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    try:
        handlers.append(logging.FileHandler(Path(__file__).resolve().with_name(LOG_FILE_NAME), encoding="utf-8"))
    except OSError:
        pass  # read-only folder: console output is all we can do
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=handlers or [logging.NullHandler()],
        force=True,
    )
    logging.getLogger("PIL").setLevel(logging.WARNING)


def _enable_dpi_awareness() -> None:
    """Make Windows report real pixels, otherwise the cursor drifts on scaled displays."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_dpi_awareness()
import pyautogui  # noqa: E402  - must come after DPI awareness is set


class Mouse(Protocol):
    """The little bit of OS mouse control the gesture engine needs."""

    def size(self) -> Tuple[int, int]: ...
    def move_to(self, x: int, y: int) -> None: ...
    def left_click(self) -> None: ...
    def right_click(self) -> None: ...
    def mouse_down(self) -> None: ...
    def mouse_up(self) -> None: ...
    def scroll(self, clicks: int) -> None: ...


class PyAutoGUIMouse:
    """Thin pyautogui wrapper configured for low-latency gesture control."""

    def __init__(self) -> None:
        pyautogui.FAILSAFE = False  # we move the pointer ourselves; corner-abort would fire mid-gesture
        pyautogui.PAUSE = 0.0

    def size(self) -> Tuple[int, int]:
        width, height = pyautogui.size()
        return int(width), int(height)

    def move_to(self, x: int, y: int) -> None:
        pyautogui.moveTo(x, y, _pause=False)

    def left_click(self) -> None:
        pyautogui.click(button="left")

    def right_click(self) -> None:
        pyautogui.click(button="right")

    def mouse_down(self) -> None:
        pyautogui.mouseDown(button="left")

    def mouse_up(self) -> None:
        pyautogui.mouseUp(button="left")

    def scroll(self, clicks: int) -> None:
        pyautogui.scroll(int(clicks))


# ══════════════════════════════════════════════════════════════════════════
#  Hand data
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Hand:
    """A single hand as normalized (x, y) landmark points."""

    points: Sequence[Tuple[float, float]]

    @classmethod
    def from_landmarks(cls, landmarks: Sequence[object]) -> "Hand":
        return cls([(float(p.x), float(p.y)) for p in landmarks])

    def point(self, index: int) -> Tuple[float, float]:
        return self.points[index]

    def distance(self, a: int, b: int) -> float:
        (x1, y1), (x2, y2) = self.points[a], self.points[b]
        return math.hypot(x1 - x2, y1 - y2)

    def hand_scale(self) -> float:
        """Rough hand size, used to make thresholds independent of camera distance."""
        return max(self.distance(WRIST, MIDDLE_MCP), 1e-6)

    def fingers_extended(self) -> Dict[str, bool]:
        names = ("index", "middle", "ring", "pinky")
        out: Dict[str, bool] = {}
        for name, tip, pip in zip(names, FINGER_TIPS, FINGER_PIPS):
            out[name] = self.distance(tip, WRIST) > self.distance(pip, WRIST) * EXTENDED_RATIO
        return out

    def is_fist(self) -> bool:
        """All four fingertips curled *into the palm*, not merely half relaxed.

        A fingertip that is still ahead of its knuckle means the hand is only
        loosely closed, which is what a resting hand looks like - treating that
        as a fist would pause the app by accident.
        """
        for tip, pip in zip(FINGER_TIPS, FINGER_PIPS):
            if self.distance(tip, WRIST) > self.distance(pip, WRIST) * FIST_MAX_RATIO:
                return False
        return True


# ══════════════════════════════════════════════════════════════════════════
#  Smoothing - 1 euro filter (adaptive low pass: steady when slow, quick when fast)
# ══════════════════════════════════════════════════════════════════════════

class _LowPass:
    def __init__(self) -> None:
        self.value: Optional[float] = None

    def __call__(self, value: float, alpha: float) -> float:
        if self.value is None:
            self.value = value
        else:
            self.value = alpha * value + (1.0 - alpha) * self.value
        return self.value

    def reset(self) -> None:
        self.value = None


class OneEuroFilter:
    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float = 1.0) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x = _LowPass()
        self._dx = _LowPass()
        self._prev: Optional[float] = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, value: float, dt: float) -> float:
        dt = min(max(dt, 1e-4), 0.2)
        if self._prev is None:
            self._prev = value
            self._dx(0.0, 1.0)
            return self._x(value, 1.0)
        derivative = (value - self._prev) / dt
        self._prev = value
        smoothed_derivative = self._dx(derivative, self._alpha(self.d_cutoff, dt))
        cutoff = self.min_cutoff + self.beta * abs(smoothed_derivative)
        return self._x(value, self._alpha(cutoff, dt))

    def reset(self) -> None:
        self._x.reset()
        self._dx.reset()
        self._prev = None


# ══════════════════════════════════════════════════════════════════════════
#  Gesture engine - all gesture logic, no camera or OpenCV window involved
# ══════════════════════════════════════════════════════════════════════════

class GestureEngine:
    """Turns a stream of hands into cursor movement and clicks."""

    def __init__(self, mouse: Mouse) -> None:
        self.mouse = mouse
        self.screen_size = mouse.size()

        smoothing = min(max(SMOOTHING, 0.0), 1.0)
        min_cutoff = 8.0 - 7.0 * smoothing
        beta = 6.0 - 4.0 * smoothing
        self._filter_x = OneEuroFilter(min_cutoff, beta)
        self._filter_y = OneEuroFilter(min_cutoff, beta)

        self.gesture = "NO HAND"
        self.paused = False
        self.cursor: Optional[Tuple[float, float]] = None  # normalized 0..1 on screen
        self.pinch_ratio = float("nan")

        self._last_point_time: Optional[float] = None
        self._last_pointer_px: Tuple[int, int] = (-1, -1)
        self._pinch_started: Optional[float] = None
        self._pinch_armed = False
        self._dragging = False
        self._index_pinching = False
        self._middle_pinching = False
        self._last_left_click = -1e9
        self._last_right_click = -1e9
        self._suppress_clicks_until = 0.0
        self._scroll_prev_y: Optional[float] = None
        self._scroll_accum = 0.0
        self._fist_frames = 0
        self._last_pause_toggle = -1e9

    # ── public ────────────────────────────────────────────────────────────
    def process(self, hand: Optional[Hand], now: float) -> str:
        """Handle one frame. Returns the current gesture name."""
        if hand is None:
            self._hand_lost()
            return self.gesture

        scale = hand.hand_scale()
        self.pinch_ratio = hand.distance(THUMB_TIP, INDEX_TIP) / scale
        middle_ratio = hand.distance(THUMB_TIP, MIDDLE_TIP) / scale

        if hand.is_fist():
            self._on_fist(now)
            self.gesture = "PAUSED" if self.paused else "FIST"
            return self.gesture
        self._fist_frames = 0
        fingers = hand.fingers_extended()

        if self.paused:
            self.gesture = "PAUSED"
            return self.gesture

        # Hysteresis: separate thresholds to engage and release a pinch.
        index_now = self._pinch_state(self.pinch_ratio, self._index_pinching)
        middle_now = self._pinch_state(middle_ratio, self._middle_pinching)
        index_edge = index_now and not self._index_pinching
        middle_edge = middle_now and not self._middle_pinching
        self._index_pinching = index_now
        self._middle_pinching = middle_now

        # ── thumb + index: click on quick release, drag when held ─────────
        if self._index_pinching:
            self._move_cursor(hand, now)
            if self._dragging:
                self.gesture = "DRAG"
                return self.gesture
            if self._pinch_started is None:
                self._pinch_started = now
                # A closing fist also brings the thumb past the index finger;
                # only arm a click when the hand was open to begin with.
                self._pinch_armed = fingers["index"] or fingers["middle"]
                self.gesture = "PINCH"
                return self.gesture
            if now - self._pinch_started >= DRAG_HOLD_DELAY:
                self.mouse.mouse_down()
                self._dragging = True
                self._pinch_started = None
                self.gesture = "DRAG"
                return self.gesture
            self.gesture = "PINCH"
            return self.gesture

        if self._dragging:  # pinch released -> drop what we were dragging
            self._end_drag()
            self._pinch_started = None
            self._last_left_click = now
            self.gesture = "DROP"
            return self.gesture

        if self._pinch_started is not None:  # pinch released -> maybe a click
            self._pinch_started = None
            armed, self._pinch_armed = self._pinch_armed, False
            if armed and self._click_allowed(now, self._last_left_click, CLICK_COOLDOWN):
                self.mouse.left_click()
                self._last_left_click = now
                self.gesture = "LEFT CLICK"
                return self.gesture

        # ── thumb + middle: right click (on the press, not the release) ───
        if middle_edge and not index_edge:
            if self._click_allowed(now, self._last_right_click, RIGHT_CLICK_COOLDOWN):
                self.mouse.right_click()
                self._last_right_click = now
                self.gesture = "RIGHT CLICK"
                return self.gesture

        # ── index + middle up: scroll by moving the hand up and down ──────
        if (
            fingers["index"]
            and fingers["middle"]
            and not fingers["ring"]
            and not fingers["pinky"]
            and not self._index_pinching
        ):
            self._scroll(hand)
            self.gesture = "SCROLL"
            return self.gesture
        self._scroll_prev_y = None
        self._scroll_accum = 0.0

        self._move_cursor(hand, now)
        self.gesture = "MOVE"
        return self.gesture

    def shutdown(self) -> None:
        """Release anything held down so we never leave a stuck mouse button."""
        self._end_drag()

    # ── internals ─────────────────────────────────────────────────────────
    def _pinch_state(self, ratio: float, was_pinching: bool) -> bool:
        return ratio < (PINCH_OFF if was_pinching else PINCH_ON)

    def _click_allowed(self, now: float, last_click: float, cooldown: float) -> bool:
        return now >= self._suppress_clicks_until and now - last_click >= cooldown

    def _hand_lost(self) -> None:
        self._end_drag()
        self._pinch_started = None
        self._pinch_armed = False
        self._index_pinching = False
        self._middle_pinching = False
        self._scroll_prev_y = None
        self._scroll_accum = 0.0
        self._last_point_time = None
        self._filter_x.reset()
        self._filter_y.reset()
        self.cursor = None
        self.gesture = "PAUSED" if self.paused else "NO HAND"

    def _on_fist(self, now: float) -> None:
        self._suppress_clicks_until = max(self._suppress_clicks_until, now + PAUSE_CLICK_LOCKOUT)
        self._fist_frames += 1
        if self._fist_frames < FIST_DEBOUNCE_FRAMES:
            return
        if now - self._last_pause_toggle < PAUSE_TOGGLE_COOLDOWN:
            return
        self._last_pause_toggle = now
        self._fist_frames = 0
        self.set_paused(not self.paused, now)

    def set_paused(self, paused: bool, now: float) -> None:
        """Pause or resume tracking - used by the fist gesture and the tray menu."""
        self.paused = paused
        self._end_drag()
        self._pinch_started = None
        self._pinch_armed = False
        self._index_pinching = False
        self._middle_pinching = False
        self._scroll_prev_y = None
        self._scroll_accum = 0.0
        self._suppress_clicks_until = max(self._suppress_clicks_until, now + PAUSE_CLICK_LOCKOUT)
        log.info("tracking %s", "paused" if paused else "resumed")

    def _end_drag(self) -> None:
        if self._dragging:
            self.mouse.mouse_up()
            self._dragging = False

    def _scroll(self, hand: Hand) -> None:
        y = hand.point(MIDDLE_TIP)[1]
        if self._scroll_prev_y is not None:
            delta = self._scroll_prev_y - y  # positive when the hand moves up
            if abs(delta) >= SCROLL_DEADZONE:
                self._scroll_accum += delta * SCROLL_SPEED
        self._scroll_prev_y = y
        steps = int(self._scroll_accum)  # truncates toward zero, keeps the remainder
        if steps:
            self.mouse.scroll(-steps if SCROLL_INVERT else steps)
            self._scroll_accum -= steps

    def _move_cursor(self, hand: Hand, now: float) -> None:
        x, y = hand.point(INDEX_TIP)
        dt = 1.0 / 30.0 if self._last_point_time is None else now - self._last_point_time
        self._last_point_time = now
        x = self._filter_x(x, dt)
        y = self._filter_y(y, dt)

        nx = self._map_axis(x, ACTIVE_REGION[0], ACTIVE_REGION[2])
        ny = self._map_axis(y, ACTIVE_REGION[1], ACTIVE_REGION[3])
        if INVERT_Y:
            ny = 1.0 - ny
        self.cursor = (nx, ny)

        width, height = self.screen_size
        px = int(round(nx * (width - 1)))
        py = int(round(ny * (height - 1)))
        if (px, py) != self._last_pointer_px:
            self._last_pointer_px = (px, py)
            self.mouse.move_to(px, py)

    @staticmethod
    def _map_axis(value: float, low: float, high: float) -> float:
        t = (value - low) / max(high - low, 1e-6)
        t = 0.5 + (t - 0.5) * SENSITIVITY
        return min(1.0, max(0.0, t))


# ══════════════════════════════════════════════════════════════════════════
#  Preview window
# ══════════════════════════════════════════════════════════════════════════

def _draw_preview(frame, engine: GestureEngine, landmarks, fps: float) -> None:
    height, width = frame.shape[:2]

    x0, y0, x1, y1 = ACTIVE_REGION
    cv2.rectangle(
        frame,
        (int(x0 * width), int(y0 * height)),
        (int(x1 * width), int(y1 * height)),
        (90, 200, 140),
        1,
    )

    if SHOW_LANDMARKS and landmarks is not None:
        mp.solutions.drawing_utils.draw_landmarks(
            frame,
            landmarks,
            mp.solutions.hands.HAND_CONNECTIONS,
            mp.solutions.drawing_styles.get_default_hand_landmarks_style(),
            mp.solutions.drawing_styles.get_default_hand_connections_style(),
        )

    if engine.cursor is not None:
        cx, cy = int(engine.cursor[0] * width), int(engine.cursor[1] * height)
        cv2.circle(frame, (cx, cy), 6, (0, 255, 255), 2)

    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (width, 56), (25, 25, 25), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    if engine.paused:
        label, colour = "PAUSED", (0, 140, 255)
    elif engine.gesture == "NO HAND":
        label, colour = "NO HAND", (180, 180, 180)
    else:
        label, colour = engine.gesture, (0, 255, 120)

    cv2.putText(frame, label, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.72, colour, 2, cv2.LINE_AA)
    cv2.putText(
        frame,
        f"{fps:4.0f} FPS   |   fist = pause/resume   |   Q = quit",
        (12, 48),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (215, 215, 215),
        1,
        cv2.LINE_AA,
    )

    if engine.paused:
        text = "TRACKING PAUSED"
        size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
        cv2.putText(
            frame,
            text,
            ((width - size[0]) // 2, height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 140, 255),
            2,
            cv2.LINE_AA,
        )


# ══════════════════════════════════════════════════════════════════════════
#  Tray icon - the app's only always-visible control
# ══════════════════════════════════════════════════════════════════════════

def _tray_image(paused: bool) -> "Image.Image":
    """Blue icon with a cursor arrow while tracking, orange with pause bars when paused."""
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (2, 2, size - 3, size - 3),
        fill=(230, 150, 30, 255) if paused else (60, 140, 235, 255),
    )
    if paused:
        draw.rectangle((22, 20, 28, 44), fill=(255, 255, 255, 255))
        draw.rectangle((36, 20, 42, 44), fill=(255, 255, 255, 255))
    else:
        draw.polygon(
            [(22, 14), (22, 46), (29, 38), (34, 50), (40, 47), (35, 35), (46, 31)],
            fill=(255, 255, 255, 255),
        )
    return image


class AgentState:
    """State shared between the tray thread and the tracking loop."""

    def __init__(self) -> None:
        self.preview = PREVIEW_ON_START
        self.stop = threading.Event()


def _refresh_tray(icon: "pystray.Icon", engine: GestureEngine, state: AgentState) -> None:
    """Keep the icon colour, tooltip and (dynamic) menu labels in sync."""
    try:
        icon.icon = _tray_image(engine.paused)
        icon.title = "Gesture Mouse - paused" if engine.paused else "Gesture Mouse - tracking"
        icon.update_menu()
    except Exception:  # tray backends differ; never let cosmetic updates kill tracking
        log.debug("tray refresh failed", exc_info=True)


def _notify(icon: "pystray.Icon", message: str) -> None:
    try:
        icon.notify(message, "Gesture Mouse")
    except Exception:
        log.debug("tray notification failed", exc_info=True)


def _start_tray(engine: GestureEngine, state: AgentState) -> "pystray.Icon":
    def toggle_pause(icon, item) -> None:
        engine.set_paused(not engine.paused, time.monotonic())
        _refresh_tray(icon, engine, state)

    def toggle_preview(icon, item) -> None:
        state.preview = not state.preview
        _refresh_tray(icon, engine, state)

    def quit_app(icon, item) -> None:
        state.stop.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(
            lambda item: "Resume tracking" if engine.paused else "Pause tracking",
            toggle_pause,
            default=True,  # left-clicking the tray icon pauses/resumes
        ),
        pystray.MenuItem(
            lambda item: "Hide preview" if state.preview else "Show preview",
            toggle_preview,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Gesture Mouse", quit_app),
    )
    icon = pystray.Icon(
        "gesture-mouse",
        _tray_image(engine.paused),
        "Gesture Mouse - paused" if engine.paused else "Gesture Mouse - tracking",
        menu,
    )
    threading.Thread(target=icon.run, name="tray", daemon=True).start()
    return icon


# ══════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════

def _pin_window_on_top() -> None:
    """Keep the preview visible while the pointer is busy on other windows."""
    try:
        cv2.setWindowProperty(PREVIEW_WINDOW_NAME, cv2.WND_PROP_TOPMOST, 1)
    except cv2.error:
        pass  # not every OpenCV backend supports this; Q then needs a click on the preview


def _window_closed() -> bool:
    try:
        return cv2.getWindowProperty(PREVIEW_WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


def _close_preview() -> None:
    try:
        cv2.destroyWindow(PREVIEW_WINDOW_NAME)
    except cv2.error:
        pass


def _open_capture(index: int) -> "cv2.VideoCapture":
    if sys.platform == "win32":
        # Media Foundation first: the DirectShow backend throttles some webcams to
        # about 20 fps, which makes the cursor feel sluggish. DirectShow is kept as
        # a fallback for cameras Media Foundation refuses to open.
        backends: List[int] = [cv2.CAP_MSMF, cv2.CAP_DSHOW]
    else:
        backends = [cv2.CAP_ANY]

    for backend in backends:
        capture = cv2.VideoCapture(index, backend)
        if capture.isOpened():
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            # Some backends open happily and then never deliver a frame; make sure
            # this camera is actually usable before committing to it.
            for _ in range(3):
                ok, _frame = capture.read()
                if ok:
                    return capture
        capture.release()

    raise RuntimeError(
        f"No webcam could be opened at CAMERA_INDEX={index}. "
        "Close other apps that use the camera, or change CAMERA_INDEX in the config."
    )


def main() -> int:
    _setup_logging()
    log.info("starting Gesture Mouse")

    mouse = PyAutoGUIMouse()
    engine = GestureEngine(mouse)
    state = AgentState()
    icon = _start_tray(engine, state)
    log.info("screen %dx%d, preview %s", *engine.screen_size, "on" if state.preview else "off")

    capture = None
    window_visible = False
    window_pinned = False
    camera_problem_reported = False
    fps = 0.0
    last_frame_time: Optional[float] = None
    hands_module = mp.solutions.hands

    try:
        with hands_module.Hands(
            static_image_mode=False,
            max_num_hands=1,
            model_complexity=MODEL_COMPLEXITY,
            min_detection_confidence=MIN_DETECTION_CONFIDENCE,
            min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
        ) as hands:
            while not state.stop.is_set():
                # ── camera: open, and reopen if it disappears at runtime ──
                if capture is None:
                    try:
                        capture = _open_capture(CAMERA_INDEX)
                    except (RuntimeError, cv2.error) as exc:
                        log.error("camera unavailable: %s", exc)
                        if not camera_problem_reported:
                            _notify(icon, "No webcam found - retrying every few seconds.")
                            camera_problem_reported = True
                        if state.stop.wait(CAMERA_RETRY_SECONDS):
                            break
                        continue
                    if camera_problem_reported:
                        _notify(icon, "Camera reconnected.")
                        camera_problem_reported = False
                    log.info("camera %d open", CAMERA_INDEX)

                ok, frame = capture.read()
                if not ok or frame is None:
                    log.warning("camera stream lost, reconnecting")
                    capture.release()
                    capture = None
                    engine.process(None, time.monotonic())  # drop drags, reset smoothing
                    if state.stop.wait(1.0):
                        break
                    continue

                # ── track one frame ───────────────────────────────────────
                if (frame.shape[1], frame.shape[0]) != (FRAME_WIDTH, FRAME_HEIGHT):
                    frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
                if MIRROR_CAMERA:
                    frame = cv2.flip(frame, 1)

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rgb.flags.writeable = False
                results = hands.process(rgb)

                landmarks = results.multi_hand_landmarks[0] if results.multi_hand_landmarks else None
                hand = Hand.from_landmarks(landmarks.landmark) if landmarks is not None else None

                now = time.monotonic()
                if last_frame_time is not None:
                    fps = 0.9 * fps + 0.1 * (1.0 / max(now - last_frame_time, 1e-6))
                last_frame_time = now

                engine.process(hand, now)

                # ── preview, toggled from the tray (never from the tray thread) ──
                if state.preview != window_visible:
                    window_visible = state.preview
                    if window_visible:
                        log.info("preview shown")
                    else:
                        _close_preview()
                        window_pinned = False
                        log.info("preview hidden")

                if window_visible:
                    _draw_preview(frame, engine, landmarks, fps)
                    cv2.imshow(PREVIEW_WINDOW_NAME, frame)
                    if not window_pinned:
                        _pin_window_on_top()
                        window_pinned = True

                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    log.info("quit from preview window")
                    break
                if window_visible and _window_closed():
                    # closing the preview only hides it; tracking continues in the tray
                    state.preview = False
                    _refresh_tray(icon, engine, state)
    except KeyboardInterrupt:
        log.info("interrupted")
    except Exception:
        # Started with pythonw there is no console, so a traceback would vanish.
        log.exception("fatal error, stopping")
        _notify(icon, "Gesture Mouse stopped after an error - see gesture_mouse.log")
        return 1
    finally:
        engine.shutdown()
        if capture is not None:
            capture.release()
        try:
            cv2.destroyAllWindows()
            cv2.waitKey(1)  # let the backend actually remove the window before we exit
        except cv2.error:
            pass
        icon.stop()
        log.info("stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
