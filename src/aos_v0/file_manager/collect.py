"""
Input Collection and Auto-Detection Manager for AOS v0.

Extension-Based Type Detection: Maps file paths to category types ('audio', 'image', 'text')
using pre-defined extension sets.

Opt-in Directory Scanning: Scans `PROJECT_ROOT/data/inputs` for dropbox-style 
file collection only when explicitly requested via `--use-inputs-folder`.

Argument Parsing & Mutation: Parses and strips `--input`, `--audio`, and 
`--image` flags from `sys.argv` while validating file existence.

Input Precedence: Ensures explicit CLI arguments override dropbox-scanned files.
"""

from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent.parent

_AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".wma", ".aac"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
_TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".pdf"}

def _detect_input_type(path: str) -> str:
    """Auto-detect input type from file extension."""
    ext = Path(path).suffix.lower()
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _TEXT_EXTS:
        return "text"
    return "text"

def _scan_inputs_folder() -> dict[str, str]:
    """Scan the inputs/ folder for files with known extensions."""
    inputs_dir = PROJECT_ROOT / "data" / "inputs"
    if not inputs_dir.is_dir():
        return {}

    found: dict[str, str] = {}
    for f in sorted(inputs_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            input_type = _detect_input_type(str(f))
            if input_type not in found:
                found[input_type] = str(f)
    return found

def _collect_inputs(args: list[str]) -> dict[str, str]:
    """Collect typed inputs from CLI flags and (optionally) the inputs/ folder """
    inputs: dict[str, str] = {}

    # 1. Folder scan (lowest priority) -- only when explicitly requested.
    if "--use-inputs-folder" in args:
        args.remove("--use-inputs-folder")
        inputs.update(_scan_inputs_folder())

    # 2. --input <path> (auto-detect type from extension, repeatable).
    while "--input" in args:
        idx = args.index("--input")
        if idx + 1 >= len(args):
            print("Error: --input requires a file path")
            sys.exit(1)
        path = args[idx + 1]
        del args[idx : idx + 2]
        if not Path(path).exists():
            print(f"Error: input file not found: {path}")
            sys.exit(1)
        input_type = _detect_input_type(path)
        inputs[input_type] = path

    # 3. --audio <path> (shorthand for --input with audio type).
    while "--audio" in args:
        idx = args.index("--audio")
        if idx + 1 >= len(args):
            print("Error: --audio requires a file path")
            sys.exit(1)
        path = args[idx + 1]
        del args[idx : idx + 2]
        if not Path(path).exists():
            print(f"Error: audio file not found: {path}")
            sys.exit(1)
        inputs["audio"] = path

    # 4. --image <path> (legacy flag, still supported).
    while "--image" in args:
        idx = args.index("--image")
        if idx + 1 >= len(args):
            print("Error: --image requires a file path")
            sys.exit(1)
        path = args[idx + 1]
        del args[idx : idx + 2]
        if not Path(path).exists():
            print(f"Error: image file not found: {path}")
            sys.exit(1)
        inputs["image"] = path

    return inputs