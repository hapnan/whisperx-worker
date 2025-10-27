from dotenv import load_dotenv, find_dotenv
import os

# find and load your .env file and ya
load_dotenv(find_dotenv())
HF_TOKEN = os.getenv("HF_TOKEN")# 


######SETTING HF_TOKENT#############

from speaker_profiles import load_embeddings, relabel  # top of file
from speaker_processing import process_diarized_output,enroll_profiles, identify_speakers_on_segments, load_known_speakers_from_samples, identify_speaker, relabel_speakers_by_avg_similarity
import logging
from huggingface_hub import login, whoami
import torch
import numpy as np
from dotenv import load_dotenv, find_dotenv
from typing import Optional

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

from speechbrain.pretrained import EncoderClassifier # type: ignore

def spk_embed(wave_16k_mono: np.ndarray) -> np.ndarray:
    wav = torch.tensor(wave_16k_mono).unsqueeze(0).to(device)
    return ecapa.encode_batch(wav).squeeze(0).cpu().numpy()

def to_numpy(x):
    return x.detach().cpu().numpy() if isinstance(x, torch.Tensor) else np.asarray(x)

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Grab the HF_TOKEN from environment
raw_token = os.environ.get("HF_TOKEN", "")
hf_token = raw_token.strip()

if not hf_token.startswith("hf_"):
    print(f"Token malformed or missing 'hf_' prefix. Forcing correction...")
    hf_token = "h" + hf_token  # Force adding the 'h' (temporary fix)

#print(f" Final HF_TOKEN used: #{hf_token}")
if hf_token:
    try:
        logger.debug(f"HF_TOKEN Loaded: {repr(hf_token[:10])}...")  # Show only start of token for security
        login(token=hf_token, add_to_git_credential=False)  # Safe for container runs
        user = whoami(token=hf_token)
        logger.info(f"Hugging Face Authenticated as: {user['name']}")
    except Exception as e:
        logger.error(" Failed to authenticate with Hugging Face", exc_info=True)
else:
    logger.warning("No Hugging Face token found in HF_TOKEN environment variable.")
##############

import shutil
import runpod
from runpod.serverless.utils.rp_validator import validate
from runpod.serverless.utils import download_files_from_urls, rp_cleanup
from rp_schema import INPUT_VALIDATIONS
from predict import Predictor, Output
import os
import copy
import logging
import sys
# Create a custom logger
logger = logging.getLogger("rp_handler")
logger.setLevel(logging.DEBUG)  # capture everything at DEBUG or above

# Create console handler and set level to DEBUG
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter(
    "%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
console_handler.setFormatter(console_formatter)

# Create file handler to write logs to 'container_log.txt'
file_handler = logging.FileHandler("container_log.txt", mode="a")
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    "%(asctime)s %(levelname)s [%(name)s]: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)

# Add both handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)




MODEL = Predictor()
MODEL.setup()

# Re-add helpers used in run()
def _write_base64_audio(job_id: str, audio_b64: str, filename: Optional[str]) -> str:
    import base64
    safe_name = (filename or "audio").replace("/", "_").replace("\\", "_")
    if "." not in safe_name:
        safe_name += ".bin"
    job_dir = os.path.join("/jobs", job_id)
    os.makedirs(job_dir, exist_ok=True)
    out_path = os.path.join(job_dir, safe_name)
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(audio_b64))
    return out_path


def cleanup_job_files(job_id, jobs_directory='/jobs'):
    job_path = os.path.join(jobs_directory, job_id)
    if os.path.exists(job_path):
        try:
            shutil.rmtree(job_path)
            logger.info(f"Removed job directory: {job_path}")
        except Exception as e:
            logger.error(f"Error removing job directory {job_path}: {str(e)}", exc_info=True)
    else:
        logger.debug(f"Job directory not found: {job_path}")

# --- JSON sanitizer to ensure valid, serializable output ---
import math
from typing import Any

def _to_jsonable(obj: Any):
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (np.ndarray,)):
        return _to_jsonable(obj.tolist())
    if isinstance(obj, (int, str, bool)) or obj is None:
        return obj
    # Fallback: try to cast to string
    try:
        return str(obj)
    except Exception:
        return None

# Increase console verbosity to DEBUG
for h in logger.handlers:
    if isinstance(h, logging.StreamHandler):
        h.setLevel(logging.DEBUG)

def run(job):
    job_id     = job["id"]
    job_input  = job["input"]

    # ------------- validate basic schema ----------------------------
    validated = validate(job_input, INPUT_VALIDATIONS)
    if "errors" in validated:
        return {"error": validated["errors"]}

    # ------------- 1) obtain primary audio --------------------------
    # Prefer base64 if provided, else fall back to URL download
    audio_file_path = None
    try:
        audio_b64 = job_input.get("audio_base64")
        audio_fname = job_input.get("audio_filename")
        if audio_b64:
            audio_file_path = _write_base64_audio(job_id, audio_b64, audio_fname)
            logger.debug(f"Audio received as base64 → {audio_file_path}")
        else:
            audio_file_path = download_files_from_urls(job_id,
                                                       [job_input["audio_file"]])[0]
            logger.debug(f"Audio downloaded → {audio_file_path}")
    except Exception as e:
        logger.error("Audio acquisition failed", exc_info=True)
        return {"error": f"audio acquisition: {e}"}

    # ------------- 2) download speaker profiles (optional) ----------
    speaker_profiles = job_input.get("speaker_samples", [])
    embeddings = {}
    if speaker_profiles:
        try:
            embeddings = load_known_speakers_from_samples(
                speaker_profiles,
                huggingface_access_token=hf_token  # or job_input.get("huggingface_access_token")
            )
            logger.info(f"Enrolled {len(embeddings)} speaker profiles successfully.")
        except Exception as e:
            logger.error("Enrollment failed", exc_info=True)
            output_dict["warning"] = f"Enrollment skipped: {e}"
      
    # ----------------------------------------------------------------

    # ------------- 3) call WhisperX / VAD / diarization -------------
    predict_input = {
        "audio_file"               : audio_file_path,
        "language"                 : job_input.get("language"),
        "language_detection_min_prob": job_input.get("language_detection_min_prob", 0),
        "language_detection_max_tries": job_input.get("language_detection_max_tries", 5),
        "initial_prompt"           : job_input.get("initial_prompt"),
        "batch_size"               : job_input.get("batch_size", 64),
        "temperature"              : job_input.get("temperature", 0),
        "vad_onset"                : job_input.get("vad_onset", 0.50),
        "vad_offset"               : job_input.get("vad_offset", 0.363),
        "align_output"             : job_input.get("align_output", False),
        "diarization"              : job_input.get("diarization", False),
        "huggingface_access_token" : job_input.get("huggingface_access_token"),
        "min_speakers"             : job_input.get("min_speakers"),
        "max_speakers"             : job_input.get("max_speakers"),
        "debug"                    : job_input.get("debug", False),
    }

    try:
        result = MODEL.predict(**predict_input)             # <-- heavy job
    except Exception as e:
        logger.error("WhisperX prediction failed", exc_info=True)
        return {"error": f"prediction: {e}"}

    output_dict = {
        "segments"         : result.segments,
        "detected_language": result.detected_language
    }

    # Sanitize output to valid JSON
    try:
        output_dict = _to_jsonable(output_dict)
    except Exception:
        logger.warning("Failed to sanitize output; attempting to continue.", exc_info=True)

    # ------------------------------------------------embedding-info----------------
    # 4) speaker verification (optional)
    if embeddings:
        try:
            segments_with_speakers = identify_speakers_on_segments(
                segments=output_dict["segments"],
                audio_path=audio_file_path,
                enrolled=embeddings,
                threshold=0.1  # Adjust threshold as needed
            )
            #output_dict["segments"] = segments_with_speakers
            segments_with_final_labels = relabel_speakers_by_avg_similarity(segments_with_speakers)
            output_dict["segments"] = segments_with_final_labels
            logger.info("Speaker identification completed successfully.")
        except Exception as e:
            logger.error("Speaker identification failed", exc_info=True)
            output_dict["warning"] = f"Speaker identification skipped: {e}"
    else:
        logger.info("No enrolled embeddings available; skipping speaker identification.")

    # 4-Cleanup and return output_dict normally
    try:
        rp_cleanup.clean(["input_objects"])
        cleanup_job_files(job_id)
    except Exception as e:
        logger.warning(f"Cleanup issue: {e}", exc_info=True)

    return output_dict

runpod.serverless.start({"handler": run})

