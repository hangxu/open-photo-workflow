#!/usr/bin/env python3
"""Optional, single-image Wan 2.6 edit; requires Pillow and an environment key.

Each new output directory permits one generation POST, with no automatic retry.
An unknown outcome may have been billed: inspect record.json before doing more.
Result bytes are preserved with their actual format; record.json names the file.
"""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_url(value):
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.fragment
            or re.search(r"[\s\\]", value)):
        raise ValueError("Use an HTTPS URL without credentials, fragments or whitespace.")
    _ = parts.port  # Reject malformed ports before any request.
    return parts


def endpoint(value):
    parts = https_url(value)
    if parts.query or parts.path.rstrip("/") not in ("", "/compatible-mode/v1", "/api/v1"):
        raise ValueError("--base-url must be an HTTPS origin, /compatible-mode/v1 or /api/v1.")
    return urlunsplit((parts.scheme, parts.netloc,
                      "/api/v1/services/aigc/multimodal-generation/generation", "", ""))


def inspect_image(data):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        info = {"width": image.width, "height": image.height, "format": image.format,
                "bytes": len(data), "frames": getattr(image, "n_frames", 1)}
        image.verify()
    return info


def write_record(directory, record):
    temporary = directory / "record.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(directory / "record.json")


def safe_token(value, key):
    return value if (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value)
                     and key not in value) else None


def numeric_usage(value, key):
    if isinstance(value, dict):
        return {name: numeric_usage(item, key) for name, item in value.items()
                if safe_token(name, key) and isinstance(item, (int, float, dict))}
    return value if isinstance(value, (int, float)) else None


def run(args):
    address = endpoint(args.base_url)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.api_key_env):
        raise ValueError("--api-key-env must name an environment variable.")
    key = os.environ.get(args.api_key_env, "").strip()
    if not key or "\r" in key or "\n" in key:
        raise ValueError("Set the API key environment variable before running; .env is not read.")
    prompt = args.prompt_file.read_text(encoding="utf-8")
    if not prompt.strip() or len(prompt) > 2000:
        raise ValueError("The UTF-8 prompt must contain 1–2000 characters.")
    with args.image.open("rb") as handle:
        data = handle.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Input exceeds 10 MB; explicitly export a smaller copy first.")
    info = inspect_image(data)
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "BMP": "image/bmp"}
    if info["format"] not in mime or info["frames"] != 1:
        raise ValueError("Use one still JPEG, PNG, WebP or BMP image.")
    if not all(240 <= info[axis] <= 8000 for axis in ("width", "height")):
        raise ValueError("Each input dimension must be 240–8000 pixels; export a suitable copy first.")
    parameters = {"n": 1, "size": args.size, "enable_interleave": False,
                  "prompt_extend": False, "watermark": False, "stream": False}
    payload = {"model": "wan2.6-image", "input": {"messages": [{"role": "user", "content": [
        {"text": prompt},
        {"image": "data:" + mime[info["format"]] + ";base64," + base64.b64encode(data).decode("ascii")}
        ]}]}, "parameters": parameters}
    request = Request(address, data=json.dumps(payload).encode("utf-8"), method="POST",
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        args.output.mkdir()  # Exclusive reservation; even incomplete directories cannot be reused.
    except FileExistsError:
        raise ValueError("Output already exists. Reuse is refused to prevent another paid generation.") from None
    record = {"model_requested": "wan2.6-image", "parameters": parameters, "input": info,
              "prompt_characters": len(prompt),
              "generation_post_limit": 1, "status": "outcome_unknown", "started_at_unix": time.time()}
    write_record(args.output, record)  # Durable marker before a potentially billable request.
    started, phase = time.monotonic(), "generation"
    opener = build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=180) as response:
            result = json.loads(response.read(2 * 1024 * 1024))
        record.update(request_id=safe_token(result.get("request_id"), key),
                      usage=numeric_usage(result.get("usage", {}), key),
                      generation_seconds=round(time.monotonic() - started, 3))
        content = result["output"]["choices"][0]["message"]["content"]
        urls = [item["image"] for item in content if isinstance(item, dict) and "image" in item]
        if len(urls) != 1:
            raise ValueError("Expected one completed image.")
        record["status"], phase = "generated", "download"
        write_record(args.output, record)
        https_url(urls[0])
        # This GET has no Authorization header and cannot redirect or trigger generation.
        with opener.open(Request(urls[0], method="GET"), timeout=120) as response:
            output = response.read(64 * 1024 * 1024 + 1)
        if len(output) > 64 * 1024 * 1024:
            raise ValueError("Result exceeds the 64 MB download limit.")
        record["result"] = inspect_image(output)
        image_format = record["result"]["format"]
        extension = {"JPEG": "jpg", "TIFF": "tif"}.get(image_format, image_format.lower())
        record["result"]["filename"] = "result." + extension
        with (args.output / record["result"]["filename"]).open("xb") as handle:
            handle.write(output)
        record["status"] = "completed"
    except (Exception, KeyboardInterrupt) as error:
        record["status"] = "outcome_unknown" if phase == "generation" else "download_failed"
        record["error_type"] = type(error).__name__
        if isinstance(error, HTTPError):
            record["http_status"] = error.code
            if phase == "generation":
                record["status"] = "http_error"
                try:
                    record["error_code"] = safe_token(json.loads(error.read(65536)).get("code"), key)
                except Exception:
                    pass
        detail = f" HTTP {record['http_status']} {record.get('error_code') or ''}" if "http_status" in record else ""
        print(f"{record['status']}:{detail} {phase} did not complete; no retry was made. See record.json.", file=sys.stderr)
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    write_record(args.output, record)
    return 0 if record["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, metavar="NEW_DIRECTORY")
    parser.add_argument("--base-url", required=True, help="Workspace HTTPS origin or compatible/API base URL")
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY")
    parser.add_argument("--size", choices=("1K", "2K"), default="2K")
    args = parser.parse_args()
    try:
        return run(args)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
    except (Exception, KeyboardInterrupt):
        print("Error: local input validation or file access failed; no automatic retry. Check any output record.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
