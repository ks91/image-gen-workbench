# Image Gen Workbench

A local image generation and editing workbench powered by **Qwen-Image-2.1**, with a Flask web interface. Generate an image, refine your prompt, edit an existing image, and compare results without losing your settings or history.

## Features

- Generate 1–8 variations per submission with different seeds and choose a result to refine.
- Text-to-image generation and instruction-based image editing.
- Upload a source image or continue editing any generated image.
- Reuse prompts, seeds, and generation settings for iterative experiments.
- Compare two images side by side, mark favorites, and save notes.
- Background generation with progress, cancellation, and persistent history.
- Browse history 24 entries per page, including server-side favorites filtering.
- Download PNGs with generation settings embedded as metadata.
- Switch between **English and Japanese** without losing your current inputs.

## Quick start

Install **Python 3.12 or later** (Python 3.12 is recommended on macOS). Clone this repository, enter its directory, and run:

```sh
cd image-gen-workbench
python3.12 setup.py
./run
```

Your default browser opens automatically at **http://127.0.0.1:7860** once the server has bound its port. If the browser cannot be opened, visit that URL manually. Press **Ctrl+C** in the terminal to stop the server.

The setup script creates a project-local `.venv` and installs the application and model dependencies without modifying your system Python. Model weights are downloaded from Hugging Face on the first generation request. Installation and the initial model download require an internet connection. An existing Hugging Face model cache is reused automatically.

You can also activate the environment and use the installed command:

```sh
source .venv/bin/activate
image-gen-workbench
```

The `./run` launcher is intended for macOS and Linux. It uses the project directory as its working directory; the installed command uses your current directory unless you specify `--data-dir`.

### Hardware and precision

Qwen is a large model: allow substantial RAM/VRAM and disk space. The reference environment is an **Apple M2 Ultra with 128 GB of unified memory**. Quantization and CPU offloading are not currently implemented. Other hardware configurations have not been verified here.

Device selection defaults to MPS, then CUDA, then CPU. Automatic precision uses `bfloat16` on GPU and `float32` on CPU. GPU/OS support for `bfloat16` is required; CPU inference may be very slow. You can select the device and precision explicitly:

```sh
./run --no-browser                   # Start without opening a browser
./run --offline                      # Use cached weights only
./run --device mps --port 7861
./run --dtype float32                # Higher memory use
./run --model /path/to/qwen-model    # A QwenImage21Pipeline-compatible model
./run --data-dir /path/to/data
```

The model is loaded lazily and kept in memory for subsequent requests. The first request can take some time before step progress appears.

### Try the interface without a model

```sh
python3.12 setup.py --ui-only
./run --demo
```

Demo mode produces images labeled `DEMO`; it does **not** perform AI generation or editing. Run `python3.12 setup.py` later to install the model dependencies.

### Reuse dependencies from an existing environment

Optionally reference another virtual environment with the same Python major/minor version:

```sh
python3.12 setup.py --reuse-env /path/to/existing/venv
./run --offline
```

The existing environment is not modified. Moving, deleting, or updating it can affect this application. A standalone installation is recommended for portability.

## Language

Use the **Language / 言語** selector in the header to switch between **English** and **日本語**. The initial language follows your browser preference when supported, otherwise it defaults to English. Your selection is stored in a local, first-party cookie for one year and takes precedence on future visits.

Switching languages updates labels, history actions, progress, and application error messages immediately. Your prompt, negative prompt, selected source image, settings, comparison, and unsaved note drafts are preserved. Prompts and notes are never translated. Third-party model/library diagnostic messages and browser-native dialogs may use their own language. Command-line help and setup messages are in English.

## Workflow

1. Enter a prompt, set **Number of images** (1–8), and select **Generate image**.
2. Select **Reuse settings** to retain the seed and settings while experimenting with the prompt. Leave the seed blank to generate a new random seed, which is recorded in the history.
3. Upload a source image or select **Edit this image** on a result. Describe the desired change, such as “change the background to sunset.” You can use the edited result as the source for another edit.
4. Select **Compare** on up to two results to view them side by side. Use favorites and notes to track promising results and ideas.
5. Select **Save PNG** to download an image. The PNG `generation` text metadata also contains the prompt and generation settings.

**Use a different seed when editing a generated image.** Qwen-Image-2.1 can produce an oversaturated, over-sharpened near-copy and ignore edits when the starting noise used to create the reference is replayed (same random generator, seed, and output dimensions). **Edit this image** therefore clears the seed field to request a new random seed. **Reuse settings** retains the recorded seed for repeatable experiments. When uploading a previously generated image or entering a seed manually, avoid its original generation seed. See the [Qwen report](https://github.com/QwenLM/Qwen-Image-2.1/issues/9) and [Diffusers investigation](https://github.com/huggingface/diffusers/issues/14824). Changing seeds addresses this particular failure mode, not every possible editing artifact.

The Qwen-Image-2.1 image input supports instruction-based editing. This pipeline does not expose a conventional img2img `strength` parameter, so the interface does not provide one. Width and height must be multiples of 32 between 256 and 2048. Negative prompts are passed even when empty; CFG is enabled when its value is greater than 1. A fixed seed helps compare experiments, but results can vary across environments.

Each batch uses the same prompt, settings, and optional source image. Seeds start at your specified seed (or a random seed when blank) and increase by one, wrapping after 4294967295. Each result records its own seed and batch label and can be independently compared, downloaded, cancelled, or edited. Reusing settings or choosing an image to edit resets the image count to one; increase it again for more variations. The queue accepts at most 20 active images and rejects an entire batch if it would exceed that limit.

The default settings are **12 inference steps and CFG 1**. CFG 1 disables classifier-free guidance, so negative prompts have no effect at that setting.

For faster previews, try fewer inference steps (for example, compare 12, 20, and 28 with the same seed) or smaller dimensions. Fewer steps generally reduce denoising time but can reduce detail and prompt fidelity; these example counts are not quality guarantees. Loading, prompt processing, and image decoding add fixed overhead. Multiple images are generated sequentially to limit peak memory usage, so requesting more images increases total time rather than accelerating inference.

Jobs run sequentially. Cancellation is checked between generation steps; model loading and prompt processing cannot be stopped immediately. Jobs left unfinished when the server exits are marked as interrupted on the next launch, so you can reuse their settings and try again.

History is displayed newest first, with **24 entries per page** and Previous/Next controls. Polling retrieves only the current page. Favorites are filtered across the full history before pagination. Comparison selections and unsaved note drafts stay available when switching pages. Submitting a new generation returns to page one and clears the favorites filter so the queued results are visible. History remains live: new submissions can shift entries between pages.

`GET /api/jobs?page=1&favorites=0` returns `{items, total, page, pages, page_size}` instead of the previous full-history array. The page size is fixed at 24; `favorites=1` selects favorites only. Pages beyond the end are clamped to the last page. An empty history is page 1 of 1.

## Storage and local operation

- Job history: `data/history.sqlite3`
- Uploaded and generated images: `data/images/`
- Backups: stop the application and copy the entire `data/` directory.

Data is not deleted automatically. Inference runs on your computer; prompts and images are not sent to an external image-generation API. The server binds to loopback only. Only one server process can use a given data directory at a time.

## Development and tests

Run the Python tests without model weights or a GPU:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

These cover generation and persistence, iterative editing, validation, failure recovery, cancellation, language selection, localized errors, and backward-compatible history display. Model substitutes verify application behavior, not image quality or hardware compatibility.

The frontend localization tests use the built-in Node.js test runner (Node.js is only needed for these tests):

```sh
node --test tests/i18n.test.cjs
```

An opt-in real-model smoke test uses MPS and cached weights to generate a 512 × 512 image and then edit it, both at 28 steps. Results are stored in `data/smoke/`:

```sh
.venv/bin/python tests/smoke_model.py
```

This generation/editing flow has been checked on the reference Mac. The original float16 configuration produced a numeric conversion warning in a low-step experiment, so the default GPU precision is bfloat16. You can explicitly choose `--dtype float16`. Numeric conversion failures are reported as failed jobs rather than successful images.

Translations are maintained in `image_gen_workbench/static/messages.json`, shared by the Python backend and browser interface. Keep message keys and interpolation placeholders aligned between languages.

## References

- [Qwen-Image-2.1 model](https://huggingface.co/Qwen/Qwen-Image-2.1)
- [Diffusers Qwen-Image-2.1 API](https://huggingface.co/docs/diffusers/en/api/pipelines/qwenimage21)

The initial implementation was based on a local Qwen script that loaded the model on CPU before moving it to MPS, using 28 inference steps and CFG 3.5.
