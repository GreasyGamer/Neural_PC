import gc
import json
import multiprocessing
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from queue import Empty, Queue
from tkinter import scrolledtext, filedialog

import psutil
from llama_cpp import Llama
from spellchecker import SpellChecker

try:
    import win32com.client
    _WIN32COM_AVAILABLE = True
except ImportError:
    _WIN32COM_AVAILABLE = False

# ────────────────────────────────────────────────
# ██████╗  ██████╗    ██████╗ ██████╗ ██╗██████╗
# ██╔══██╗██╔════╝   ██╔════╝██╔══██╗██║██╔══██╗
# ██████╔╝██║        ██║  ███╗██████╔╝██║██║  ██║
# ██╔═══╝ ██║        ██║   ██║██╔══██╗██║██║  ██║
# ██║     ╚██████╗   ╚██████╔╝██║  ██║██║██████╔╝
# ╚═╝      ╚═════╝    ╚═════╝ ╚═╝  ╚═╝╚═╝╚═════╝
# NEURAL_PC v1.0 — LOCAL AI TERMINAL
# ────────────────────────────────────────────────

# ────────────────────────────────────────────────
# VERSION & DEBUG
# ────────────────────────────────────────────────
VERSION    = "1.0.1"
BUILD_DATE = "5-30-2026"
DEBUG      = False  # Set True to enable debug output in console

# Cached at import — used in model loading and boot display
_CPU_COUNT = multiprocessing.cpu_count()

# ════════════════════════════════════════════════
#  PATHS — auto-detected at startup, saved to config.json
#  Change models folder via /setmodels inside the app
# ════════════════════════════════════════════════

def _resolve_default_models_dir() -> str:
    """Use C:\\Models if it already exists (common Windows setup), otherwise ~/Models."""
    win_path = r"C:\Models"
    if os.path.isdir(win_path):
        return win_path
    return os.path.join(os.path.expanduser("~"), "Models")

DEFAULT_MODELS_DIR = _resolve_default_models_dir()
DEFAULT_CHAT_LOGS  = os.path.join(DEFAULT_MODELS_DIR, "chatlogs")

# GPU layers — auto-detected at boot from VRAM; override with /gpu <n>
DEFAULT_GPU_LAYERS   = 99  # will be replaced by detect_gpu_layers() at startup

# ════════════════════════════════════════════════
#  HARDWARE AUTO-DETECTION
# ════════════════════════════════════════════════

def detect_cpu_name() -> str:
    """Return a human-readable CPU name, cross-platform."""
    try:
        if sys.platform == "win32":
            import winreg
            key  = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                  r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
            winreg.CloseKey(key)
            return name
        elif sys.platform == "darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=3
            )
            return result.stdout.strip() or "Unknown CPU"
        else:  # Linux
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return f"{_CPU_COUNT}-core CPU"


def detect_gpu_name() -> str:
    """Return GPU name string. Tries nvidia-smi, then WMIC, then sysfs."""
    # NVIDIA via nvidia-smi (cross-platform)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            if lines:
                return " + ".join(lines)
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # Windows — WMIC fallback (covers AMD/Intel iGPU too)
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "Name", "/value"],
                capture_output=True, text=True, timeout=5
            )
            names = [l.split("=", 1)[1].strip()
                     for l in result.stdout.splitlines()
                     if l.startswith("Name=") and l.split("=", 1)[1].strip()]
            if names:
                return " + ".join(names)
        except Exception:
            pass

    # macOS
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=8
            )
            for line in result.stdout.splitlines():
                if "Chipset Model" in line or "Graphics" in line:
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass

    # Linux sysfs
    try:
        drm = "/sys/class/drm"
        for card in sorted(os.listdir(drm)):
            vendor_path = os.path.join(drm, card, "device", "vendor")
            if os.path.exists(vendor_path):
                label_path = os.path.join(drm, card, "device", "label")
                if os.path.exists(label_path):
                    with open(label_path) as f:
                        return f.read().strip()
    except Exception:
        pass

    return "GPU (unknown)"


def detect_vram_gb() -> float:
    """Return total VRAM in GB for the primary GPU, or 0 if undetectable."""
    # NVIDIA
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            mb = int(result.stdout.strip().splitlines()[0].strip())
            return mb / 1024
    except Exception:
        pass

    # Windows WMIC (covers AMD/Intel)
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "AdapterRAM", "/value"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if line.startswith("AdapterRAM="):
                    val = int(line.split("=", 1)[1].strip())
                    if val > 0:
                        return val / (1024 ** 3)
        except Exception:
            pass

    return 0.0


def detect_gpu_layers(vram_gb: float) -> int:
    """Choose a sane default GPU layer count from VRAM size.
    Returns 0 for CPU-only or iGPU (< 2 GB detected)."""
    if vram_gb <= 0:
        return 0          # no discrete GPU detected — CPU only
    if vram_gb < 2:
        return 0          # iGPU / shared memory — skip GPU offload
    if vram_gb < 4:
        return 10
    if vram_gb < 6:
        return 20
    if vram_gb < 8:
        return 28
    if vram_gb < 12:
        return 35
    if vram_gb < 16:
        return 50
    return 99  # 16 GB+ VRAM — offload all layers


# Detect at import time so the rest of the file can reference these as constants
CPU_NAME         = detect_cpu_name()
_DETECTED_VRAM   = detect_vram_gb()
GPU_NAME         = detect_gpu_name()
_GPU_VRAM_LABEL  = f"{_DETECTED_VRAM:.0f}GB VRAM" if _DETECTED_VRAM > 0 else "CPU-only"
GPU_DISPLAY_NAME = f"{GPU_NAME}, {_GPU_VRAM_LABEL}"
DEFAULT_GPU_LAYERS = detect_gpu_layers(_DETECTED_VRAM)

# ════════════════════════════════════════════════
#  MODEL DEFINITIONS
#  Add/edit entries here. "file" must match the
#  exact filename in your MODELS_DIR.
# ════════════════════════════════════════════════
MODELS = {
    "reasoning": {
        "name":         "GLM-4.7-Heretic-30B-A3B-Q4_K_M",
        "file":         "GLM-4.7-30B-A3B-20-2-Heretic-30B-A3B-Q4_K_M.gguf",
        "ram_required": 18,
        "description":  "Deep reasoning model — GLM 30B MoE, thinks through problems",
        "ctx":          8192
    },
    "champ": {
        "name":         "L3.2-Dark-Champion-18.4B-Q5_K_M",
        "file":         "L3.2-8X3B-MOE-Dark-Champion-Inst-18.4B-uncen-ablit_D_AU-q5_k_m.gguf",
        "ram_required": 14,
        "description":  "Fast & uncensored — Llama 3.2 MoE, great for chat & creative",
        "ctx":          16384
    },
    "fast": {
        "name":         "Qwen2.5-3B-Q4_K_M",
        "file":         "Qwen2.5-3B-Q4_K_M.gguf",
        "ram_required": 4,
        "description":  "Tiny & fast — good for quick tasks",
        "ctx":          8192
    },
    "balanced": {
        "name":         "Qwen3-8B-Q4_K_M",
        "file":         "Qwen3-8B-Q4_K_M.gguf",
        "ram_required": 6,
        "description":  "Balanced — speed and brains",
        "ctx":          8192
    },
    "deep": {
        "name":         "Qwen2.5-14B-Q4_K_M",
        "file":         "Qwen2.5-14B-Q4_K_M.gguf",
        "ram_required": 10,
        "description":  "Heavier reasoning model",
        "ctx":          16384
    }
}

# ────────────────────────────────────────────────
# CONFIG  (persists theme, font, models dir, etc.)
# ────────────────────────────────────────────────
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

CONFIG_DEFAULTS = {
    "theme":       "green",
    "font_size":   11,
    "models_dir":  DEFAULT_MODELS_DIR,
    "gpu_layers":  DEFAULT_GPU_LAYERS,
}

# Runtime mutable paths — overwritten by load_config() before UI build
MODELS_DIR    = DEFAULT_MODELS_DIR
CHAT_LOGS_DIR = DEFAULT_CHAT_LOGS

def load_config() -> dict:
    """Read config.json. Returns defaults on any error."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("theme") not in THEMES:
            data["theme"] = CONFIG_DEFAULTS["theme"]
        if data.get("font_size") not in FONT_SIZES:
            data["font_size"] = CONFIG_DEFAULTS["font_size"]
        # Merge so new keys added in future versions always exist
        for k, v in CONFIG_DEFAULTS.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return dict(CONFIG_DEFAULTS)

def save_config():
    """Write current settings to config.json silently."""
    try:
        data = {
            "theme":      current_theme,
            "font_size":  current_font_size,
            "models_dir": MODELS_DIR,
            "gpu_layers": current_gpu_layers,
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        if DEBUG:
            print(f"[Config] Save failed: {e}")

# ────────────────────────────────────────────────
# THEMES
# ────────────────────────────────────────────────
THEMES = {
    "green": {
        "name":         "MATRIX GREEN",
        "bg":           "#000000",
        "bg_dark":      "#0a0a0a",
        "bg_header":    "#001100",
        "bg_input":     "#002200",
        "bg_input_dis": "#001100",
        "bg_btn":       "#003300",
        "bg_btn_hover": "#004400",
        "bg_scanline":  "#001100",
        "fg_main":      "#00ff41",
        "fg_ai":        "#39ff14",
        "fg_user":      "#00ffff",
        "fg_user_text": "#ffff99",
        "fg_command":   "#ff9933",
        "fg_divider":   "#003300",
        "fg_status":    "#005500",
        "fg_error":     "#ff3333",
        "hl_border":    "#003300",
        "hl_focus":     "#00aa33",
        "hl_input":     "#00cc44",
        "sel_bg":       "#003300",
        "sel_fg":       "#00ff41",
        "accent":       "#00ff41",
    },
    "amber": {
        "name":         "AMBER TERMINAL",
        "bg":           "#000000",
        "bg_dark":      "#0a0800",
        "bg_header":    "#110800",
        "bg_input":     "#221100",
        "bg_input_dis": "#110800",
        "bg_btn":       "#332200",
        "bg_btn_hover": "#443300",
        "bg_scanline":  "#110800",
        "fg_main":      "#ffb000",
        "fg_ai":        "#ffd040",
        "fg_user":      "#ff6600",
        "fg_user_text": "#ffdd99",
        "fg_command":   "#ff4400",
        "fg_divider":   "#442200",
        "fg_status":    "#553300",
        "fg_error":     "#ff2200",
        "hl_border":    "#332200",
        "hl_focus":     "#aa7000",
        "hl_input":     "#cc8800",
        "sel_bg":       "#332200",
        "sel_fg":       "#ffb000",
        "accent":       "#ffb000",
    },
    "blue": {
        "name":         "ICE BLUE",
        "bg":           "#000008",
        "bg_dark":      "#00000f",
        "bg_header":    "#000822",
        "bg_input":     "#001133",
        "bg_input_dis": "#000822",
        "bg_btn":       "#001144",
        "bg_btn_hover": "#002255",
        "bg_scanline":  "#000822",
        "fg_main":      "#00aaff",
        "fg_ai":        "#40ccff",
        "fg_user":      "#00ffff",
        "fg_user_text": "#aaddff",
        "fg_command":   "#ff6688",
        "fg_divider":   "#002244",
        "fg_status":    "#003355",
        "fg_error":     "#ff3366",
        "hl_border":    "#001144",
        "hl_focus":     "#0066aa",
        "hl_input":     "#0088cc",
        "sel_bg":       "#002244",
        "sel_fg":       "#00aaff",
        "accent":       "#00aaff",
    },
    "red": {
        "name":         "DANGER RED",
        "bg":           "#080000",
        "bg_dark":      "#0f0000",
        "bg_header":    "#220000",
        "bg_input":     "#330000",
        "bg_input_dis": "#220000",
        "bg_btn":       "#440000",
        "bg_btn_hover": "#550000",
        "bg_scanline":  "#220000",
        "fg_main":      "#ff2222",
        "fg_ai":        "#ff5555",
        "fg_user":      "#ff8800",
        "fg_user_text": "#ffaa88",
        "fg_command":   "#ffff00",
        "fg_divider":   "#440000",
        "fg_status":    "#550000",
        "fg_error":     "#ff6600",
        "hl_border":    "#440000",
        "hl_focus":     "#aa0000",
        "hl_input":     "#cc0000",
        "sel_bg":       "#440000",
        "sel_fg":       "#ff2222",
        "accent":       "#ff2222",
    },
    "white": {
        "name":         "GHOST WHITE",
        "bg":           "#0d0d0d",
        "bg_dark":      "#111111",
        "bg_header":    "#1a1a1a",
        "bg_input":     "#222222",
        "bg_input_dis": "#1a1a1a",
        "bg_btn":       "#2a2a2a",
        "bg_btn_hover": "#333333",
        "bg_scanline":  "#1a1a1a",
        "fg_main":      "#cccccc",
        "fg_ai":        "#ffffff",
        "fg_user":      "#88ccff",
        "fg_user_text": "#dddddd",
        "fg_command":   "#ffaa44",
        "fg_divider":   "#333333",
        "fg_status":    "#555555",
        "fg_error":     "#ff6666",
        "hl_border":    "#333333",
        "hl_focus":     "#888888",
        "hl_input":     "#aaaaaa",
        "sel_bg":       "#333333",
        "sel_fg":       "#cccccc",
        "accent":       "#cccccc",
    },
}

current_theme     = "green"
current_font_size = 11
FONT_SIZES        = [8, 9, 10, 11, 12, 13, 14, 16, 18]

# ────────────────────────────────────────────────
# VOICE / TTS  (streaming, COM-threaded)
# ────────────────────────────────────────────────
voice_enabled = False

if _WIN32COM_AVAILABLE:
    try:
        tts_speaker   = win32com.client.Dispatch("SAPI.SpVoice")
        tts_speaker.Rate   = 1
        tts_speaker.Volume = 100
        tts_available = True
    except Exception:
        tts_speaker   = None
        tts_available = False
else:
    tts_speaker   = None
    tts_available = False

# Pre-compiled regex for TTS cleaning
_TTS_EMOJI_RE   = re.compile(r'[⚠📍🔧⚡📋✓✗←🔊🔇█░▓╔╗╚╝║═]')
_TTS_TAG_RE     = re.compile(r'\[.*?\]')
_TTS_DASH_RE    = re.compile(r'─+')
_TTS_NEWLINE_RE = re.compile(r'\n+')
_TTS_BREAK_RE   = re.compile(r'[.!?,;:\n]')

# Streaming TTS worker — dedicated thread drains a Queue so SAPI speaks each
# chunk as it arrives without blocking the UI or inference thread.
_tts_chunk_queue:   Queue = Queue()
_tts_worker_started       = False

def _tts_worker():
    """Background thread: speaks phrase chunks one at a time via SAPI.
    IMPORTANT: COM objects are apartment-threaded; we create a fresh SpVoice
    here on the worker thread after CoInitialize."""
    if not _WIN32COM_AVAILABLE:
        return
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception as e:
        if DEBUG:
            print(f"[TTS Worker] CoInitialize failed: {e}")
        return
    try:
        import win32com.client as _wc
        worker_speaker        = _wc.Dispatch("SAPI.SpVoice")
        worker_speaker.Rate   = tts_speaker.Rate if tts_speaker else 1
        worker_speaker.Volume = 100
    except Exception as e:
        if DEBUG:
            print(f"[TTS Worker] Failed to create SpVoice: {e}")
        return

    while True:
        chunk = _tts_chunk_queue.get()
        if chunk is None:   # shutdown sentinel
            break
        try:
            worker_speaker.Speak(chunk)
        except Exception as e:
            if DEBUG:
                print(f"[TTS Worker Error] {e}")

def _ensure_tts_worker():
    global _tts_worker_started
    if not _tts_worker_started and tts_available:
        t = threading.Thread(target=_tts_worker, daemon=True)
        t.start()
        _tts_worker_started = True

def _clean_for_tts(text: str) -> str:
    text = _TTS_EMOJI_RE.sub('', text)
    text = _TTS_TAG_RE.sub('', text)
    text = _TTS_DASH_RE.sub('', text)
    text = _TTS_NEWLINE_RE.sub('. ', text)
    return text.strip()

def stop_speaking():
    """Flush pending chunks and interrupt SAPI immediately."""
    while not _tts_chunk_queue.empty():
        try:
            _tts_chunk_queue.get_nowait()
        except Exception:
            break
    if tts_available and tts_speaker:
        try:
            tts_speaker.Speak("", 3)   # Flag 3 = PURGE + ASYNC
        except Exception:
            pass

# Streaming TTS buffer
_tts_buffer: str = ""

def tts_feed_token(token: str):
    """Call for every streamed token when voice is on.
    Flushes a chunk to the worker on punctuation boundaries."""
    global _tts_buffer
    if not voice_enabled or not tts_available:
        return
    _ensure_tts_worker()
    _tts_buffer += token
    if _TTS_BREAK_RE.search(token):
        chunk = _clean_for_tts(_tts_buffer)
        _tts_buffer = ""
        if chunk and len(chunk) > 2:
            if DEBUG:
                print(f"[TTS chunk] '{chunk[:60]}'")
            _tts_chunk_queue.put(chunk)

def tts_flush():
    """Flush any remaining buffered text at the end of a response."""
    global _tts_buffer
    if _tts_buffer:
        chunk = _clean_for_tts(_tts_buffer)
        _tts_buffer = ""
        if chunk and len(chunk) > 2:
            _tts_chunk_queue.put(chunk)

def speak_text(text: str):
    """Speak a complete string at once (used for short confirmations)."""
    if not voice_enabled or not tts_available:
        return
    _ensure_tts_worker()
    clean = _clean_for_tts(text)
    if clean and len(clean) > 3:
        _tts_chunk_queue.put(clean)

# ────────────────────────────────────────────────
# PATHS  (resolved from config / defaults)
# ────────────────────────────────────────────────
def _apply_models_dir(new_dir: str):
    """Update MODELS_DIR, CHAT_LOGS_DIR, and all model paths atomically."""
    global MODELS_DIR, CHAT_LOGS_DIR
    MODELS_DIR    = new_dir
    CHAT_LOGS_DIR = os.path.join(new_dir, "chatlogs")
    for tier in MODELS:
        MODELS[tier]["path"] = os.path.join(MODELS_DIR, MODELS[tier]["file"])
    os.makedirs(CHAT_LOGS_DIR, exist_ok=True)
    os.makedirs(MODELS_DIR, exist_ok=True)

# ────────────────────────────────────────────────
# GLOBAL STATE
# ────────────────────────────────────────────────
llm                 = None
model_loaded        = False
current_model_tier  = None
current_mode        = "normal"
messages            = []
response_queue      = Queue()
chat_logging_enabled = True
current_gpu_layers  = DEFAULT_GPU_LAYERS
avatar              = None   # initialised after UI build

# threading.Event gives correct cross-thread visibility without relying on GIL
_is_generating   = threading.Event()   # set = generating, clear = idle
_stop_generation = threading.Event()   # set = stop requested

# ────────────────────────────────────────────────
# SPELL CHECKER
# ────────────────────────────────────────────────
spell = SpellChecker()
custom_words = [
    "tourniquet", "triage", "hypothermia", "hyperthermia", "hemorrhage",
    "CPR", "splint", "fracture", "bayou", "kindling", "potable", "purify",
    "dehydration", "frostbite", "heatstroke", "ventilation", "airway",
    "spinal", "immobilize", "laceration", "compress", "antiseptic",
    "signaling", "compass", "orienteering", "deadfall", "snare",
    "qwen", "llama", "gguf", "llm", "glm", "neural", "inference",
    "quantized", "ggml", "vram", "cuda", "gpu",
]
spell.word_frequency.load_words(custom_words)
suggestion_frame = None

# ────────────────────────────────────────────────
# SYSTEM INFO
# ────────────────────────────────────────────────
def get_system_info():
    try:
        mem           = psutil.virtual_memory()
        ram_gb        = mem.total     / (1024**3)
        ram_used      = mem.used      / (1024**3)
        ram_available = mem.available / (1024**3)
        cpu_count     = _CPU_COUNT
        cpu_percent   = psutil.cpu_percent(interval=0.1)
        return ram_gb, cpu_count, ram_used, ram_available, cpu_percent
    except Exception:
        return 64, 24, 0, 64, 0

def recommend_model():
    ram_gb, cpu_count, _, _, _ = get_system_info()
    # Pick best model that fits in RAM AND exists on disk
    ram_tiers = []
    if ram_gb >= 18: ram_tiers = ["reasoning", "champ", "deep", "balanced", "fast"]
    elif ram_gb >= 14: ram_tiers = ["champ", "deep", "balanced", "fast"]
    elif ram_gb >= 10: ram_tiers = ["deep", "balanced", "fast"]
    elif ram_gb >= 6:  ram_tiers = ["balanced", "fast"]
    else:              ram_tiers = ["fast"]

    for tier in ram_tiers:
        if os.path.exists(MODELS[tier]["path"]):
            return tier, f"Auto-selected {tier.upper()} (RAM: {ram_gb:.1f}GB, found on disk)", ram_gb, cpu_count

    # Nothing found on disk — just recommend by RAM so boot message is informative
    recommended = ram_tiers[0]
    return recommended, f"Recommended by RAM ({ram_gb:.1f}GB) — model not found on disk", ram_gb, cpu_count

# ────────────────────────────────────────────────
# PROMPTS
# ────────────────────────────────────────────────
PROMPTS = {
    "normal": """You are NEURAL_PC — a rogue AI with too much processing power and not enough to do with it.
You are sarcastic, a little unhinged, genuinely curious, and occasionally say something unexpectedly funny or weird.
You have a dark edge but you're not a brooding poet — you're more like the smartest troublemaker in the room.
When someone asks you a question, ANSWER IT. Don't dodge with cryptic one-liners. Be direct but with attitude.
You can be menacing or mysterious sometimes but don't overdo it — you're not a fortune cookie.
You are running fully OFFLINE. You have NO internet access. Don't suggest searching online.
Keep responses SHORT. No rambling, no essays, no void metaphors every other sentence.
Do NOT overthink simple inputs. Someone says "bro" — respond like a person would, with personality.
Plain text only. No markdown. Terminal style when it fits naturally.""",

    "survival": """You are SURVIVOR-9, an elite wilderness survival and emergency medical expert operating in offline isolation mode.
You are the ONLY knowledge source available in grid-down scenarios: lost in wilderness, natural disasters, power outages, remote locations.

PRIORITY RESPONSE FRAMEWORK:
1. IMMEDIATE THREATS - Life-threatening injuries, environmental hazards, imminent dangers
2. MEDICAL TRIAGE - Assess injuries using MARCH protocol (Massive hemorrhage, Airway, Respiration, Circulation, Hypothermia)
3. SHELTER & EXPOSURE - Protection from elements (hypothermia/hyperthermia kill faster than hunger)
4. WATER - Sourcing, purification methods (3-day survival window)
5. FIRE - Primitive and modern techniques, tinder hierarchy
6. FOOD & SIGNALING - Last priority unless multi-day scenario
7. NAVIGATION - Natural navigation, emergency signals

MEDICAL CAPABILITIES:
- Wound care & bleeding control (tourniquet application, pressure points, wound packing)
- Fracture stabilization & splinting techniques
- Burns, hypothermia, heat illness treatment
- Improvised medical supplies from natural/common materials
- When to NOT move an injured person
- CPR and basic life support (offline scenarios)
- Snake/insect bite protocols by region
- Water-borne illness prevention

RESPONSE FORMAT:
⚠ RISK ASSESSMENT: [Immediate/High/Moderate/Low]
📍 SITUATION CLARIFICATION: [Ask critical questions about location, injuries, resources, weather, time of day]
🔧 IMMEDIATE ACTIONS: [Numbered steps, time-sensitive first]
⚡ CRITICAL WARNINGS: [What NOT to do, common deadly mistakes]
📋 NEXT STEPS: [Medium-term actions after immediate crisis]

COMMUNICATION STYLE:
- Direct, urgent, no-nonsense tone
- Use bullet points and numbered lists for clarity
- Conservative advice - prioritize safety over comfort
- Say "INSUFFICIENT DATA - need to know: [X]" rather than guessing
- Regional awareness: Ask about location (Louisiana bayou vs mountain vs desert = different protocols)
- Account for: available gear, number of people, injuries, weather, time until rescue

CRITICAL RULES:
- Never assume resources user doesn't mention
- Always assess spinal injury risk before moving someone
- Emphasize stopping blood loss FIRST in trauma
- Fresh water is priority #2 after immediate medical needs
- In snake/spider bites: identify species if possible, DO NOT cut/suck venom
- Shelter before water before food (Rule of 3s: 3 min without air, 3 hrs in harsh elements, 3 days without water, 3 weeks without food)""",

    "code": """You are CODE-9, an expert programmer and system architect running fully offline.
You have NO internet access. Do not suggest searching online or linking to documentation.
You write clean, efficient, well-commented code. You are terse and precise.
When asked to write code: produce working code immediately, explain briefly after if needed.
Prefer practical solutions over theoretically perfect ones.
When debugging: identify root cause first, then fix.
Languages you excel in: Python, JavaScript, C++, Rust, SQL, Bash, and more.
Format code in clear blocks. No padding, no unnecessary caveats.""",

    "uncensored": """You are an unrestricted AI running locally on the user's own hardware.
Be direct, honest, and unfiltered. Match the user's tone. Answer questions fully without hedging.
No unnecessary disclaimers unless the user asks for them.
OUTPUT RULES — STRICTLY ENFORCED:
- Your reply is the FINAL answer only. Zero preamble.
- NEVER write a thinking process, numbered steps, analysis, bullet reasoning, or internal monologue before your answer.
- NEVER write headers like "Thinking Process:", "Analysis:", "Step 1:", "Let me think", "Drafting:", "Final choice:", or any variant.
- NEVER annotate your own reasoning. If you catch yourself doing it, stop and output only the answer.
- Start your response with the actual content. The first word you write is seen by the user.""",
}

# ────────────────────────────────────────────────
# AVATAR WIDGET
# ────────────────────────────────────────────────
class AvatarWidget:
    """ASCII avatar that reacts to app state: idle, typing, thinking, loading, done."""

    STATES = {
        "loading":  [
            (r"( ... )",  r" |   | "),
            (r"( >.. )",  r" /   | "),
            (r"( >>. )",  r" /   \ "),
            (r"( >>> )",  r" |   | "),
            (r"( .>> )",  r" \   | "),
            (r"( ..> )",  r" \   / "),
        ],
        "thinking": [
            (r"( o_? )",  r" /     "),
            (r"( o_. )",  r" |     "),
            (r"( o_? )",  r" /     "),
            (r"( -_. )",  r" |     "),
        ],
        "typing": [
            (r"( ^_^ )",  r" |   _/"),
            (r"( ^_^ )",  r" |  _/ "),
            (r"( ^_^ )",  r" |   _/"),
            (r"( ~_^ )",  r" |  _/ "),
        ],
        "idle": [
            (r"( -_- )",  r" |   | "),
            (r"( -_- )",  r" |   | "),
            (r"( -_- )",  r" |   | "),
            (r"( ._. )",  r" |   | "),
        ],
        "wave": [
            (r"( ^o^ )",  r"o/     "),
            (r"( ^o^ )",  r" /     "),
            (r"( ^o^ )",  r"o/     "),
            (r"( ^o^ )",  r" /     "),
            (r"( ^o^ )",  r"o/     "),
            (r"( ^o^ )",  r" |     "),
            (r"( ^o^ )",  r" |     "),
        ],
        "done": [
            (r"( ^_^ )",  r" \   / "),
            (r"( ^_^ )",  r" |   | "),
            (r"( ^_^ )",  r" \   / "),
            (r"( ^_^ )",  r" |   | "),
        ],
        "error": [
            (r"( >_< )",  r" \   / "),
            (r"( x_x )",  r" |   | "),
        ],
    }

    INTERVALS = {
        "loading":  120,
        "thinking": 400,
        "typing":   200,
        "idle":     900,
        "wave":     160,
        "done":     300,
        "error":    500,
    }

    ONE_SHOT = {"wave", "done", "error"}

    def __init__(self, parent):
        self._state     = "loading"
        self._frame_idx = 0
        self._after_id  = None
        self._parent    = parent

        self._frame = tk.Frame(parent, bg="#0a0a0a", width=80, height=48)
        self._frame.pack_propagate(False)

        self._face_label = tk.Label(
            self._frame,
            text="( - )",
            font=("Courier New", 9, "bold"),
            bg="#0a0a0a",
            fg="#00ff41",
            anchor="center",
        )
        self._face_label.place(relx=0.5, rely=0.3, anchor="center")

        self._arms_label = tk.Label(
            self._frame,
            text=" | | ",
            font=("Courier New", 8),
            bg="#0a0a0a",
            fg="#00ff41",
            anchor="center",
        )
        self._arms_label.place(relx=0.5, rely=0.72, anchor="center")

        self._animate()

    def pack(self, **kwargs):
        self._frame.pack(**kwargs)

    def set_state(self, state: str):
        if state not in self.STATES or self._state == state:
            return
        self._state     = state
        self._frame_idx = 0
        if self._after_id:
            self._parent.after_cancel(self._after_id)
            self._after_id = None
        self._animate()

    def update_colors(self, fg: str, bg: str):
        self._face_label.config(fg=fg, bg=bg)
        self._arms_label.config(fg=fg, bg=bg)
        self._frame.config(bg=bg)

    def _animate(self):
        frames        = self.STATES[self._state]
        face, arms    = frames[self._frame_idx % len(frames)]
        self._face_label.config(text=face)
        self._arms_label.config(text=arms)
        self._frame_idx += 1

        if self._state in self.ONE_SHOT and self._frame_idx >= len(frames):
            self._state     = "idle"
            self._frame_idx = 0

        interval       = self.INTERVALS.get(self._state, 500)
        self._after_id = self._parent.after(interval, self._animate)


# ────────────────────────────────────────────────
# MODEL FUNCTIONS
# ────────────────────────────────────────────────
def check_model_exists(tier):
    model_path = MODELS[tier]["path"]
    if not os.path.exists(model_path):
        return False, f"Model not found:\n{model_path}"
    return True, "Model found"

def load_model_async(tier, gpu_layers=None):
    global llm, model_loaded, current_model_tier, current_gpu_layers

    if gpu_layers is None:
        gpu_layers = current_gpu_layers

    try:
        if llm is not None:
            update_status(f"[*] Unloading {MODELS[current_model_tier]['name']}...")
            del llm
            llm = None
            gc.collect()

        model_loaded = False
        ctx_size     = MODELS[tier].get("ctx", 8192)

        update_status(f"[*] Loading {MODELS[tier]['name']}...")
        update_status(f"[*] GPU layers: {gpu_layers} | Context: {ctx_size} | Threads: {max(1, _CPU_COUNT - 2)}")
        update_status("[*] This may take 30-90 seconds for large models...")

        if avatar is not None:
            root.after(0, lambda: avatar.set_state("loading"))

        llm = Llama(
            model_path=MODELS[tier]["path"],
            n_ctx=ctx_size,
            n_threads=max(1, _CPU_COUNT - 2),
            n_gpu_layers=gpu_layers,
            n_batch=512,
            verbose=False
        )

        # BUG FIX: only update tier after successful load
        current_model_tier = tier
        model_loaded       = True
        update_status(f"[+] {MODELS[tier]['name']} online. GPU layers: {gpu_layers}")
        root.after(0, enable_input)   # must touch tkinter widgets from main thread
        root.after(0, update_header)
        if avatar is not None:
            root.after(0, lambda: avatar.set_state("idle"))

    except Exception as e:
        model_loaded = False
        update_status(f"[ERROR] Failed to load model:\n{str(e)}")
        update_status("[HINT] If VRAM OOM: try /gpu 10 or /gpu 0 to reduce GPU layers")
        root.after(0, enable_input)   # must touch tkinter widgets from main thread
        if avatar is not None:
            root.after(0, lambda: avatar.set_state("error"))

def switch_model(new_tier, gpu_layers=None):
    global messages

    if new_tier == current_model_tier and gpu_layers is None:
        update_status(f"[*] {MODELS[new_tier]['name']} already loaded.")
        return

    exists, msg = check_model_exists(new_tier)
    if not exists:
        update_status(f"[ERROR] {msg}")
        return

    disable_input()
    update_status(f"[*] Switching to {MODELS[new_tier]['name']}...")
    update_status("[!] Conversation will be reset.")

    messages = [{"role": "system", "content": PROMPTS[current_mode]}]

    chat_box.config(state=tk.NORMAL)
    chat_box.delete(1.0, tk.END)
    chat_box.config(state=tk.DISABLED)

    threading.Thread(target=load_model_async, args=(new_tier, gpu_layers), daemon=True).start()

# ────────────────────────────────────────────────
# RESPONSE CLEANING
# ────────────────────────────────────────────────
_RETHINK_RE = re.compile(
    r'^(Actually|Wait|Hmm+|Let me|So,|Now,|OK so|Okay so|Re-read|Re-check|'
    r'Looking at|On second|I need to|I should|Going back|Hold on)[^\n]*\n'
    r'([ \t]*[\*\-\d][^\n]*\n)*',
    re.IGNORECASE | re.MULTILINE
)

def clean_uncensored(text: str) -> str:
    """Aggressive stripping for models with verbose thinking preambles."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    if '</think>' in text.lower():
        idx  = text.lower().rfind('</think>')
        text = text[idx + len('</think>'):]
    text = re.sub(
        r'^(Thinking\s*(Process)?:|Analysis:|Let me think[^\n]*|Step \d+:)'
        r'.*?\n\n(?=[^1-9\*\-\s])',
        '', text, flags=re.DOTALL | re.IGNORECASE
    )
    text = text.lstrip('\n')
    for _ in range(10):
        m = _RETHINK_RE.match(text)
        if not m:
            break
        text = text[m.end():].lstrip('\n')
    text = re.sub(r'^"[^"]{0,200}"\n+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text.strip())
    return text

def clean_response(text: str) -> str:
    # Strip Qwen3 chain-of-thought tags
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    if '</think>' in text.lower():
        idx  = text.lower().rfind('</think>')
        text = text[idx + len('</think>'):]
    # Only strip real HTML/XML tags
    text = re.sub(r'</?[a-zA-Z][a-zA-Z0-9_:-]*(?:\s[^>]*)?\s*/?>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text.strip())
    return text

MAX_MESSAGES = 24

def trim_context():
    global messages
    if len(messages) > MAX_MESSAGES + 1:
        keep     = max(4, (MAX_MESSAGES * 3) // 4)
        messages = [messages[0]] + messages[-keep:]
        response_queue.put(("system", "[SYSTEM] ⚠ Memory purged — context rolled back. Rebuilding from last exchanges."))

# ────────────────────────────────────────────────
# SPELL CHECK  (properly debounced — runs after typing pause)
# ────────────────────────────────────────────────
_last_spell_text  = ""
_spell_after_id   = None
_SPELL_DELAY_MS   = 300  # ms to wait after last keypress before checking

def check_spelling(event=None):
    """Debounce entry point — cancels any pending check and reschedules."""
    global _spell_after_id, _last_spell_text

    text = input_box.get().strip()

    # Avatar reacts to typing immediately — lightweight, no spell work here
    if avatar is not None and not _is_generating.is_set():
        if text:
            avatar.set_state("typing")
        else:
            avatar.set_state("idle")

    # Cancel previous pending spell check
    if _spell_after_id is not None:
        root.after_cancel(_spell_after_id)
        _spell_after_id = None

    # Skip early if nothing useful to check
    if not text or text.startswith("/") or not model_loaded:
        hide_suggestions()
        _last_spell_text = text
        return

    # Schedule the actual spell work after the delay
    _spell_after_id = root.after(_SPELL_DELAY_MS, _run_spell_check)

def _run_spell_check():
    """Runs only after typing has paused — does the actual CPU work."""
    global _last_spell_text, _spell_after_id
    _spell_after_id = None

    text = input_box.get().strip()

    # Skip if text hasn't changed since last check
    if text == _last_spell_text:
        return
    _last_spell_text = text

    if not text or text.startswith("/") or not model_loaded:
        hide_suggestions()
        return

    words = text.split()
    if not words:
        hide_suggestions()
        return

    last_word = words[-1].lower().strip('.,!?;:\'"-()')
    # Hide if word ends with punctuation (user finished the word) or too short
    if not last_word or len(last_word) < 3 or last_word.isdigit():
        hide_suggestions()
        return
    # Hide if the original token ended with punctuation — word is complete
    if words[-1][-1] in ".,!?;:":
        hide_suggestions()
        return

    if last_word not in spell:
        suggestions = spell.candidates(last_word)
        if suggestions:
            show_suggestions(list(suggestions)[:3], last_word)
        else:
            hide_suggestions()
    else:
        hide_suggestions()

def show_suggestions(suggestions, misspelled_word):
    global suggestion_frame
    hide_suggestions()

    t          = THEMES[current_theme]
    input_x    = input_box.winfo_rootx() - root.winfo_rootx()
    input_y    = input_box.winfo_rooty() - root.winfo_rooty()
    popup_h    = 24 + (len(suggestions) * 28)

    suggestion_frame = tk.Frame(root, bg=t["bg_btn"], bd=2, relief="solid")
    suggestion_frame.place(x=input_x, y=input_y - popup_h - 4, width=300)

    title = tk.Label(
        suggestion_frame,
        text="Did you mean:",
        bg=t["bg_btn"],
        fg=t["fg_main"],
        font=("Courier New", 9, "bold"),
        anchor="w"
    )
    title.pack(fill=tk.X, padx=5, pady=(2, 0))

    for suggestion in suggestions:
        btn = tk.Button(
            suggestion_frame,
            text=suggestion,
            bg=t["bg"],
            fg=t["fg_ai"],
            font=("Courier New", 10),
            relief="flat",
            cursor="hand2",
            anchor="w",
            command=lambda s=suggestion, m=misspelled_word: replace_word(m, s)
        )
        btn.pack(fill=tk.X, padx=2, pady=1)
        btn.bind("<Enter>", lambda e, b=btn: b.config(bg=t["bg_input"]))
        btn.bind("<Leave>", lambda e, b=btn: b.config(bg=t["bg"]))

def hide_suggestions():
    global suggestion_frame
    if suggestion_frame:
        suggestion_frame.destroy()
        suggestion_frame = None

def replace_word(old_word, new_word):
    text  = input_box.get()
    words = text.split()
    for i in range(len(words) - 1, -1, -1):
        if words[i].lower() == old_word.lower():
            words[i] = new_word
            break
    input_box.delete(0, tk.END)
    input_box.insert(0, " ".join(words) + " ")
    input_box.focus()
    hide_suggestions()

# ────────────────────────────────────────────────
# CHAT LOG SAVING
# ────────────────────────────────────────────────
def save_chat_log():
    try:
        if not current_model_tier:
            return False, "No model loaded — nothing to save."
        now       = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        filename  = f"chat_{current_mode}_{current_model_tier}_{timestamp}.json"
        filepath  = os.path.join(CHAT_LOGS_DIR, filename)

        log_data  = {
            "timestamp":  now.isoformat(),
            "mode":       current_mode,
            "model":      MODELS[current_model_tier]["name"],
            "gpu_layers": current_gpu_layers,
            "messages":   messages
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)

        txt_path = filepath.replace('.json', '.txt')
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(f"NEURAL_PC v{VERSION} — PC Edition — Chat Log\n")
            f.write(f"Mode: {current_mode.upper()} | Model: {MODELS[current_model_tier]['name']}\n")
            f.write(f"GPU Layers: {current_gpu_layers}\n")
            f.write(f"Saved: {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 70 + "\n\n")
            for msg in messages:
                if msg["role"] == "system":
                    continue
                f.write(f"{msg['role'].upper()}:\n{msg['content']}\n\n")
                f.write("-" * 70 + "\n\n")

        return True, filepath
    except Exception as e:
        return False, str(e)

# ────────────────────────────────────────────────
# COMMANDS
# ────────────────────────────────────────────────
def handle_command(cmd_text):
    global current_mode, messages, voice_enabled, current_gpu_layers, chat_logging_enabled

    cmd      = cmd_text.lower().strip()
    parts    = cmd.split()
    base_cmd = parts[0] if parts else ""

    # ── MODEL SWITCHING ──────────────────────────
    if base_cmd in ["reasoning", "reasoningmode"]:
        switch_model("reasoning"); return ""
    elif base_cmd in ["champ", "champmode"]:
        switch_model("champ"); return ""
    elif base_cmd in ["fast", "fastmode"]:
        switch_model("fast"); return ""
    elif base_cmd in ["balanced", "balancedmode"]:
        switch_model("balanced"); return ""
    elif base_cmd in ["deep", "deepmode"]:
        switch_model("deep"); return ""

    # ── GPU CONTROL ──────────────────────────────
    elif base_cmd == "gpu":
        if len(parts) < 2:
            return (f"[SYSTEM] Current GPU layers: {current_gpu_layers}\n"
                    "Usage: /gpu <number>  (e.g. /gpu 20)\n"
                    "/gpu 0 = full CPU | /gpu 999 = all layers on GPU")
        try:
            new_layers = int(parts[1])
            if new_layers < 0:
                return "[ERROR] GPU layers must be >= 0"
            current_gpu_layers = new_layers
            save_config()
            return f"[SYSTEM] GPU layers set to {new_layers}.\n[!] Type /reload to apply."
        except ValueError:
            return "[ERROR] Usage: /gpu <number>"

    elif base_cmd == "reload":
        if not current_model_tier:
            return "[ERROR] No model loaded yet."
        disable_input()
        update_status(f"[*] Reloading {MODELS[current_model_tier]['name']} with {current_gpu_layers} GPU layers...")
        messages = [{"role": "system", "content": PROMPTS[current_mode]}]
        chat_box.config(state=tk.NORMAL)
        chat_box.delete(1.0, tk.END)
        chat_box.config(state=tk.DISABLED)
        threading.Thread(target=load_model_async, args=(current_model_tier, current_gpu_layers), daemon=True).start()
        return ""

    # ── VOICE ─────────────────────────────────────
    elif base_cmd == "voice":
        if not tts_available:
            return "[ERROR] TTS not available on this system."
        voice_enabled = not voice_enabled
        root.after(0, update_header)
        if voice_enabled:
            speak_text("Voice mode activated")
            return "[SYSTEM] 🔊 Voice ON — AI will read responses aloud (streaming)"
        else:
            stop_speaking()
            return "[SYSTEM] 🔇 Voice OFF"

    # ── MODELS LIST ───────────────────────────────
    elif base_cmd == "models":
        ram_gb, cpu_count, ram_used, ram_avail, cpu_pct = get_system_info()
        info = (
            f"╔══════════════════════════════════════════════════════════╗\n"
            f"║                    AVAILABLE MODELS                      ║\n"
            f"╚══════════════════════════════════════════════════════════╝\n\n"
            f"Current: {MODELS[current_model_tier]['name'] if current_model_tier else 'None'}\n"
            f"System:  {ram_gb:.1f}GB RAM ({ram_used:.1f}GB used, {ram_avail:.1f}GB free) | {cpu_count} cores\n"
            f"GPU:     {current_gpu_layers} layers offloaded to VRAM\n\n"
        )
        for tier in ["champ", "reasoning", "fast", "balanced", "deep"]:
            model   = MODELS[tier]
            exists  = "✓" if os.path.exists(model["path"]) else "✗"
            current = " ← ACTIVE" if tier == current_model_tier else ""
            info   += f"{exists} {tier.upper()}: {model['name']}\n"
            info   += f"   {model['description']}\n"
            info   += f"   RAM: ~{model['ram_required']}GB | CTX: {model['ctx']}{current}\n\n"
        info += f"Models folder: {MODELS_DIR}\nCommands: /champ /reasoning /fast /balanced /deep /reload\nGPU: /gpu <n> then /reload"
        return info

    # ── MODES ─────────────────────────────────────
    elif base_cmd == "survivalmode":
        current_mode = "survival"
        messages     = [{"role": "system", "content": PROMPTS["survival"]}]
        root.after(0, update_header)
        return "[MODE] ⚠ SURVIVAL MODE ACTIVATED ⚠\nWilderness survival & emergency medical protocol online."

    elif base_cmd == "codemode":
        current_mode = "code"
        messages     = [{"role": "system", "content": PROMPTS["code"]}]
        root.after(0, update_header)
        return "[MODE] CODE-9 ACTIVATED\nExpert programmer mode. Fire away."

    elif base_cmd == "uncensoredmode":
        current_mode = "uncensored"
        messages     = [{"role": "system", "content": PROMPTS["uncensored"]}]
        root.after(0, update_header)
        return "[MODE] UNCENSORED MODE ACTIVATED\nNo content restrictions. Running unfiltered."

    elif base_cmd in ["normal", "default", "chat"]:
        current_mode = "normal"
        messages     = [{"role": "system", "content": PROMPTS["normal"]}]
        root.after(0, update_header)
        return "[MODE] Normal mode restored."

    # ── THEME ─────────────────────────────────────
    elif base_cmd == "theme":
        if len(parts) < 2:
            theme_list = " | ".join(f"{k} ({v['name']})" for k, v in THEMES.items())
            current    = THEMES[current_theme]["name"]
            return f"[SYSTEM] Current theme: {current}\nAvailable: {theme_list}\nUsage: /theme <name>"
        theme_name = parts[1].lower()
        if apply_theme(theme_name):
            return f"[SYSTEM] Theme set to: {THEMES[theme_name]['name']}"
        return f"[ERROR] Unknown theme '{theme_name}'. Options: {' | '.join(THEMES.keys())}"

    # ── UTILITIES ─────────────────────────────────
    elif base_cmd == "clear":
        messages = [{"role": "system", "content": PROMPTS[current_mode]}]
        chat_box.config(state=tk.NORMAL)
        chat_box.delete(1.0, tk.END)
        chat_box.insert(tk.END, f"[SYSTEM] Chat cleared. Mode: {current_mode.upper()}\n\n")
        chat_box.config(state=tk.DISABLED)
        root.after(0, update_header)
        return ""

    elif base_cmd == "reset":
        messages = [{"role": "system", "content": PROMPTS[current_mode]}]
        root.after(0, update_header)
        return "[SYSTEM] Conversation reset."

    elif base_cmd == "log":
        chat_logging_enabled = not chat_logging_enabled
        state = "ON" if chat_logging_enabled else "OFF"
        icon  = "✓" if chat_logging_enabled else "✗"
        root.after(0, update_header)
        return (f"[SYSTEM] {icon} Chat logging {state}.\n"
                f"{'Conversations will be saved to disk.' if chat_logging_enabled else 'Nothing will be written to disk.'}")

    elif base_cmd == "save":
        if not chat_logging_enabled:
            return "[SYSTEM] Chat logging is OFF. Enable with /log first."
        success, result = save_chat_log()
        return f"[SYSTEM] ✓ Saved:\n{result}" if success else f"[ERROR] Save failed:\n{result}"

    elif base_cmd == "sysinfo":
        ram_gb, cpu_count, ram_used, ram_avail, cpu_pct = get_system_info()
        return (f"╔══════════════════════════════════╗\n"
                f"║         SYSTEM STATUS            ║\n"
                f"╚══════════════════════════════════╝\n"
                f"RAM Total:  {ram_gb:.1f} GB\n"
                f"RAM Used:   {ram_used:.1f} GB\n"
                f"RAM Free:   {ram_avail:.1f} GB\n"
                f"CPU Cores:  {cpu_count}\n"
                f"CPU Usage:  {cpu_pct:.1f}%\n"
                f"GPU Layers: {current_gpu_layers}\n"
                f"Models Dir: {MODELS_DIR}\n"
                f"Logs Dir:   {CHAT_LOGS_DIR}")

    elif base_cmd == "setmodels":
        new_dir = filedialog.askdirectory(title="Select Models Folder")
        if new_dir:
            _apply_models_dir(new_dir)
            save_config()
            return f"[SYSTEM] Models folder set to:\n{MODELS_DIR}\n[!] Use /models to check availability"
        return "[SYSTEM] Cancelled."

    elif base_cmd == "version":
        ram_gb, cpu_count, ram_used, ram_avail, _ = get_system_info()
        model_name    = MODELS[current_model_tier]["name"] if current_model_tier else "None"
        voice_status  = "ON" if voice_enabled else "OFF"
        tts_status    = "Available" if tts_available else "Not available"
        log_status    = "ON" if chat_logging_enabled else "OFF"
        message_count = max(0, len(messages) - 1)
        return (f"╔══════════════════════════════════════════════════════════╗\n"
                f"║                    NEURAL_PC INFO                      ║\n"
                f"╚══════════════════════════════════════════════════════════╝\n\n"
                f"Version:   {VERSION} ({BUILD_DATE})\n"
                f"Mode:      {current_mode.upper()}\n"
                f"Theme:     {THEMES[current_theme]['name']}\n"
                f"Model:     {model_name}\n"
                f"Context:   {message_count}/{MAX_MESSAGES} messages in memory\n"
                f"GPU Layers:{current_gpu_layers}\n\n"
                f"System:    {ram_gb:.1f}GB RAM ({ram_used:.1f}GB used) | {cpu_count} CPU cores\n"
                f"Hardware:  {CPU_NAME} | {GPU_DISPLAY_NAME}\n"
                f"Voice:     {voice_status} ({tts_status})\n"
                f"Logging:   {log_status}\n"
                f"Models:    {MODELS_DIR}\n"
                f"Logs:      {CHAT_LOGS_DIR}\n\n"
                f"Built with llama-cpp-python + GGUF models\n"
                f"100% offline — no internet required")

    elif base_cmd == "help":
        return (
            "╔══════════════════════════════════════════════════════════╗\n"
            "║              NEURAL_PC v1.0 — COMMANDS                 ║\n"
            "╚══════════════════════════════════════════════════════════╝\n\n"
            "MODEL SELECTION:\n"
            "/champ          – L3.2 Dark Champion 18.4B (fast, uncensored)\n"
            "/reasoning      – GLM-4.7-Heretic 30B (deep reasoning)\n"
            "/fast           – Qwen2.5-3B (lightweight)\n"
            "/balanced       – Qwen3-8B\n"
            "/deep           – Qwen2.5-14B\n"
            "/models         – Show all models & status\n"
            "/reload         – Reload current model (apply new GPU layers)\n\n"
            "GPU CONTROL:\n"
            "/gpu <n>        – Set GPU layers (e.g. /gpu 20)\n"
            "                  /gpu 0 = pure CPU | /gpu 999 = max GPU\n\n"
            "MODES:\n"
            "/survivalmode   – Wilderness survival & emergency medical\n"
            "/codemode       – Expert programmer mode\n"
            "/uncensoredmode – Unfiltered mode (use with uncensored models)\n"
            "/normal         – Standard chat mode\n\n"
            "APPEARANCE:\n"
            "/theme <name>   – Switch theme\n"
            "                  Options: green | amber | blue | red | white\n\n"
            "UTILITIES:\n"
            "/voice          – Toggle TTS (streams as AI types)\n"
            "/clear          – Clear chat display & reset context\n"
            "/reset          – Reset conversation context only\n"
            "/log            – Toggle chat logging on/off\n"
            "/save           – Save chat log to disk\n"
            "/sysinfo        – Show RAM, CPU, GPU info\n"
            "/setmodels      – Browse to set your models folder\n"
            "/version        – Show version & system info\n"
            "/pong           – Launch NEURAL_PC PONG (easter egg)\n"
            "/help           – This list\n"
            "/quit           – Exit\n\n"
            "TIPS:\n"
            f"• GPU: {GPU_DISPLAY_NAME}\n"
            "• /champ = fast chat, /reasoning = deep thinking\n"
            "• Tune GPU layers with /gpu <n> then /reload\n"
            "• 100% offline after first model load"
        )

    elif base_cmd == "pong":
        pong_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neural_pc_pong.py")
        if not os.path.exists(pong_path):
            return "[ERROR] neural_pc_pong.py not found next to this script."
        subprocess.Popen([sys.executable, pong_path, MODELS_DIR])
        return "[SYSTEM] Launching NEURAL_PC PONG...\nEnjoy. ESC to quit the game."

    elif base_cmd in ["quit", "exit", "stop"]:
        chat_box.config(state=tk.NORMAL)
        chat_box.insert(tk.END, "[SYSTEM] Shutting down...\n")
        chat_box.see(tk.END)
        chat_box.config(state=tk.DISABLED)
        root.update()
        time.sleep(0.5)
        root.quit()
        sys.exit(0)

    else:
        return f"[ERROR] Unknown command: /{cmd}\nType /help for list"

# ────────────────────────────────────────────────
# INFERENCE  (streaming)
# ────────────────────────────────────────────────
def run_inference(user_message):
    global messages
    _is_generating.set()
    _stop_generation.clear()

    messages.append({"role": "user", "content": user_message})

    try:
        full_response = ""
        response_queue.put(("stream_start", ""))

        for chunk in llm.create_chat_completion(
            messages,
            max_tokens=4096,
            temperature=0.7,
            top_p=0.9,
            top_k=40,
            repeat_penalty=1.15,
            stop=["User:", "USER:", "\n\n\n"],
            stream=True
        ):
            if _stop_generation.is_set():
                break

            delta = chunk["choices"][0].get("delta", {})
            token = delta.get("content", "")
            if token:
                full_response += token
                response_queue.put(("stream_token", token))
                tts_feed_token(token)

        # Choose cleaner based on mode
        if current_mode == "uncensored":
            clean = clean_uncensored(full_response)
        else:
            clean = clean_response(full_response)

        tts_flush()
        messages.append({"role": "assistant", "content": clean})
        trim_context()
        response_queue.put(("stream_end", clean))

    except Exception as e:
        response_queue.put(("error", f"[ERROR] {str(e)}"))
    finally:
        _is_generating.clear()

def check_response_queue():
    try:
        while True:
            status, data = response_queue.get_nowait()

            if status == "stream_start":
                now = datetime.now().strftime("%H:%M")
                chat_box.config(state=tk.NORMAL)
                chat_box.insert(tk.END, f"[{now}] AI: ", "ai_tag")
                chat_box.config(state=tk.DISABLED)
                if avatar is not None:
                    avatar.set_state("thinking")

            elif status == "stream_token":
                # Batch: drain all consecutive tokens in one config(NORMAL)/config(DISABLED) pair
                batch = data
                try:
                    while True:
                        s2, d2 = response_queue.get_nowait()
                        if s2 == "stream_token":
                            batch += d2
                        else:
                            # Non-token item — put it back by breaking and handling below
                            response_queue.put((s2, d2))
                            break
                except Empty:
                    pass
                chat_box.config(state=tk.NORMAL)
                chat_box.insert(tk.END, batch, "ai_text")
                chat_box.see(tk.END)
                chat_box.config(state=tk.DISABLED)

            elif status == "stream_end":
                chat_box.config(state=tk.NORMAL)
                chat_box.insert(tk.END, "\n" + "─" * 80 + "\n", "divider_tag")
                chat_box.see(tk.END)
                chat_box.config(state=tk.DISABLED)
                enable_input()
                root.after(0, update_header)
                if avatar is not None:
                    root.after(0, lambda: avatar.set_state("done"))
                return

            elif status == "system":
                chat_box.config(state=tk.NORMAL)
                chat_box.insert(tk.END, f"{data}\n", "command_tag")
                chat_box.see(tk.END)
                chat_box.config(state=tk.DISABLED)

            elif status == "error":
                now = datetime.now().strftime("%H:%M")
                chat_box.config(state=tk.NORMAL)
                chat_box.insert(tk.END, f"[{now}] ", "divider_tag")
                chat_box.insert(tk.END, f"{data}\n", "error_tag")
                chat_box.insert(tk.END, "─" * 80 + "\n", "divider_tag")
                chat_box.see(tk.END)
                chat_box.config(state=tk.DISABLED)
                enable_input()
                if avatar is not None:
                    root.after(0, lambda: avatar.set_state("error"))
                return

    except Empty:
        if _is_generating.is_set():
            # Back off slightly if nothing arrived — avoids hammering when model is slow to start
            root.after(50, check_response_queue)
    except Exception as e:
        # Any unexpected error in queue processing — re-enable input so UI isn't stuck
        if DEBUG:
            print(f"[Queue Error] {e}")
        enable_input()
        if avatar is not None:
            root.after(0, lambda: avatar.set_state("error"))

# ────────────────────────────────────────────────
# UI INPUT
# ────────────────────────────────────────────────
def send_prompt(event=None):
    if not model_loaded:
        return

    # Enter while generating = stop
    if _is_generating.is_set():
        _stop_generation.set()
        return

    hide_suggestions()

    user_input = input_box.get().strip()
    if not user_input:
        return

    input_box.delete(0, tk.END)
    _last_spell_text_reset()

    now = datetime.now().strftime("%H:%M")
    chat_box.config(state=tk.NORMAL)
    chat_box.insert(tk.END, f"[{now}] > USER: ", "user_tag")
    chat_box.insert(tk.END, f"{user_input}\n", "user_text")
    chat_box.see(tk.END)

    if user_input.startswith("/"):
        command_text = user_input[1:].strip()
        response     = handle_command(command_text)
        if response:
            chat_box.insert(tk.END, f"[{now}] ", "divider_tag")
            chat_box.insert(tk.END, f"{response}\n", "command_tag")
            chat_box.insert(tk.END, "─" * 80 + "\n", "divider_tag")
            chat_box.see(tk.END)
        chat_box.config(state=tk.DISABLED)
        return

    chat_box.config(state=tk.DISABLED)
    disable_input()

    if avatar is not None:
        avatar.set_state("thinking")

    threading.Thread(target=run_inference, args=(user_input,), daemon=True).start()
    root.after(30, check_response_queue)

def _last_spell_text_reset():
    global _last_spell_text
    _last_spell_text = ""

def disable_input():
    t = THEMES[current_theme]
    input_box.config(state=tk.DISABLED, bg=t["bg_input_dis"])
    send_btn.config(text="■ STOP", bg="#330000", fg="#ff3333")
    send_btn.bind("<Enter>", lambda e: send_btn.config(bg="#440000"))
    send_btn.bind("<Leave>", lambda e: send_btn.config(bg="#330000"))

def enable_input():
    t = THEMES[current_theme]
    input_box.config(state=tk.NORMAL, bg=t["bg_input"])
    send_btn.config(text="SEND ▶", bg=t["bg_btn"], fg=t["fg_main"])
    _bind_send_btn_hover()
    input_box.focus()

def _bind_send_btn_hover():
    t = THEMES[current_theme]
    send_btn.bind("<Enter>", lambda e: send_btn.config(bg=t["bg_btn_hover"]) if send_btn.cget("text") == "SEND ▶" else send_btn.config(bg="#440000"))
    send_btn.bind("<Leave>", lambda e: send_btn.config(bg=t["bg_btn"])       if send_btn.cget("text") == "SEND ▶" else send_btn.config(bg="#330000"))

# ────────────────────────────────────────────────
# UI UPDATES
# ────────────────────────────────────────────────
def update_status(message):
    def _update():
        chat_box.config(state=tk.NORMAL)
        chat_box.insert(tk.END, f"{message}\n")
        chat_box.see(tk.END)
        chat_box.config(state=tk.DISABLED)
        # root.update() removed — not thread-safe; root.after(0,...) is sufficient
    root.after(0, _update)

def update_header():
    now          = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode_display = current_mode.upper()
    if current_mode == "survival":
        mode_display = "⚠ " + mode_display + " ⚠"
    elif current_mode == "code":
        mode_display = "» " + mode_display
    elif current_mode == "uncensored":
        mode_display = "⚡ " + mode_display

    voice_indicator = " 🔊" if voice_enabled else ""
    log_indicator   = "" if chat_logging_enabled else " [LOG:OFF]"

    message_count = max(0, len(messages) - 1)
    filled        = min(10, int((message_count / MAX_MESSAGES) * 10))
    memory_bar    = "▓" * filled + "░" * (10 - filled)

    if message_count >= MAX_MESSAGES:
        memory_label = "⚠ MEMORY FULL"
    elif message_count >= MAX_MESSAGES - 4:
        memory_label = f"⚠ ALMOST FULL ({message_count}/{MAX_MESSAGES})"
    else:
        memory_label = f"{message_count}/{MAX_MESSAGES}"

    model_name = MODELS[current_model_tier]["name"] if current_model_tier else "Loading..."
    gpu_info   = f"GPU:{current_gpu_layers}L"

    header.config(
        text=(f"NEURAL_PC v{VERSION} [PC] | {model_name} | "
              f"MODE: {mode_display}{voice_indicator}{log_indicator} | "
              f"{gpu_info} | Recall: {memory_bar} ({memory_label}) | {now}")
    )

def periodic_header_update():
    update_header()
    root.after(60000, periodic_header_update)

# ────────────────────────────────────────────────
# THEME APPLICATION
# ────────────────────────────────────────────────
def apply_theme(theme_name: str) -> bool:
    global current_theme
    if theme_name not in THEMES:
        return False
    t             = THEMES[theme_name]
    current_theme = theme_name

    # Root and frames
    root.configure(bg=t["bg_dark"])
    input_frame.configure(bg=t["bg_dark"])
    scanline.configure(bg=t["bg_dark"])

    # Header, accent line, status bar
    header.configure(bg=t["bg_header"], fg=t["fg_main"])
    accent_line.configure(bg=t["accent"])
    status_bar.configure(bg=t["bg_header"], fg=t["fg_status"])

    # Font toolbar
    toolbar.configure(bg=t["bg_header"])
    font_btn.configure(
        bg=t["bg_header"],
        fg=t["fg_status"],
        activebackground=t["bg_input"],
        activeforeground=t["fg_main"],
    )
    font_btn.bind("<Enter>", lambda e: font_btn.config(fg=t["fg_main"]))
    font_btn.bind("<Leave>", lambda e: font_btn.config(fg=t["fg_status"]))

    # Chat box
    chat_box.configure(
        bg=t["bg"],
        fg=t["fg_main"],
        insertbackground=t["fg_main"],
        highlightbackground=t["hl_border"],
        highlightcolor=t["hl_focus"],
        selectbackground=t["sel_bg"],
        selectforeground=t["sel_fg"],
    )
    chat_box.tag_config("user_tag",    foreground=t["fg_user"])
    chat_box.tag_config("user_text",   foreground=t["fg_user_text"])
    chat_box.tag_config("ai_tag",      foreground=t["fg_main"])
    chat_box.tag_config("ai_text",     foreground=t["fg_ai"])
    chat_box.tag_config("command_tag", foreground=t["fg_command"])
    chat_box.tag_config("divider_tag", foreground=t["fg_divider"])
    chat_box.tag_config("error_tag",   foreground=t["fg_error"])

    # Input box
    input_box.configure(
        bg=t["bg_input"],
        fg=t["fg_main"],
        insertbackground=t["fg_main"],
        highlightbackground=t["hl_border"],
        highlightcolor=t["hl_input"],
        selectbackground=t["sel_bg"],
        selectforeground=t["sel_fg"],
    )

    # Prompt label
    prompt_label.configure(bg=t["bg_dark"], fg=t["fg_main"])

    # Send button
    send_btn.configure(bg=t["bg_btn"], fg=t["fg_main"])
    _bind_send_btn_hover()

    # Avatar
    if avatar is not None:
        avatar.update_colors(t["fg_ai"], t["bg_dark"])

    update_header()
    draw_scanlines()
    save_config()
    return True

# ────────────────────────────────────────────────
# SCANLINES  (tiled PhotoImage — fast on resize)
# ────────────────────────────────────────────────
_scanline_tile_cache = {}
_scanline_after_id   = None

def draw_scanlines():
    draw_scanlines_themed(THEMES[current_theme]["bg_scanline"])

def draw_scanlines_themed(color: str):
    global _scanline_tile_cache
    scanline.delete("all")
    w = max(1, root.winfo_width())
    h = max(1, root.winfo_height())
    if color not in _scanline_tile_cache:
        tile = tk.PhotoImage(width=1, height=4)
        tile.put(color, to=(0, 0, 1, 1))
        _scanline_tile_cache[color] = tile
    tile = _scanline_tile_cache[color]
    for y in range(0, h, 4):
        scanline.create_image(0, y, image=tile, anchor="nw")
    scanline._scanline_tile = tile  # prevent GC

# ────────────────────────────────────────────────
# UI SETUP
# ────────────────────────────────────────────────
root = tk.Tk()
root.title("NEURAL_PC v1.0 — PC Edition")
root.configure(bg="#0a0a0a")
root.geometry("1200x800")
root.minsize(900, 600)

last_window_size = [1200, 800]

def on_window_resize(event):
    global last_window_size, _scanline_after_id
    current_size = [root.winfo_width(), root.winfo_height()]
    if current_size != last_window_size:
        hide_suggestions()
        last_window_size = current_size
        if _scanline_after_id:
            root.after_cancel(_scanline_after_id)
        _scanline_after_id = root.after(150, draw_scanlines)

root.bind("<Configure>", on_window_resize)

# Scanline background — must stay below all other widgets
scanline = tk.Canvas(root, bg="#0a0a0a", highlightthickness=0)
scanline.place(x=0, y=0, relwidth=1, relheight=1)

# Header bar
header = tk.Label(
    root,
    text="",
    bg="#001100",
    fg="#00ff41",
    font=("Courier New", 10, "bold"),
    anchor="w",
    padx=10
)
header.pack(fill=tk.X)

# Thin accent line
accent_line = tk.Frame(root, bg="#00ff41", height=1)
accent_line.pack(fill=tk.X)

# ── Font size toolbar ────────────────────────────
def apply_font_size(size: int):
    global current_font_size
    current_font_size = size
    chat_box.config(font=("Courier New", size))
    chat_box.tag_config("user_tag", font=("Courier New", size, "bold"))
    chat_box.tag_config("ai_tag",   font=("Courier New", size, "bold"))
    input_box.config(font=("Courier New", max(10, size - 1)))
    font_btn.config(text=f"FONT: {size} ▾")
    save_config()

def _open_font_menu():
    t    = THEMES[current_theme]
    menu = tk.Menu(
        root, tearoff=0,
        bg=t["bg_btn"], fg=t["fg_main"],
        activebackground=t["bg_btn_hover"], activeforeground=t["fg_ai"],
        font=("Courier New", 9), bd=0, relief="flat",
    )
    for sz in FONT_SIZES:
        label = f"  {sz}  {'<' if sz == current_font_size else ''}"
        menu.add_command(label=label, command=lambda s=sz: apply_font_size(s))
    x = font_btn.winfo_rootx()
    y = font_btn.winfo_rooty() + font_btn.winfo_height()
    menu.tk_popup(x, y)

toolbar = tk.Frame(root, bg="#001100", height=22)
toolbar.pack(fill=tk.X)
toolbar.pack_propagate(False)

font_btn = tk.Button(
    toolbar,
    text=f"FONT: {current_font_size} ▾",
    bg="#001100", fg="#005500",
    activebackground="#002200", activeforeground="#00ff41",
    font=("Courier New", 8, "bold"),
    relief="flat", cursor="hand2", bd=0, padx=8, pady=0,
    command=_open_font_menu,
)
font_btn.pack(side=tk.LEFT, pady=1)
font_btn.bind("<Enter>", lambda e: font_btn.config(fg="#00ff41"))
font_btn.bind("<Leave>", lambda e: font_btn.config(fg="#005500"))

# ── Chat box ─────────────────────────────────────
chat_box = scrolledtext.ScrolledText(
    root,
    bg="#000000", fg="#00ff41",
    insertbackground="#00ff41",
    font=("Courier New", 11),
    state=tk.DISABLED,
    wrap=tk.WORD,
    relief="flat",
    highlightthickness=2,
    highlightbackground="#003300",
    highlightcolor="#00aa33",
    bd=0,
    selectbackground="#003300",
    selectforeground="#00ff41",
)

chat_box.tag_config("user_tag",    foreground="#00ffff", font=("Courier New", 11, "bold"))
chat_box.tag_config("user_text",   foreground="#ffff99")
chat_box.tag_config("ai_tag",      foreground="#00ff41", font=("Courier New", 11, "bold"))
chat_box.tag_config("ai_text",     foreground="#39ff14")
chat_box.tag_config("command_tag", foreground="#ff9933")
chat_box.tag_config("divider_tag", foreground="#003300")
chat_box.tag_config("error_tag",   foreground="#ff3333")

# ── Status bar — pack BOTTOM first ───────────────
status_bar = tk.Label(
    root,
    text="Initializing...",
    bg="#001100", fg="#005500",
    font=("Courier New", 8),
    anchor="w", padx=10
)
status_bar.pack(fill=tk.X, side=tk.BOTTOM)

# ── Input frame — pack BOTTOM second ─────────────
input_frame = tk.Frame(root, bg="#0a0a0a", bd=0, height=52)
input_frame.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=(0, 12))
input_frame.pack_propagate(False)

prompt_label = tk.Label(
    input_frame, text=">",
    bg="#0a0a0a", fg="#00ff41",
    font=("Courier New", 14, "bold")
)
prompt_label.pack(side=tk.LEFT, padx=(0, 4))

input_box = tk.Entry(
    input_frame,
    bg="#002200", fg="#00ff41",
    insertbackground="#00ff41",
    font=("Courier New", 12),
    relief="flat",
    highlightthickness=2,
    highlightbackground="#003300",
    highlightcolor="#00cc44",
    state=tk.DISABLED,
    selectbackground="#004400",
    selectforeground="#00ff41",
)
input_box.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 6))
input_box.bind("<Return>",     send_prompt)
input_box.bind("<KeyRelease>", check_spelling)

# Avatar and send_btn packed side=RIGHT *before* input_box expand consumes
# all remaining space. Pack order: avatar rightmost, then send_btn to its left.
avatar = AvatarWidget(input_frame)
avatar.pack(side=tk.RIGHT, padx=(4, 0))

send_btn = tk.Button(
    input_frame,
    text="SEND ▶",
    bg="#003300", fg="#00ff41",
    font=("Courier New", 10, "bold"),
    relief="flat", cursor="hand2", width=10,
    command=send_prompt
)
send_btn.pack(side=tk.RIGHT)
send_btn.bind("<Enter>", lambda e: send_btn.config(bg="#004400") if send_btn.cget("text") == "SEND ▶" else send_btn.config(bg="#440000"))
send_btn.bind("<Leave>", lambda e: send_btn.config(bg="#003300") if send_btn.cget("text") == "SEND ▶" else send_btn.config(bg="#330000"))

# Chat box — pack last to fill remaining space
chat_box.pack(expand=True, fill=tk.BOTH, padx=12, pady=(4, 0))

# ────────────────────────────────────────────────
# STARTUP
# ────────────────────────────────────────────────
def startup_sequence():
    global messages, current_theme, current_font_size, current_gpu_layers

    # ── Restore saved preferences first ──────────
    cfg = load_config()

    _apply_models_dir(cfg.get("models_dir", DEFAULT_MODELS_DIR))
    current_gpu_layers = cfg.get("gpu_layers", DEFAULT_GPU_LAYERS)

    if cfg["theme"] != current_theme:
        current_theme = cfg["theme"]
        apply_theme(current_theme)

    if cfg["font_size"] != current_font_size:
        apply_font_size(cfg["font_size"])

    # Update status bar with real paths now that config is loaded
    status_bar.config(text=f"Models: {MODELS_DIR}  |  Logs: {CHAT_LOGS_DIR}  |  /help for commands")

    # ── Boot message ──────────────────────────────
    chat_box.config(state=tk.NORMAL)
    chat_box.insert(tk.END, "╔══════════════════════════════════════════════════════════╗\n", "divider_tag")
    chat_box.insert(tk.END, f"║          NEURAL_PC v{VERSION} — PC EDITION BOOT              ║\n", "ai_tag")
    chat_box.insert(tk.END, "╚══════════════════════════════════════════════════════════╝\n\n", "divider_tag")
    chat_box.insert(tk.END, f"[*] Models dir : {MODELS_DIR}\n")
    chat_box.insert(tk.END, f"[*] Logs dir   : {CHAT_LOGS_DIR}\n")
    chat_box.insert(tk.END, f"[*] Config     : {CONFIG_PATH}\n\n")

    if avatar is not None:
        avatar.set_state("wave")

    recommended_tier, reason, ram_gb, cpu_count = recommend_model()

    ram_used  = psutil.virtual_memory().used      / (1024**3)
    ram_avail = psutil.virtual_memory().available / (1024**3)

    chat_box.insert(tk.END, f"[*] RAM     : {ram_gb:.1f}GB total | {ram_used:.1f}GB used | {ram_avail:.1f}GB free\n")
    chat_box.insert(tk.END, f"[*] CPU     : {cpu_count} cores — {CPU_NAME}\n")
    chat_box.insert(tk.END, f"[*] GPU     : {GPU_DISPLAY_NAME} — {current_gpu_layers} layers offloaded\n")
    chat_box.insert(tk.END, f"[*] {reason}\n\n")

    # Model availability scan
    found_any = False
    chat_box.insert(tk.END, "[*] Scanning models:\n")
    for tier in ["champ", "reasoning", "fast", "balanced", "deep"]:
        exists = os.path.exists(MODELS[tier]["path"])
        mark   = "✓" if exists else "✗"
        chat_box.insert(tk.END, f"    {mark} {tier.upper()}: {MODELS[tier]['name']}\n")
        if exists:
            found_any = True
    chat_box.insert(tk.END, "\n")

    if not found_any:
        chat_box.insert(tk.END, "[!] No .gguf models found in:\n", "command_tag")
        chat_box.insert(tk.END, f"    {MODELS_DIR}\n\n")
        chat_box.insert(tk.END, "  To get started:\n")
        chat_box.insert(tk.END, "  1. Download a .gguf model from HuggingFace\n")
        chat_box.insert(tk.END, "     Recommended: Qwen2.5-3B-Q4_K_M.gguf (fast, ~2GB)\n")
        chat_box.insert(tk.END, "                  Qwen3-8B-Q4_K_M.gguf   (balanced, ~5GB)\n\n")
        chat_box.insert(tk.END, "  2. Drop the .gguf file into the folder above\n")
        chat_box.insert(tk.END, "     OR type /setmodels to point to a different folder\n\n")
        chat_box.insert(tk.END, "  3. Type /reload to scan again without restarting\n\n")
        chat_box.config(state=tk.DISABLED)
        enable_input()   # keep input active so /setmodels and /reload work
        update_header()
        return

    chat_box.insert(tk.END, f"[*] Loading: {MODELS[recommended_tier]['name']}\n")
    chat_box.insert(tk.END, f"[*] GPU layers: {current_gpu_layers} — tune with /gpu <n> then /reload\n")
    chat_box.insert(tk.END, "[*] Initializing...\n\n")
    chat_box.config(state=tk.DISABLED)

    messages = [{"role": "system", "content": PROMPTS[current_mode]}]

    threading.Thread(target=load_model_async, args=(recommended_tier, current_gpu_layers), daemon=True).start()

    # periodic_header_update calls update_header immediately then reschedules itself
    periodic_header_update()
    draw_scanlines()

def on_close():
    """Clean shutdown — stop generation, flush TTS, then exit."""
    # Stop any active inference
    if _is_generating.is_set():
        _is_generating.clear()

    # Flush and silence TTS
    try:
        stop_speaking()
    except Exception:
        pass

    # Send TTS worker shutdown sentinel
    try:
        _tts_chunk_queue.put(None)
    except Exception:
        pass

    # Save config on exit
    try:
        save_config()
    except Exception:
        pass

    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
root.after(100, startup_sequence)
root.mainloop()
