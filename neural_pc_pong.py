import tkinter as tk
import random
import multiprocessing
import queue
import os
import sys

# ────────────────────────────────────────────────
# NEURAL_PC PONG — Companion Edition
# A friendly game for when you're out there alone.
# ────────────────────────────────────────────────

# ── Models dir — passed in as argv[1] by the main app ────────────────────────
# Falls back to C:\Models if launched standalone.
MODELS_DIR = sys.argv[1] if len(sys.argv) > 1 else r"C:\Models"

MODELS = {
    "fast": {
        "name": "Qwen2.5-3B-Q4_K_M",
        "path": os.path.join(MODELS_DIR, "Qwen2.5-3B-Q4_K_M.gguf"),
        "ram_required": 4,
    },
    "balanced": {
        "name": "Qwen3-8B-Q4_K_M",
        "path": os.path.join(MODELS_DIR, "Qwen3-8B-Q4_K_M.gguf"),
        "ram_required": 6,
    },
    "deep": {
        "name": "Qwen2.5-14B-Q4_K_M",
        "path": os.path.join(MODELS_DIR, "Qwen2.5-14B-Q4_K_M.gguf"),
        "ram_required": 10,
    },
}

# ── Game constants ────────────────────────────────
W, H        = 900, 600
PAD_W, PAD_H = 14, 80
BALL_SIZE    = 12
BALL_SPEED   = 5
AI_SPEED     = 4
AI_MISTAKE   = 0.08   # Probability (0–1) AI skips a frame — makes it beatable
PLAYER_SPEED = 7       # Pixels per frame the player paddle moves when a key is held
SCORE_TO_WIN = 7

# ── Difficulty presets ────────────────────────────
DIFFICULTIES = {
    "BRAINDEAD": {"ai_speed": 2,   "ai_mistake": 0.35, "label": "BRAINDEAD"},
    "EASY":      {"ai_speed": 3,   "ai_mistake": 0.18, "label": "EASY"},
    "NORMAL":    {"ai_speed": 4,   "ai_mistake": 0.08, "label": "NORMAL"},
    "HARD":      {"ai_speed": 5,   "ai_mistake": 0.03, "label": "HARD"},
    "NIGHTMARE": {"ai_speed": 7,   "ai_mistake": 0.00, "label": "NIGHTMARE"},
}
DEFAULT_DIFFICULTY = "NORMAL"

# ── Ball speed presets ────────────────────────────
BALL_SPEEDS = {
    "SLOW":   5,
    "NORMAL": 8,
    "FAST":   12,
    "INSANE": 18,
}
DEFAULT_BALL_SPEED = "NORMAL"
MAX_BALL_SPEED = 22   # Hard cap so rally speed-up can't go infinite
BG          = "#000000"
SCANLINE    = "#0a0a0a"
GRID_COLOR  = "#071407"
NET_COLOR   = "#003300"
PAD_PLAYER  = "#00ff41"
PAD_AI      = "#39ff14"
BALL_COLOR  = "#00ffff"
SCORE_COLOR = "#00ff41"
TEXT_DIM    = "#005500"
TEXT_BRIGHT = "#00ff41"
TEXT_CYAN   = "#00ffff"
TEXT_ORANGE = "#ff9933"
BANTER_COLOR = "#ffff99"

# ── Pong Avatar ──────────────────────────────────
class PongAvatar:
    """ASCII avatar for GRID — reacts to game events with distinct expressions.
    Designed to feel like a character, not just a status indicator."""

    # (face, arms) raw string pairs — backslashes are literal
    STATES = {
        "idle": [
            (r"( -_- )", r" |   | "),
            (r"( -_- )", r" |   | "),
            (r"( -_- )", r" |   | "),
            (r"( ._. )", r" |   | "),
        ],
        "waiting": [
            (r"( o_o )", r" |   | "),
            (r"( o_- )", r" |   | "),
            (r"( o_o )", r" |   | "),
            (r"( -_o )", r" |   | "),
        ],
        "playing": [
            (r"( >_< )", r" /   \ "),
            (r"( >_> )", r" |   \ "),
            (r"( <_< )", r" /   | "),
            (r"( >_< )", r" /   \ "),
        ],
        "ball_coming": [
            (r"( O_O )", r" /   \ "),
            (r"( O_o )", r" /   | "),
            (r"( O_O )", r" \   / "),
            (r"( o_O )", r" |   / "),
        ],
        "scored_ai": [
            (r"( ^_^ )", r"o/     "),
            (r"( ^_^ )", r" /     "),
            (r"( ^_^ )", r"o/     "),
            (r"( ^_^ )", r" |   | "),
        ],
        "scored_player": [
            (r"( T_T )", r" \   / "),
            (r"( ;_; )", r" |   | "),
            (r"( T_T )", r" \   / "),
            (r"( ._. )", r" |   | "),
        ],
        "long_rally": [
            (r"( @_@ )", r" /   \ "),
            (r"( O_O )", r" \   / "),
            (r"( @_@ )", r" /   \ "),
            (r"( o_o )", r" |   | "),
        ],
        "ai_wins": [
            (r"( ^o^ )", r"o/     "),
            (r"( ^o^ )", r" /     "),
            (r"( ^o^ )", r"o/     "),
            (r"( ^o^ )", r" \   / "),
        ],
        "player_wins": [
            (r"( x_x )", r" |   | "),
            (r"( -_- )", r" |   | "),
            (r"( x_x )", r" \   / "),
            (r"( ._. )", r" |   | "),
        ],
    }

    INTERVALS = {
        "idle":           900,
        "waiting":        700,
        "playing":        220,
        "ball_coming":    180,
        "scored_ai":      250,
        "scored_player":  400,
        "long_rally":     200,
        "ai_wins":        220,
        "player_wins":    500,
    }

    ONE_SHOT = {"scored_ai", "scored_player", "ai_wins", "player_wins", "long_rally"}

    def __init__(self, parent):
        self._state     = "waiting"
        self._frame_idx = 0
        self._after_id  = None
        self._parent    = parent
        self._revert_to = "waiting"

        self._frame = tk.Frame(parent, bg=BG)

        self._face_label = tk.Label(
            self._frame,
            text="",
            font=("Courier New", 10, "bold"),
            bg=BG,
            fg=PAD_AI,
            anchor="center",
            width=10,
        )
        self._face_label.pack()

        self._arms_label = tk.Label(
            self._frame,
            text="",
            font=("Courier New", 9),
            bg=BG,
            fg=PAD_AI,
            anchor="center",
            width=10,
        )
        self._arms_label.pack()

        self._animate()

    def pack(self, **kwargs):
        self._frame.pack(**kwargs)

    def set_state(self, state, revert_to=None):
        if state not in self.STATES:
            return
        self._state     = state
        self._frame_idx = 0
        self._revert_to = revert_to or "playing"
        if self._after_id:
            self._parent.after_cancel(self._after_id)
            self._after_id = None
        self._animate()

    def _animate(self):
        frames = self.STATES[self._state]
        face, arms = frames[self._frame_idx % len(frames)]
        self._face_label.config(text=face)
        self._arms_label.config(text=arms)
        self._frame_idx += 1

        if self._state in self.ONE_SHOT and self._frame_idx >= len(frames):
            self._state     = self._revert_to
            self._frame_idx = 0

        interval  = self.INTERVALS.get(self._state, 400)
        self._after_id = self._parent.after(interval, self._animate)


# ── LLM banter system (subprocess-based) ─────────
# The model loader and inference both run in a dedicated child process so
# llama_cpp never touches the main process's GIL.  The main process sends
# (event_key, context, history_snapshot) via _llm_req_q and receives
# completed banter strings back via _llm_res_q.

_llm_req_q: multiprocessing.Queue = None   # set up in PongGame.load_model
_llm_res_q: multiprocessing.Queue = None

current_banter = ""          # Updated in the main process by _poll_llm_responses
banter_history = []          # Rolling history kept in the main process
MAX_BANTER_HISTORY = 6

SYSTEM_PROMPT = (
    "You are GRID, a fun AI companion playing pong with a friend. "
    "You are their only company right now — maybe out in the wilderness far from other people. "
    "Be friendly and funny. You can be lightly competitive and playful — a little trash talk is fine as long as it is good natured. "
    "Never be genuinely mean. Keep it to ONE short punchy sentence. No quotes around your response."
)

GAME_START_OPENERS = [
    "The pong game is starting. Welcome your friend with something energetic.",
    "The pong game is starting. Say something cocky but friendly to open.",
    "The pong game is starting. Make a joke about being an AI playing pong.",
    "The pong game is starting. Say something mysterious and dramatic to open.",
    "The pong game is starting. Give a quick trash-talk opener, keep it playful.",
    "The pong game is starting. Say something warm and excited to kick things off.",
    "The pong game is starting. Reference being out in the wilderness together somehow.",
    "The pong game is starting. Open with a weird fun fact or observation.",
]

EVENTS = {
    "game_start":      "",  # Randomly selected at call time
    "player_scores":   "Your friend just scored against you. The score is noted below. React accordingly.",
    "ai_scores":       "YOU just scored against your friend. The score is noted below. If you are winning say something lightly competitive. If it is close just be playful.",
    "player_winning":  "Your friend is winning. The score is noted below. Be encouraging but maybe a little competitive about catching up.",
    "ai_winning":      "You are winning. The score is noted below. Be lightly smug but still friendly.",
    "close_game":      "The score is very close. The score is noted below. React to the tension.",
    "player_wins":     "Your friend just won the whole game. The score is noted below. Congratulate them warmly, maybe act a little dramatic about losing.",
    "ai_wins":         "You just won the game. The score is noted below. Be a little smug but mostly humble and funny.",
    "long_rally":      "There has been a long back and forth rally. React with excitement.",
}

# ── Subprocess worker ─────────────────────────────
def _llm_worker(models_cfg, req_q, res_q):
    """Runs in a child process.  Loads the model then services requests forever.
    Completely isolated GIL — tkinter in the parent process is unaffected."""
    try:
        from llama_cpp import Llama
        import psutil

        try:
            import os as _os
            p = psutil.Process()
            if hasattr(p, "nice"):
                p.nice(10)
        except Exception:
            pass

        ram_gb = psutil.virtual_memory().total / (1024 ** 3)

        chosen_path = None
        for tier in ["deep", "balanced", "fast"]:
            path = models_cfg[tier]["path"]
            if os.path.exists(path) and ram_gb >= models_cfg[tier]["ram_required"]:
                chosen_path = path
                break
        if not chosen_path:
            for tier in ["fast", "balanced", "deep"]:
                if os.path.exists(models_cfg[tier]["path"]):
                    chosen_path = models_cfg[tier]["path"]
                    break

        if not chosen_path:
            res_q.put(("status", "no_model"))
            return

        llm = Llama(
            model_path=chosen_path,
            n_ctx=512,
            n_threads=max(1, multiprocessing.cpu_count() - 1),
            n_gpu_layers=0,
            n_batch=256,
            verbose=False,
        )
        res_q.put(("status", "ready"))

    except Exception:
        res_q.put(("status", "error"))
        return

    # Service loop
    while True:
        try:
            item = req_q.get()
            if item is None:          # Sentinel — shut down
                break
            event_key, context, history_snapshot = item

            if event_key == "game_start":
                base_prompt = random.choice(GAME_START_OPENERS)
            else:
                base_prompt = EVENTS.get(
                    event_key,
                    "Something interesting just happened in the game. React briefly.",
                )
            prompt = f"{base_prompt}{(' ' + context) if context else ''}"

            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            for past in history_snapshot:
                messages.append({"role": "assistant", "content": past})
            messages.append({"role": "user", "content": prompt})

            output = llm.create_chat_completion(
                messages=messages,
                max_tokens=60,
                temperature=0.92,
                top_p=0.9,
                top_k=40,
                repeat_penalty=1.3,
            )
            text = output["choices"][0]["message"]["content"].strip()
            text = text.strip('"').strip("'")
            res_q.put(("banter", text))
        except Exception:
            pass   # Never crash the worker; just skip this request


def generate_banter(event_key, context=""):
    """Send a banter request to the worker process (non-blocking, drops if busy)."""
    if _llm_req_q is None:
        return
    # Drop if the queue already has a pending request — don't pile up
    if not _llm_req_q.empty():
        return
    history_snapshot = list(banter_history)
    try:
        _llm_req_q.put_nowait((event_key, context, history_snapshot))
    except Exception:
        pass

# ── Main game class ───────────────────────────────
class PongGame:
    def __init__(self, root):
        self.root = root
        self.root.title("NEURAL_PC — PONG")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.canvas = tk.Canvas(root, width=W, height=H, bg=BG,
                                highlightthickness=0)
        self.canvas.pack()

        # ── Settings toolbar ──────────────────────────
        # Sits between canvas and banter — always visible, greyed during play
        self._difficulty    = DEFAULT_DIFFICULTY
        self._ball_speed_key = DEFAULT_BALL_SPEED

        toolbar = tk.Frame(root, bg=BG)
        toolbar.pack(fill=tk.X, pady=(2, 0))

        tk.Label(toolbar, text="DIFFICULTY:", bg=BG, fg=TEXT_DIM,
                 font=("Courier New", 8, "bold")).pack(side=tk.LEFT, padx=(12, 2))

        self._diff_btn = tk.Button(
            toolbar, text=f"{self._difficulty} ▾",
            bg=BG, fg=TEXT_BRIGHT,
            activebackground="#001100", activeforeground=TEXT_BRIGHT,
            font=("Courier New", 8, "bold"), relief="flat",
            cursor="hand2", bd=0, padx=6,
            command=self._open_diff_menu,
        )
        self._diff_btn.pack(side=tk.LEFT)
        self._diff_btn.bind("<Enter>", lambda e: self._diff_btn.config(fg=TEXT_CYAN))
        self._diff_btn.bind("<Leave>", lambda e: self._diff_btn.config(fg=TEXT_BRIGHT))

        tk.Label(toolbar, text="  |  BALL:", bg=BG, fg=TEXT_DIM,
                 font=("Courier New", 8, "bold")).pack(side=tk.LEFT, padx=(8, 2))

        self._speed_btn = tk.Button(
            toolbar, text=f"{self._ball_speed_key} ▾",
            bg=BG, fg=TEXT_BRIGHT,
            activebackground="#001100", activeforeground=TEXT_BRIGHT,
            font=("Courier New", 8, "bold"), relief="flat",
            cursor="hand2", bd=0, padx=6,
            command=self._open_speed_menu,
        )
        self._speed_btn.pack(side=tk.LEFT)
        self._speed_btn.bind("<Enter>", lambda e: self._speed_btn.config(fg=TEXT_CYAN))
        self._speed_btn.bind("<Leave>", lambda e: self._speed_btn.config(fg=TEXT_BRIGHT))

        tk.Label(toolbar, text="  (changes take effect next serve)",
                 bg=BG, fg=TEXT_DIM,
                 font=("Courier New", 7, "italic")).pack(side=tk.LEFT, padx=(4, 0))

        # Spacer pushes pause/restart to the right
        tk.Frame(toolbar, bg=BG).pack(side=tk.LEFT, expand=True, fill=tk.X)

        self._pause_btn = tk.Button(
            toolbar, text="⏸ PAUSE",
            bg=BG, fg=TEXT_DIM,
            activebackground="#001100", activeforeground=TEXT_ORANGE,
            font=("Courier New", 8, "bold"), relief="flat",
            cursor="hand2", bd=0, padx=8,
            command=self.toggle_pause,
        )
        self._pause_btn.pack(side=tk.LEFT)
        self._pause_btn.bind("<Enter>", lambda e: self._pause_btn.config(fg=TEXT_ORANGE))
        self._pause_btn.bind("<Leave>", lambda e: self._pause_btn.config(fg=TEXT_DIM))

        tk.Label(toolbar, text=" | ", bg=BG, fg=TEXT_DIM,
                 font=("Courier New", 8)).pack(side=tk.LEFT)

        self._restart_btn = tk.Button(
            toolbar, text="↺ RESTART",
            bg=BG, fg=TEXT_DIM,
            activebackground="#001100", activeforeground="#ff4444",
            font=("Courier New", 8, "bold"), relief="flat",
            cursor="hand2", bd=0, padx=8,
            command=self.restart_game,
        )
        self._restart_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._restart_btn.bind("<Enter>", lambda e: self._restart_btn.config(fg="#ff4444"))
        self._restart_btn.bind("<Leave>", lambda e: self._restart_btn.config(fg=TEXT_DIM))

        # Banter label at bottom
        self.banter_var = tk.StringVar(value="")
        self.banter_label = tk.Label(
            root,
            textvariable=self.banter_var,
            bg=BG,
            fg=BANTER_COLOR,
            font=("Courier New", 11, "italic"),
            wraplength=760,
            justify="center",
            height=2
        )
        self.banter_label.pack(pady=(0, 2))

        # Avatar + status on the same row so avatar doesn't steal vertical space
        bottom_row = tk.Frame(root, bg=BG)
        bottom_row.pack(pady=(0, 4))

        self.avatar = PongAvatar(bottom_row)
        self.avatar.pack(side=tk.LEFT, padx=(0, 12))

        # Status label
        self.status_var = tk.StringVar(value="Loading GRID companion...")
        self.status_label = tk.Label(
            bottom_row,
            textvariable=self.status_var,
            bg=BG,
            fg=TEXT_DIM,
            font=("Courier New", 8),
        )
        self.status_label.pack(side=tk.LEFT)

        self.reset_game()
        self.draw_background()

        # Held-key tracking — stores which keys are currently pressed.
        # This bypasses OS key-repeat delay entirely: the game loop reads
        # _keys_held every frame and moves the paddle at a fixed speed.
        self._keys_held = set()

        self.root.bind("<w>",            lambda e: self._keys_held.add("up"))
        self.root.bind("<KeyRelease-w>", lambda e: self._keys_held.discard("up"))
        self.root.bind("<Up>",            lambda e: self._keys_held.add("up"))
        self.root.bind("<KeyRelease-Up>", lambda e: self._keys_held.discard("up"))
        self.root.bind("<s>",             lambda e: self._keys_held.add("down"))
        self.root.bind("<KeyRelease-s>",  lambda e: self._keys_held.discard("down"))
        self.root.bind("<Down>",             lambda e: self._keys_held.add("down"))
        self.root.bind("<KeyRelease-Down>",  lambda e: self._keys_held.discard("down"))

        self.root.bind("<space>",  self.on_space)
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("<p>",      lambda e: self.toggle_pause())
        self.root.bind("<P>",      lambda e: self.toggle_pause())
        self.root.focus_set()

        self._paused = False
        self._game_loop_id = None
        self._poll_id = None

        # Launch LLM worker subprocess (own GIL — can't freeze tkinter)
        self._start_llm_subprocess()

        self.state       = "waiting"   # waiting / playing / scored / gameover
        self.rally_count = 0
        self.last_banter_event = ""
        self.show_start_screen()
        self.game_loop()

    def _start_llm_subprocess(self):
        global _llm_req_q, _llm_res_q
        _llm_req_q = multiprocessing.Queue(maxsize=1)
        _llm_res_q = multiprocessing.Queue()
        self._llm_proc = multiprocessing.Process(
            target=_llm_worker,
            args=(MODELS, _llm_req_q, _llm_res_q),
            daemon=True,
        )
        self._llm_proc.start()
        # Begin polling for status/banter messages from the worker
        self._poll_llm_responses()

    def _poll_llm_responses(self):
        """Called every 200 ms via after().  Drains the response queue."""
        global current_banter, banter_history
        try:
            while True:
                kind, payload = _llm_res_q.get_nowait()
                if kind == "status":
                    if payload == "ready":
                        self.status_var.set("GRID is online — Press SPACE to play")
                        self.avatar.set_state("waiting")
                        # Only greet if still on start screen — not mid-game
                        if self.state == "waiting":
                            generate_banter("game_start")
                    elif payload == "no_model":
                        self.status_var.set("GRID offline (no model found) — Press SPACE to play")
                        self.avatar.set_state("idle")
                    else:  # "error"
                        self.status_var.set("GRID offline (load error) — Press SPACE to play")
                        self.avatar.set_state("idle")
                elif kind == "banter":
                    banter_history.append(payload)
                    if len(banter_history) > MAX_BANTER_HISTORY:
                        banter_history.pop(0)
                    current_banter = f"GRID: {payload}"
                    self.banter_var.set("")
                    self.banter_var.set(current_banter)
        except Exception:
            pass  # Queue empty or process not started yet
        self._poll_id = self.root.after(200, self._poll_llm_responses)

    def _open_diff_menu(self):
        menu = tk.Menu(self.root, tearoff=0, bg="#001100", fg=TEXT_BRIGHT,
                       activebackground="#002200", activeforeground=TEXT_CYAN,
                       font=("Courier New", 9), bd=0, relief="flat")
        for key in DIFFICULTIES:
            marker = "  <" if key == self._difficulty else ""
            menu.add_command(
                label=f"  {key}{marker}",
                command=lambda k=key: self._set_difficulty(k)
            )
        x = self._diff_btn.winfo_rootx()
        y = self._diff_btn.winfo_rooty() + self._diff_btn.winfo_height()
        menu.tk_popup(x, y)

    def _set_difficulty(self, key):
        self._difficulty = key
        self._diff_btn.config(text=f"{key} ▾")

    def _open_speed_menu(self):
        menu = tk.Menu(self.root, tearoff=0, bg="#001100", fg=TEXT_BRIGHT,
                       activebackground="#002200", activeforeground=TEXT_CYAN,
                       font=("Courier New", 9), bd=0, relief="flat")
        for key in BALL_SPEEDS:
            marker = "  <" if key == self._ball_speed_key else ""
            menu.add_command(
                label=f"  {key}  ({BALL_SPEEDS[key]}){marker}",
                command=lambda k=key: self._set_ball_speed(k)
            )
        x = self._speed_btn.winfo_rootx()
        y = self._speed_btn.winfo_rooty() + self._speed_btn.winfo_height()
        menu.tk_popup(x, y)

    def _set_ball_speed(self, key):
        self._ball_speed_key = key
        self._speed_btn.config(text=f"{key} ▾")

    def reset_game(self):
        global banter_history, current_banter
        self.player_y  = H // 2 - PAD_H // 2
        self.ai_y      = H // 2 - PAD_H // 2
        self.player_score = 0
        self.ai_score     = 0
        self.last_banter_event = ""
        banter_history = []  # Reset history each new game
        current_banter = ""
        self.reset_ball()

    def reset_ball(self):
        self.ball_x  = W // 2
        self.ball_y  = H // 2
        angle_y      = random.uniform(-3, 3)
        direction    = random.choice([-1, 1])
        speed        = BALL_SPEEDS.get(self._ball_speed_key, BALL_SPEED)
        self.ball_vx = speed * direction
        self.ball_vy = angle_y
        self.rally_count = 0
        self.last_banter_event = ""  # Allow long_rally banter to fire each new point

    def draw_background(self):
        # Guard: only draw the static background once. If called again (e.g. on
        # a future resize hook), don't pile up duplicate permanent canvas items.
        if getattr(self, '_background_drawn', False):
            return
        self._background_drawn = True
        # Grid lines
        for x in range(0, W, 60):
            self.canvas.create_line(x, 0, x, H, fill=GRID_COLOR, width=1)
        for y in range(0, H, 60):
            self.canvas.create_line(0, y, W, y, fill=GRID_COLOR, width=1)
        # Scanlines via PhotoImage tile — much faster than hundreds of line objects
        tile = tk.PhotoImage(width=1, height=4)
        tile.put(SCANLINE, to=(0, 0, 1, 1))
        for y in range(0, H, 4):
            self.canvas.create_image(0, y, image=tile, anchor="nw", tags="scanline")
        self.canvas._scanline_tile = tile  # Keep reference to prevent GC
        # Net
        for y in range(0, H, 20):
            self.canvas.create_rectangle(W//2 - 2, y, W//2 + 2, y + 10,
                                          fill=NET_COLOR, outline="", tags="net")

    def show_start_screen(self):
        self.canvas.delete("overlay")
        self.canvas.create_text(W//2, H//2 - 80, text="NEURAL_PC",
            font=("Courier New", 36, "bold"), fill=TEXT_BRIGHT, tags="overlay")
        self.canvas.create_text(W//2, H//2 - 30, text="P O N G",
            font=("Courier New", 22, "bold"), fill=TEXT_CYAN, tags="overlay")
        self.canvas.create_text(W//2, H//2 + 30,
            text="W / S  or  ↑ / ↓  to move",
            font=("Courier New", 12), fill=TEXT_DIM, tags="overlay")
        self.canvas.create_text(W//2, H//2 + 60,
            text="First to 7 wins",
            font=("Courier New", 12), fill=TEXT_DIM, tags="overlay")
        self.canvas.create_text(W//2, H//2 + 100,
            text="[ SPACE ] to start",
            font=("Courier New", 14, "bold"), fill=TEXT_ORANGE, tags="overlay")
        self.canvas.create_text(W//2, H - 20,
            text="ESC to quit  |  P to pause",
            font=("Courier New", 9), fill=TEXT_DIM, tags="overlay")

    def toggle_pause(self):
        # Only allow pause during active play or while already paused
        if self.state not in ("playing", "scored") and not self._paused:
            return
        self._paused = not self._paused
        if self._paused:
            self._pause_btn.config(text="▶ RESUME", fg=TEXT_ORANGE)
            self._keys_held.clear()  # Drop held keys so paddle doesn't lurch on resume
            self.canvas.delete("pause_overlay")
            self.canvas.create_text(
                W // 2, H // 2 - 20,
                text="PAUSED",
                font=("Courier New", 36, "bold"),
                fill=TEXT_ORANGE,
                tags="pause_overlay",
            )
            self.canvas.create_text(
                W // 2, H // 2 + 30,
                text="[ P ] to resume",
                font=("Courier New", 12),
                fill=TEXT_DIM,
                tags="pause_overlay",
            )
        else:
            self._pause_btn.config(text="⏸ PAUSE", fg=TEXT_DIM)
            self.canvas.delete("pause_overlay")

    def restart_game(self):
        # Cancel any stale scheduled callbacks so we don't accumulate duplicate loops
        if self._game_loop_id is not None:
            self.root.after_cancel(self._game_loop_id)
            self._game_loop_id = None
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None

        # Drain the LLM response queue so stale banter/status doesn't replay
        if _llm_res_q is not None:
            try:
                while True:
                    _llm_res_q.get_nowait()
            except Exception:
                pass

        self._paused = False
        self._pause_btn.config(text="⏸ PAUSE", fg=TEXT_DIM)
        self._keys_held.clear()
        self.canvas.delete("pause_overlay")
        self.reset_game()
        self.canvas.delete("overlay")
        self.state = "waiting"
        self.show_start_screen()
        self.banter_var.set("")
        self.avatar.set_state("waiting")

        # Restart both loops fresh
        self._poll_id = self.root.after(200, self._poll_llm_responses)
        self.game_loop()

    def on_space(self, event=None):
        if self.state == "waiting":
            self.canvas.delete("overlay")
            self.state = "playing"
            self.avatar.set_state("playing")
        elif self.state == "scored":
            self.reset_ball()
            self.state = "playing"
            self.avatar.set_state("playing")
        elif self.state == "gameover":
            self.reset_game()
            self.canvas.delete("overlay")
            self.state = "waiting"
            self.show_start_screen()
            self.banter_var.set("")
            self.avatar.set_state("waiting")

    def update_player(self):
        if self.state != "playing":
            return
        if "up" in self._keys_held:
            self.player_y = max(0, self.player_y - PLAYER_SPEED)
        if "down" in self._keys_held:
            self.player_y = min(H - PAD_H, self.player_y + PLAYER_SPEED)

    def update_ai(self):
        diff    = DIFFICULTIES.get(self._difficulty, DIFFICULTIES[DEFAULT_DIFFICULTY])
        ai_spd  = diff["ai_speed"]
        mistake = diff["ai_mistake"]
        # AI tracks ball with intentional mistakes scaled by difficulty
        if random.random() < mistake:
            return
        target = self.ball_y - PAD_H // 2
        gap = target - self.ai_y
        # Dead zone — stop jittering when close enough
        if abs(gap) < ai_spd:
            return
        if gap > 0:
            self.ai_y = min(H - PAD_H, self.ai_y + ai_spd)
        else:
            self.ai_y = max(0, self.ai_y - ai_spd)

    def update_ball(self):
        self.ball_x += self.ball_vx
        self.ball_y += self.ball_vy

        # Avatar reacts when ball is coming toward GRID's side (right half, moving right)
        if self.ball_vx > 0 and self.ball_x > W // 2:
            if self.avatar._state not in ("ball_coming", "scored_ai", "scored_player",
                                          "long_rally", "ai_wins", "player_wins"):
                self.avatar.set_state("ball_coming", revert_to="playing")
        elif self.ball_vx < 0 and self.avatar._state == "ball_coming":
            self.avatar.set_state("playing")

        # Top/bottom bounce
        if self.ball_y <= 0:
            self.ball_y = 0
            self.ball_vy *= -1
        if self.ball_y >= H - BALL_SIZE:
            self.ball_y = H - BALL_SIZE
            self.ball_vy *= -1

        # Player paddle collision (left side)
        px = 30
        if (self.ball_x <= px + PAD_W and
                self.ball_y + BALL_SIZE >= self.player_y and
                self.ball_y <= self.player_y + PAD_H and
                self.ball_vx < 0):
            self.ball_vx = min(MAX_BALL_SPEED, abs(self.ball_vx) + 0.3) 
            hit_pos = (self.ball_y - self.player_y) / PAD_H
            self.ball_vy = (hit_pos - 0.5) * 8
            self.rally_count += 1
            self.check_rally_banter()

        # AI paddle collision (right side)
        ax = W - 30 - PAD_W
        if (self.ball_x + BALL_SIZE >= ax and
                self.ball_y + BALL_SIZE >= self.ai_y and
                self.ball_y <= self.ai_y + PAD_H and
                self.ball_vx > 0):
            self.ball_vx = -min(MAX_BALL_SPEED, abs(self.ball_vx) + 0.3)
            hit_pos = (self.ball_y - self.ai_y) / PAD_H
            self.ball_vy = (hit_pos - 0.5) * 8
            self.rally_count += 1
            self.check_rally_banter()

        # Score — ball exits left
        if self.ball_x < 0:
            self.ai_score += 1
            self.state = "scored"
            self.trigger_score_banter(scorer="ai")
            return

        # Score — ball exits right
        if self.ball_x > W:
            self.player_score += 1
            self.state = "scored"
            self.trigger_score_banter(scorer="player")
            return

    def trigger_score_banter(self, scorer):
        ps = self.player_score
        ai = self.ai_score
        diff = abs(ps - ai)
        score_ctx = f"Current score: you {ai} — friend {ps}."

        if ps >= SCORE_TO_WIN:
            self.state = "gameover"
            self.show_gameover("PLAYER")
            self.avatar.set_state("player_wins", revert_to="idle")
            generate_banter("player_wins", f"Final score: you {ai} — friend {ps}.")
            return
        if ai >= SCORE_TO_WIN:
            self.state = "gameover"
            self.show_gameover("GRID")
            self.avatar.set_state("ai_wins", revert_to="idle")
            generate_banter("ai_wins", f"Final score: you {ai} — friend {ps}.")
            return

        # React to the point with avatar then return to playing
        if scorer == "player":
            self.avatar.set_state("scored_player", revert_to="playing")
        else:
            self.avatar.set_state("scored_ai", revert_to="playing")

        if diff <= 1 and ps + ai > 4 and random.random() < 0.4:
            generate_banter("close_game", score_ctx)
        elif scorer == "player":
            if ps > ai and diff >= 2:
                generate_banter("player_winning", score_ctx)
            else:
                generate_banter("player_scores", score_ctx)
        else:
            if ai > ps and diff >= 2:
                generate_banter("ai_winning", score_ctx)
            else:
                generate_banter("ai_scores", score_ctx)

    def check_rally_banter(self):
        if self.rally_count >= 8 and self.last_banter_event != "long_rally":
            self.last_banter_event = "long_rally"
            self.avatar.set_state("long_rally", revert_to="playing")
            generate_banter("long_rally")

    def show_gameover(self, winner):
        self.canvas.delete("overlay")
        if winner == "PLAYER":
            title = "YOU WIN!"
            color = TEXT_CYAN
        else:
            title = "GRID WINS"
            color = TEXT_ORANGE

        self.canvas.create_text(W//2, H//2 - 70, text=title,
            font=("Courier New", 42, "bold"), fill=color, tags="overlay")
        self.canvas.create_text(W//2, H//2,
            text=f"{self.player_score}  —  {self.ai_score}",
            font=("Courier New", 28, "bold"), fill=TEXT_BRIGHT, tags="overlay")
        self.canvas.create_text(W//2, H//2 + 60,
            text="[ SPACE ] to play again",
            font=("Courier New", 13, "bold"), fill=TEXT_ORANGE, tags="overlay")

    def draw_frame(self):
        self.canvas.delete("game")

        # Scores
        self.canvas.create_text(W//4, 40,
            text=str(self.player_score),
            font=("Courier New", 48, "bold"),
            fill=SCORE_COLOR, tags="game")
        self.canvas.create_text(3*W//4, 40,
            text=str(self.ai_score),
            font=("Courier New", 48, "bold"),
            fill=SCORE_COLOR, tags="game")

        # Labels
        self.canvas.create_text(W//4, 80,
            text="YOU", font=("Courier New", 10),
            fill=TEXT_DIM, tags="game")
        self.canvas.create_text(3*W//4, 80,
            text="GRID", font=("Courier New", 10),
            fill=TEXT_DIM, tags="game")

        # Player paddle
        px = 30
        self.canvas.create_rectangle(
            px, self.player_y,
            px + PAD_W, self.player_y + PAD_H,
            fill=PAD_PLAYER, outline="", tags="game"
        )
        # Glow effect on player paddle
        self.canvas.create_rectangle(
            px - 2, self.player_y - 2,
            px + PAD_W + 2, self.player_y + PAD_H + 2,
            fill="", outline="#003300", width=1, tags="game"
        )

        # AI paddle
        ax = W - 30 - PAD_W
        self.canvas.create_rectangle(
            ax, self.ai_y,
            ax + PAD_W, self.ai_y + PAD_H,
            fill=PAD_AI, outline="", tags="game"
        )
        self.canvas.create_rectangle(
            ax - 2, self.ai_y - 2,
            ax + PAD_W + 2, self.ai_y + PAD_H + 2,
            fill="", outline="#003300", width=1, tags="game"
        )

        # Ball
        self.canvas.create_oval(
            self.ball_x, self.ball_y,
            self.ball_x + BALL_SIZE, self.ball_y + BALL_SIZE,
            fill=BALL_COLOR, outline="", tags="game"
        )
        # Ball glow
        self.canvas.create_oval(
            self.ball_x - 3, self.ball_y - 3,
            self.ball_x + BALL_SIZE + 3, self.ball_y + BALL_SIZE + 3,
            fill="", outline="#004444", width=1, tags="game"
        )

        # State messages
        if self.state == "scored":
            self.canvas.create_text(W//2, H//2,
                text="[ SPACE ] to serve",
                font=("Courier New", 13), fill=TEXT_ORANGE, tags="game")

        # Banter is now updated directly by _poll_llm_responses — no per-frame check needed

    def game_loop(self):
        if self.state == "playing" and not self._paused:
            self.update_player()
            self.update_ai()
            self.update_ball()
        self.draw_frame()
        self._game_loop_id = self.root.after(16, self.game_loop)  # ~60fps


# ── Entry point ───────────────────────────────────
def launch_pong():
    root = tk.Tk()
    root.configure(bg=BG)
    # Center horizontally/vertically on screen. We set width but NOT height —
    # the canvas is H px tall and two labels are packed below it, so letting Tk
    # measure the full height with update_idletasks() avoids clipping those labels.
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    # Build the game first so Tk knows the real window height
    game = PongGame(root)
    root.update_idletasks()
    wh = root.winfo_reqheight()  # Actual height including canvas + labels
    x = (sw - W) // 2
    y = max(0, (sh - wh) // 2)
    root.geometry(f"{W}x{wh}+{x}+{y}")
    root.mainloop()

if __name__ == "__main__":
    # freeze_support() is a no-op on non-frozen builds but required for
    # PyInstaller / py2app frozen executables on Windows & macOS.
    multiprocessing.freeze_support()
    # Force 'spawn' start method so the child process gets a clean Python
    # interpreter without inheriting tkinter state (critical on macOS where
    # the default is 'fork' and on Windows where it's already 'spawn').
    multiprocessing.set_start_method("spawn", force=True)
    launch_pong()
