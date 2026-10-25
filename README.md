# Gesture Mouse

Control your PC with hand gestures in front of your webcam — no extra hardware, no
cloud, no account. It runs quietly in the system tray and starts with Windows, so your
hand is a mouse from the moment you log in.

**Windows 10 / 11** · Python 3.9–3.12 · [MIT license](LICENSE)

```
point your index finger  →  cursor moves
quick pinch              →  left click
pinch and hold           →  drag, release to drop
thumb + middle pinch     →  right click
two fingers up/down      →  scroll
closed fist              →  pause / resume
```

Built with OpenCV, MediaPipe Hands and pyautogui. Everything happens locally on your
machine: frames from the webcam are never written to disk or sent anywhere.

## Install (Windows)

**1. Install Python 3.9–3.12** from [python.org](https://www.python.org/downloads/)
(tick *"Add python.exe to PATH"*). MediaPipe has no wheels for 3.13+ yet, so 3.12 is
the newest version that works.

**2. Get the code** — either clone it:

```bash
git clone https://github.com/sreevathson1530/gesture-mouse.git
cd gesture-mouse
```

or download the ZIP from GitHub (**Code → Download ZIP**) and extract it.

**3. Install and start it:**

```bash
python install.py
```

That one command creates a local `.venv`, installs the dependencies, puts a silent
launcher in your Windows Startup folder, and starts the app. A **tray icon** appears —
that is the app running.

Prefer no autostart? Do the steps by hand instead:

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe gesture_mouse.py
```

## Using it

Hold your hand about arm's length from the webcam, palm facing it, and point with your
index finger. The cursor follows your fingertip inside a comfortable box in the middle
of the frame — you do not have to reach for the edges of the camera's view.

**The tray icon is the control panel:**

| Action | What happens |
| --- | --- |
| Left-click the tray icon | Pause / resume tracking (icon turns orange when paused) |
| Right-click → *Show preview* | Small window with the camera feed, hand landmarks, live gesture name and FPS |
| Right-click → *Pause / Resume tracking* | Same as the fist gesture |
| Right-click → *Quit Gesture Mouse* | Stops the app until you start it again |
| **Q** or **Esc** in the preview window | Quits the app |
| Closed fist | Pause / resume without touching the keyboard |

Closing the preview window only hides it — tracking continues in the tray.

### Gestures

| Gesture | Action | Notes |
| --- | --- | --- |
| Point with your index finger | Move the cursor | Smoothed, with adaptive filtering |
| Quick pinch, thumb + index | Left click | Fires when you release the pinch |
| Pinch and hold, thumb + index | Click and drag | Held longer than `DRAG_HOLD_DELAY`, release to drop |
| Pinch with thumb + middle finger | Right click | Fires on the pinch |
| Index + middle finger up, move hand up/down | Scroll | Moving up scrolls up |
| Closed fist (four fingers curled into the palm) | Pause / resume | A relaxed, half-open hand is ignored |

## Tuning

Every setting lives in the `CONFIG` block at the top of `gesture_mouse.py`. Save the file
and restart the app (tray → *Quit*, then relaunch) to apply changes.

| Setting | Meaning |
| --- | --- |
| `CAMERA_INDEX` | `0` is the built-in webcam; try `1`, `2` for external ones |
| `FRAME_WIDTH` / `FRAME_HEIGHT` | Capture resolution — lower is faster |
| `MIRROR_CAMERA` | Mirror the image so hand-right = cursor-right |
| `ACTIVE_REGION` | `(x_min, y_min, x_max, y_max)` of the frame that maps to the whole screen. Shrink it to move less |
| `SENSITIVITY` | `1.0` = the region covers the screen exactly, `0.85` = steadier, `1.2` = faster |
| `SMOOTHING` | `0` = raw and jittery, `1` = very smooth but laggy. `0.6`–`0.75` feels best |
| `INVERT_Y` | Flip vertical direction if it feels backwards |
| `PINCH_ON` / `PINCH_OFF` | Pinch threshold as a fraction of your hand's size; lower = must pinch tighter |
| `CLICK_COOLDOWN` | Minimum seconds between two left clicks |
| `RIGHT_CLICK_COOLDOWN` | Same, for right clicks |
| `DRAG_HOLD_DELAY` | How long a pinch must be held before it becomes a drag (raise if drags start by accident) |
| `PAUSE_CLICK_LOCKOUT` | Click lockout right after pausing/resuming |
| `SCROLL_SPEED` | Wheel clicks per hand-height moved |
| `SCROLL_DEADZONE` | Hand jitter below this is ignored while scrolling |
| `SCROLL_INVERT` | Swap the scroll direction |
| `FIST_DEBOUNCE_FRAMES`, `PAUSE_TOGGLE_COOLDOWN` | How firmly and how often a fist can toggle pause |
| `MODEL_COMPLEXITY` | `0` = fast (default), `1` = more accurate but slower |
| `MIN_DETECTION_CONFIDENCE` | Raise toward `0.75` if the app sees hands that are not there |
| `PREVIEW_ON_START` | `true` opens the camera preview with the app |
| `SHOW_LANDMARKS` | Draw the hand skeleton in the preview |
| `CAMERA_RETRY_SECONDS` | How long to wait before retrying a camera that is missing or unplugged |

Thresholds are measured relative to your own hand (wrist to middle knuckle), so they keep
working whether your hand is close to the camera or far away.

## How it works

- **Tracking** — MediaPipe Hands finds 21 landmarks per frame; the index fingertip drives
  the pointer.
- **Smoothing** — a *1 euro filter* per axis: heavy smoothing when the hand is slow (kills
  jitter for precise aiming), lighter smoothing when it moves fast (no trailing).
- **Mapping** — `ACTIVE_REGION` stretches a comfortable box of the camera frame to the full
  screen and clamps at the edges, so you never have to stretch your arm.
- **Pinch** — distance between thumb and index/middle tips, normalised by hand size, with
  hysteresis (`PINCH_ON` / `PINCH_OFF`) so a fingertip on the threshold cannot chatter.
- **Click vs drag** — a pinch released quickly is a click; held past `DRAG_HOLD_DELAY` it
  presses the button down and drags until you release. Cooldowns stop double firing.
- **Pause** — a fist (fingertips curled into the palm, debounced) toggles tracking; while
  paused no clicks, movement or scrolling are sent.
- **Safety** — any pause, lost hand, lost camera, Ctrl+C or *Quit* releases a held mouse
  button, so a drag can never get stuck. Capture prefers Media Foundation over DirectShow
  because DirectShow throttles some webcams to ~20 fps.
- **Always on** — the app reconnects by itself if the camera is unplugged, closed by another
  app or comes back later, and the Startup entry relaunches it at every login.

## Privacy and safety

- Video frames are processed in memory and immediately discarded. Nothing is recorded,
  saved or uploaded, and the app has no network code at all.
- It *does* control your real mouse. Keep the tray icon or the preview window within reach
  so you can pause (left-click the icon or a closed fist) if a gesture misfires.
- `pyautogui`'s corner failsafe is disabled because it would trigger while you simply move
  the pointer; use the tray menu, the fist or **Q** to stop it.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Nothing happens, no tray icon | Run `install.py` from a terminal to see the error, and check `gesture_mouse.log` next to the script |
| "No webcam found" notification at login | Another app (Teams, Zoom, Camera) is holding the webcam. The app retries by itself every few seconds |
| `ImportError: cannot import name 'solutions' from 'mediapipe'` | You have MediaPipe 1.x. Install the pinned version with `pip install -r requirements.txt` |
| `No matching distribution found for mediapipe` | Your Python is too new (3.13+) or 32-bit. Install Python 3.12 64-bit |
| Cursor stops at the screen edge | Your hand left `ACTIVE_REGION` — enlarge it or match it to how you sit |
| Clicks fire while making a fist | Lower `PINCH_ON` (e.g. `0.24`) |
| Drags start when you want a click | Raise `DRAG_HOLD_DELAY` to `0.45` |
| Too jumpy | Raise `SMOOTHING` toward `0.85`, lower `SENSITIVITY` to `0.85` |
| Cursor lags, CPU pegged | `MODEL_COMPLEXITY = 0`, lower `FRAME_WIDTH` / `FRAME_HEIGHT` |
| Front-panel camera not used | Change `CAMERA_INDEX` to `1` or `2` |
| Startup entry removed accidentally | Run `python install.py` again |

## Uninstall

```bash
python install.py --remove          # stop starting with Windows
```

Then quit from the tray menu and delete the folder (the app only lives there — no system
files, registry keys or services).

## macOS / Linux

The gesture engine is portable, but the tray and autostart are Windows-tested. On macOS or
Linux, create the venv as above, run `.venv/bin/python gesture_mouse.py`, and grant
Accessibility permission on macOS (System Settings → Privacy & Security → Accessibility)
so pyautogui can move the pointer. `install.py` only writes the Windows Startup entry; on
macOS pystray wants its event loop on the main thread, which this version does not do yet.

## License

MIT — use it, fork it, ship it. See [LICENSE](LICENSE).
