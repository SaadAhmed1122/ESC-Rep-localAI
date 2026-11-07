# Models and hardware

ESG Local AI works with **any** chat model served locally. Pick one that fits your RAM.

| RAM / VRAM | Suggested model | Notes |
|---|---|---|
| 8 GB | `llama3.2:3b` | Works. Reports are simpler. |
| 16 GB | `qwen2.5:7b`, `llama3.1:8b` | Recommended default |
| 24–32 GB | `qwen2.5:14b`, `mistral-nemo:12b` | Better strategy and writing |
| 48 GB+ | 27B–32B models | Best quality. A full report takes about 3 minutes on Apple Silicon. |

Embeddings: `nomic-embed-text` (~300 MB) improves document search and narrative scoring.
Without it, an offline hashing embedder is used automatically.

## Ollama (default)

```bash
ollama pull qwen2.5:7b
esglocal models list
ESGLOCAL_CHAT_MODEL=qwen2.5:7b esglocal ui
```

You can also pull models from the **Models & privacy** page. Downloading is done by the
Ollama daemon and needs internet **once**. After that, everything runs offline.

## Air-gapped machines (import a GGUF file)

Download a `.gguf` file on another machine, copy it over (for example on a USB drive), then:

```bash
esglocal models import /path/to/model.gguf --as my-model
ESGLOCAL_CHAT_MODEL=my-model esglocal ui
```

This uploads the file to your **local** Ollama server as a blob and registers it. Nothing
touches the internet.

## LM Studio, llama.cpp, vLLM, LocalAI, Jan

Any OpenAI-compatible server on this machine works:

```bash
# LM Studio (default port 1234)
ESGLOCAL_PROVIDER=openai_compat ESGLOCAL_BASE_URL=http://127.0.0.1:1234 esglocal ui

# llama.cpp
llama-server -m model.gguf --port 8080 --jinja      # --jinja enables tool calling
ESGLOCAL_PROVIDER=openai_compat ESGLOCAL_BASE_URL=http://127.0.0.1:8080 esglocal doctor
```

## Model on another machine in your network

This is refused by default. If you trust the host (for example a GPU box on your LAN, or
the `ollama` service in Docker Compose), allow it explicitly:

```bash
ESGLOCAL_BASE_URL=http://gpu-box:11434 ESGLOCAL_ALLOWED_HOSTS=gpu-box esglocal doctor
```
