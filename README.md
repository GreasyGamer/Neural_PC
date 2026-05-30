# NEURAL_PC v1.0

> A PC port of [NEURAL_GRID](https://github.com/GreasyGamer/NEURAL_GRID) — the offline USB AI assistant, rebuilt for desktop with GPU acceleration.  
> No internet. No cloud. No data collection. Just raw local inference on your own hardware.  
> Runs Qwen out of the box — swap in any GGUF-compatible model your PC can handle.

![Python](https://img.shields.io/badge/Python-3.10+-green?style=flat-square&logo=python)
![Platform](https://img.shields.io/badge/Platform-Windows-blue?style=flat-square&logo=windows)
![Offline](https://img.shields.io/badge/Mode-100%25%20Offline-brightgreen?style=flat-square)
![CUDA](https://img.shields.io/badge/GPU-CUDA%20Accelerated-76b900?style=flat-square&logo=nvidia)
![License](https://img.shields.io/badge/License-MIT-orange?style=flat-square)

---

## What is this?

NEURAL_PC is a desktop port of NEURAL_GRID — a fully offline local LLM terminal with a cyberpunk aesthetic. Where the original runs from a USB drive on any Windows machine using portable Python, NEURAL_PC is built to stay on your desktop and take full advantage of your hardware — specifically GPU acceleration via CUDA.

Same interface, same commands, same philosophy. More power.

---

## What's different from NEURAL_GRID (USB)

| Feature | NEURAL_GRID (USB) | NEURAL_PC |
|---|---|---|
| Runs from USB | Yes | No — installs to disk |
| Portable Python | WinPython bundled | System Python |
| GPU acceleration | No (CPU only) | Yes — CUDA via llama-cpp-python |
| Model tiers | Fast / Balanced / Deep | Fast / Balanced / Deep + Champ / Reasoning |
| Setup | setup.bat on USB | setup.bat installs deps + configures path |
| Config persistence | No | Yes — saves theme, font, GPU layers, models path |
| AMD GPU support | Roadmap | Roadmap |

---

## Features

- **100% offline** — no API keys, no cloud, no telemetry
- **CUDA GPU acceleration** — offload model layers to VRAM for dramatically faster inference
- **Bring your own model** — works with any GGUF-compatible model
- **5 model slots** — Fast, Balanced, Deep, Champ, Reasoning — switch mid-session
- **Streaming responses** — output appears token by token
- **Stop generation** — cancel mid-stream with Enter or the STOP button
- **Modes** — Normal, Survival, Code, Uncensored
- **Voice output** — Windows SAPI TTS, streams as the AI types
- **Spell check** — live suggestions as you type
- **5 themes** — Green, Amber, Blue, Red, White — saved between sessions
- **Config persistence** — remembers your theme, font size, models folder, GPU layers
- **Context memory** — tracks up to 24 messages with a visual memory bar
- **Chat logging** — saves conversations as `.json` and `.txt`
- **ASCII avatar** — reacts to what you're doing
- **GRID Pong** — play pong against your AI companion with live LLM banter (`/pong`)

---

## GPU Acceleration (CUDA)

NEURAL_PC uses `llama-cpp-python` with CUDA support to offload model layers to your GPU VRAM. This is the biggest performance upgrade over the USB version — inference that takes minutes on CPU can take seconds with GPU offload.

### Checking if CUDA is active

Run this in a terminal:

```
python -c "from llama_cpp import llama_cpp; print(llama_cpp.llama_supports_gpu_offload())"
```

If it prints `True` you're good. If `False`, see the install section below.

### Installing llama-cpp-python with CUDA

The default `pip install llama-cpp-python` installs the CPU-only build. To get CUDA:

```
pip uninstall llama-cpp-python -y
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
```

Replace `cu121` with your CUDA version:

| CUDA Version | Index URL suffix |
|---|---|
| 11.8 | `cu118` |
| 12.1 | `cu121` |
| 12.2 | `cu122` |
| 12.4 | `cu124` |

Check your CUDA version with `nvidia-smi`.

### Tuning GPU layers

NEURAL_PC auto-detects your VRAM at startup and sets `gpu_layers` accordingly — no manual tuning needed out of the box. If you hit an out-of-memory error on a large model, reduce the layer count:

```
/gpu 20
/reload
```

`/gpu 0` = full CPU. `/gpu 999` = max GPU. Your setting is saved to `config.json` automatically.

### AMD GPU

AMD support via ROCm is on the roadmap but not available yet. For now, AMD users will run on CPU.

---

## Models

NEURAL_PC uses GGUF models via `llama-cpp-python`. Five slots are pre-configured — edit the `MODELS` dict near the top of `neural_pc.py` to swap in your own.

| Slot | Default Model | VRAM / RAM | Best For |
|---|---|---|---|
| `/fast` | Qwen2.5-3B-Q4_K_M | ~2GB | Quick tasks, low VRAM |
| `/balanced` | Qwen3-8B-Q4_K_M | ~5GB | General use |
| `/deep` | Qwen2.5-14B-Q4_K_M | ~9GB | Heavy reasoning |
| `/champ` | L3.2 Dark Champion 18.4B | ~14GB | Fast uncensored chat |
| `/reasoning` | GLM-4.7-Heretic 30B MoE | ~18GB | Deep thinking |

### Using a different model

NEURAL_PC supports any GGUF-compatible model. Open `neural_pc.py` and find the `MODELS` dictionary near the top:

```python
MODELS = {
    "fast": {
        "name":         "Qwen2.5-3B-Q4_K_M",
        "file":         "Qwen2.5-3B-Q4_K_M.gguf",
        "ram_required": 4,
        "description":  "Fast and lightweight",
        "ctx":          8192
    },
    ...
}
```

Replace the fields for whichever slot you want to swap. Drop the `.gguf` file into your models folder and switch to it with the matching command.

**Tips for picking a model:**
- `Q4_K_M` quantization is the sweet spot — good quality, reasonable size
- Check VRAM requirement before downloading — larger models need more
- 7B–8B models run well on most GPUs with 6GB+ VRAM
- 13B+ models need 10GB+ VRAM for full GPU offload (partial offload still works)

---

## Setup

### Quick Setup (Recommended)

1. Clone or download this repo
2. Double-click `setup.bat`
3. Follow the prompts:
   - Python and dependencies are installed automatically
   - GPU support is checked — if CUDA isn't detected you'll see instructions to fix it
   - **Models folder** — setup checks for `C:\Models` first. If it exists, it's used automatically. If not, it defaults to `%USERPROFILE%\Models`. You can type any custom path at the prompt.
   - GPU layers are auto-detected from your VRAM and written to `config.json`
4. Drop your `.gguf` model files into the folder setup picked
5. Double-click `launch.bat` to start

> `setup.bat` only needs to be run once. After that just use `launch.bat`.  
> You can change the models folder any time inside the app with `/setmodels`.

### Manual Setup

```
pip install -r requirements.txt
```

Launch with `python neural_pc.py`. On first run, hardware is auto-detected — CPU, GPU name, VRAM, and GPU layers are all set automatically. Use `/setmodels` to point the app at your models folder, or edit `config.json` directly.

For GPU support install the CUDA build of llama-cpp-python (see GPU section above).

---

## File Structure

```
NEURAL_PC\
├── neural_pc.py           <- Main application
├── neural_pc_pong.py      <- Pong companion game
├── launch.bat             <- Launch the app
├── setup.bat              <- First-time setup wizard
├── requirements.txt       <- Python dependencies
├── config.json            <- Auto-generated, saves your settings
└── chatlogs\              <- Saved conversations (auto-created)

Your models folder (e.g. C:\Models\):
├── Qwen2.5-3B-Q4_K_M.gguf
├── Qwen3-8B-Q4_K_M.gguf
└── ...any other .gguf files
```

---

## Commands

| Command | Description |
|---|---|
| `/fast` | Switch to fast 3B model |
| `/balanced` | Switch to balanced 8B model |
| `/deep` | Switch to deep 14B model |
| `/champ` | Switch to Champ model |
| `/reasoning` | Switch to Reasoning model |
| `/models` | Show all models and system info |
| `/gpu <n>` | Set GPU layers (e.g. `/gpu 20`) |
| `/reload` | Reload current model (applies new GPU layers) |
| `/survivalmode` | Wilderness and emergency medical expert mode |
| `/codemode` | Expert programmer mode |
| `/uncensoredmode` | Unfiltered mode (use with uncensored models) |
| `/normal` | Return to standard chat mode |
| `/voice` | Toggle TTS — streams as the AI types |
| `/theme <name>` | Switch theme (green / amber / blue / red / white) |
| `/clear` | Clear chat and reset context |
| `/reset` | Reset conversation context only |
| `/log` | Toggle chat logging on/off |
| `/save` | Save chat log to disk |
| `/setmodels` | Browse to change your models folder |
| `/sysinfo` | Show RAM, CPU, GPU info |
| `/version` | Show version and system info |
| `/help` | Show all commands |
| `/pong` | ... |
| `/quit` | Exit |

---

## Memory System

NEURAL_PC tracks conversation context with a visual bar in the header:

```
Recall: ▓▓▓▓▓░░░░░ (12/24)
```

When memory fills up, the oldest messages are purged and the bar rolls back — keeps the most recent exchanges and rebuilds from there.

---

## GRID Pong

Type `/pong` to launch a pong game against your AI companion GRID.

GRID watches the game and generates live banter based on what's happening — every comment is freshly generated from the local model running in a background process so the game never pauses.

**Controls**

| Key | Action |
|---|---|
| W / Up | Move paddle up |
| S / Down | Move paddle down |
| Space | Serve / restart |
| P | Pause |
| Escape | Quit |

If no model is found the game still runs — GRID just stays quiet.

---

## Why offline?

Same reason as NEURAL_GRID. Your hardware, your data, your control. Nothing leaves the machine.

---

## Built With

- [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) — local LLM inference with CUDA support
- [Qwen models](https://huggingface.co/Qwen) — by Alibaba Cloud (GGUF quantized)
- [tkinter](https://docs.python.org/3/library/tkinter.html) — GUI
- [pywin32](https://github.com/mhammond/pywin32) — Windows SAPI TTS
- [NEURAL_GRID](https://github.com/GreasyGamer/NEURAL_GRID) — the original USB version this is ported from

---

## Disclaimer

Built with AI assistance (Claude by Anthropic). Concept, design, and direction are original. Models are third-party — check each model's license on Hugging Face before commercial use.

---

*Boot up. Stay offline.*
