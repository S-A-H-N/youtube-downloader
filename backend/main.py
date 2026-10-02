from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, HttpUrl
import httpx
from urllib.parse import urlparse, unquote
import os
import re
import uuid
import asyncio
import time
import json
from pathlib import Path
import boto3
from botocore.exceptions import BotoCoreError, ClientError


app = FastAPI(
    title="SAHN Download Backend",
    version="0.7.0"
)


DOWNLOAD_DIR = Path("/tmp/sahn-downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

JOBS_FILE = DOWNLOAD_DIR / "jobs.json"

downloads = {}


# ---------------------------------------------------------
# B2 CONFIGURATION
# ---------------------------------------------------------

B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME")
B2_REGION = os.getenv("B2_REGION", "us-west-004")
B2_KEY_ID = os.getenv("B2_KEY_ID")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY")

B2_ENDPOINT = (
    f"https://s3.{B2_REGION}.backblazeb2.com"
)


def get_b2_client():
    if not B2_BUCKET_NAME:
        raise RuntimeError("B2_BUCKET_NAME is not configured.")

    if not B2_KEY_ID:
        raise RuntimeError("B2_KEY_ID is not configured.")

    if not B2_APPLICATION_KEY:
        raise RuntimeError(
            "B2_APPLICATION_KEY is not configured."
        )

    return boto3.client(
        "s3",
        endpoint_url=B2_ENDPOINT,
        aws_access_key_id=B2_KEY_ID,
        aws_secret_access_key=B2_APPLICATION_KEY,
        region_name=B2_REGION
    )


# ---------------------------------------------------------
# MODELS
# ---------------------------------------------------------

class AnalyzeRequest(BaseModel):
    url: HttpUrl


class DownloadStartRequest(BaseModel):
    url: HttpUrl
    filename: str | None = None


# ---------------------------------------------------------
# JOB PERSISTENCE
# ---------------------------------------------------------

def load_jobs():
    global downloads

    if not JOBS_FILE.exists():
        downloads = {}
        return

    try:
        with open(
            JOBS_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            downloads = json.load(file)

    except Exception:
        downloads = {}


def save_jobs():
    temp_file = JOBS_FILE.with_suffix(".tmp")

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            downloads,
            file,
            ensure_ascii=False,
            indent=2
        )

    temp_file.replace(JOBS_FILE)


@app.on_event("startup")
async def startup_event():
    load_jobs()


# ---------------------------------------------------------
# BASIC ENDPOINTS
# ---------------------------------------------------------

@app.get("/")
async def root():
    return {
        "success": True,
        "service": "SAHN Download Backend",
        "status": "online",
        "version": "0.7.0",
        "storage": "backblaze-b2"
    }


@app.get("/health")
async def health():
    return {
        "success": True,
        "status": "healthy",
        "storage": "backblaze-b2"
    }


# ---------------------------------------------------------
# FILE HELPERS
# ---------------------------------------------------------

def extract_filename(response):
    content_disposition = (
        response.headers.get("content-disposition")
        or ""
    )

    match = re.search(
        r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?',
        content_disposition,
        re.IGNORECASE
    )

    if match:
        return unquote(match.group(1))

    path = urlparse(str(response.url)).path

    if path:
        filename = os.path.basename(path)

        if filename:
            return unquote(filename)

    return None


def detect_extension(filename, content_type):
    if filename:
        extension = os.path.splitext(filename)[1]

        if extension:
            return extension.lower().lstrip(".")

    content_type = (
        content_type or ""
    ).lower().split(";")[0]

    mime_map = {
        "video/mp4": "mp4",
        "video/webm": "webm",
        "video/quicktime": "mov",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/wav": "wav",
        "audio/ogg": "ogg",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "application/pdf": "pdf"
    }

    return mime_map.get(content_type)


def detect_media_type(content_type, filename):
    value = (content_type or "").lower()
    name = (filename or "").lower()

    if value.startswith("video/"):
        return "video"

    if value.startswith("audio/"):
        return "audio"

    if value.startswith("image/"):
        return "image"

    if value.startswith("application/pdf"):
        return "document"

    extension = os.path.splitext(name)[1]

    if extension in {
        ".mp4", ".m4v", ".webm", ".mkv",
        ".mov", ".avi", ".wmv", ".flv"
    }:
        return "video"

    if extension in {
        ".mp3", ".m4a", ".aac", ".wav",
        ".ogg", ".opus", ".flac"
    }:
        return "audio"

    if extension in {
        ".jpg", ".jpeg", ".png", ".gif",
        ".webp", ".bmp", ".svg"
    }:
        return "image"

    if extension in {
        ".pdf", ".zip", ".rar", ".7z",
        ".doc", ".docx", ".xls", ".xlsx"
    }:
        return "document"

    return "unknown"


def safe_filename(filename):
    filename = os.path.basename(filename)

    filename = re.sub(
        r'[^a-zA-Z0-9._-]',
        "_",
        filename
    )

    return filename or "download"


async def check_range_support(client, url):
    try:
        response = await client.get(
            url,
            headers={"Range": "bytes=0-0"}
        )

        return response.status_code == 206

    except httpx.HTTPError:
        return False


# ---------------------------------------------------------
# ANALYZE
# ---------------------------------------------------------

@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    url = str(request.url)

    try:
        timeout = httpx.Timeout(
            connect=10.0,
            read=20.0,
            write=20.0,
            pool=10.0
        )

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout
        ) as client:

            response = await client.head(url)

            content_type = (
                response.headers.get("content-type")
                or ""
            )

            content_length = (
                response.headers.get("content-length")
            )

            final_url = str(response.url)
            hostname = response.url.host

            filename = extract_filename(response)

            extension = detect_extension(
                filename,
                content_type
            )

            media_type = detect_media_type(
                content_type,
                filename
            )

            range_supported = (
                response.headers.get(
                    "accept-ranges",
                    ""
                ).lower() == "bytes"
            )

            if not range_supported:
                range_supported = await check_range_support(
                    client,
                    final_url
                )

            try:
                size_bytes = (
                    int(content_length)
                    if content_length
                    else None
                )
            except ValueError:
                size_bytes = None

            return {
                "success": True,
                "stage": "analyzed",
                "source": {
                    "url": final_url,
                    "hostname": hostname
                },
                "media": {
                    "type": media_type,
                    "content_type": content_type,
                    "extension": extension,
                    "filename": filename,
                    "size_bytes": size_bytes,
                    "content_length": content_length
                },
                "download": {
                    "accessible": response.status_code < 400,
                    "status_code": response.status_code,
                    "supports_range": range_supported,
                    "supports_resume": range_supported
                },
                "message": "Media URL analyzed successfully."
            }

    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Unable to analyze URL: {error}"
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Internal analysis error: {error}"
        )


# ---------------------------------------------------------
# B2 UPLOAD
# ---------------------------------------------------------

def upload_to_b2(file_path, object_name, content_type):
    client = get_b2_client()

    extra_args = {}

    if content_type:
        extra_args["ContentType"] = (
            content_type.split(";")[0].strip()
        )

    if extra_args:
        client.upload_file(
            str(file_path),
            B2_BUCKET_NAME,
            object_name,
            ExtraArgs=extra_args
        )
    else:
        client.upload_file(
            str(file_path),
            B2_BUCKET_NAME,
            object_name
        )


def create_b2_download_url(object_name, filename):
    client = get_b2_client()

    response_headers = {
        "ResponseContentDisposition": (
            f'attachment; filename="{filename}"'
        )
    }

    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": B2_BUCKET_NAME,
            "Key": object_name,
            **response_headers
        },
        ExpiresIn=900
    )


# ---------------------------------------------------------
# DOWNLOAD ENGINE
# ---------------------------------------------------------

async def run_download(
    download_id,
    url,
    filename,
    resume=True
):
    item = downloads[download_id]

    file_path = DOWNLOAD_DIR / filename

    offset = (
        file_path.stat().st_size
        if resume and file_path.exists()
        else 0
    )

    item["status"] = "downloading"
    item["cancel_requested"] = False
    item["downloaded_bytes"] = offset
    item["resumed_from_bytes"] = offset

    save_jobs()

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=None
        ) as client:

            headers = {}

            if offset > 0:
                headers["Range"] = f"bytes={offset}-"

            async with client.stream(
                "GET",
                url,
                headers=headers
            ) as response:

                response.raise_for_status()

                if (
                    offset > 0
                    and response.status_code != 206
                ):
                    offset = 0

                    item["downloaded_bytes"] = 0
                    item["resumed_from_bytes"] = 0

                    try:
                        file_path.unlink(
                            missing_ok=True
                        )
                    except Exception:
                        pass

                content_length = (
                    response.headers.get(
                        "content-length"
                    )
                )

                if content_length:
                    content_length = int(
                        content_length
                    )

                if (
                    response.status_code == 206
                    and offset > 0
                    and content_length is not None
                ):
                    total = (
                        offset + content_length
                    )
                else:
                    total = content_length

                item["total_bytes"] = total

                item["content_type"] = (
                    response.headers.get(
                        "content-type"
                    )
                    or ""
                )

                item["status_code"] = (
                    response.status_code
                )

                item["supports_resume"] = (
                    response.status_code == 206
                    or response.headers.get(
                        "accept-ranges",
                        ""
                    ).lower() == "bytes"
                )

                mode = (
                    "ab"
                    if offset > 0
                    else "wb"
                )

                downloaded = offset

                last_time = time.time()
                last_bytes = downloaded

                with open(
                    file_path,
                    mode
                ) as file:

                    async for chunk in response.aiter_bytes(
                        chunk_size=1024 * 256
                    ):

                        if item["cancel_requested"]:
                            item["status"] = "paused"
                            item["downloaded_bytes"] = (
                                downloaded
                            )

                            save_jobs()
                            return

                        file.write(chunk)
                        downloaded += len(chunk)

                        item["downloaded_bytes"] = (
                            downloaded
                        )

                        now = time.time()

                        if now - last_time >= 1:
                            elapsed = (
                                now - last_time
                            )

                            speed = (
                                downloaded
                                - last_bytes
                            ) / elapsed

                            item["speed_bytes"] = (
                                int(speed)
                            )

                            last_time = now
                            last_bytes = downloaded

                        if total:
                            item["progress"] = round(
                                (
                                    downloaded
                                    / total
                                ) * 100,
                                2
                            )
                        else:
                            item["progress"] = None

                        save_jobs()

                        await asyncio.sleep(0)

                # -----------------------------------------
                # DOWNLOAD COMPLETE
                # -----------------------------------------

                item["status"] = "uploading"
                item["progress"] = 100
                item["downloaded_bytes"] = downloaded

                save_jobs()

                object_name = (
                    f"downloads/{download_id}/{filename}"
                )

                upload_to_b2(
                    file_path,
                    object_name,
                    item["content_type"]
                )

                item["storage"] = "backblaze-b2"
                item["b2_bucket"] = (
                    B2_BUCKET_NAME
                )
                item["b2_object"] = object_name
                item["file_available"] = True
                item["status"] = "completed"
                item["completed_at"] = time.time()

                save_jobs()

                # Local temporary copy is no longer needed.
                try:
                    file_path.unlink(
                        missing_ok=True
                    )
                except Exception:
                    pass

                item["file_path"] = None

                save_jobs()

    except asyncio.CancelledError:
        item["status"] = "paused"
        save_jobs()

    except Exception as error:
        item["status"] = "failed"
        item["error"] = str(error)
        save_jobs()


# ---------------------------------------------------------
# START DOWNLOAD
# ---------------------------------------------------------

@app.post("/download/start")
async def start_download(
    request: DownloadStartRequest
):
    url = str(request.url)

    parsed = urlparse(url)

    if parsed.scheme not in {
        "http",
        "https"
    }:
        raise HTTPException(
            status_code=400,
            detail="Only HTTP and HTTPS URLs are supported."
        )

    download_id = uuid.uuid4().hex[:12]

    filename = request.filename

    if not filename:
        filename = os.path.basename(
            parsed.path
        )

    if not filename:
        filename = (
            f"download-{download_id}"
        )

    filename = safe_filename(filename)

    file_path = DOWNLOAD_DIR / filename

    if file_path.exists():
        stem = Path(filename).stem
        suffix = Path(filename).suffix

        filename = (
            f"{stem}-{download_id}{suffix}"
        )

    downloads[download_id] = {
        "id": download_id,
        "status": "queued",
        "url": url,
        "filename": filename,
        "downloaded_bytes": 0,
        "total_bytes": None,
        "progress": 0,
        "speed_bytes": 0,
        "content_type": None,
        "status_code": None,
        "supports_resume": False,
        "resumed_from_bytes": 0,
        "cancel_requested": False,
        "file_path": None,
        "file_available": False,
        "storage": None,
        "b2_bucket": None,
        "b2_object": None,
        "error": None
    }

    save_jobs()

    asyncio.create_task(
        run_download(
            download_id,
            url,
            filename,
            resume=False
        )
    )

    return {
        "success": True,
        "id": download_id,
        "status": "queued",
        "filename": filename,
        "message": "Download started."
    }


# ---------------------------------------------------------
# RESUME
# ---------------------------------------------------------

@app.post(
    "/download/{download_id}/resume"
)
async def resume_download(download_id):
    item = downloads.get(download_id)

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Download not found."
        )

    if item["status"] == "completed":
        return {
            "success": True,
            "id": download_id,
            "status": "completed",
            "message": (
                "Download is already completed."
            )
        }

    if item["status"] in {
        "downloading",
        "uploading"
    }:
        return {
            "success": True,
            "id": download_id,
            "status": item["status"],
            "message": (
                "Download is already running."
            )
        }

    item["status"] = "queued"
    item["error"] = None
    item["cancel_requested"] = False

    save_jobs()

    asyncio.create_task(
        run_download(
            download_id,
            item["url"],
            item["filename"],
            resume=True
        )
    )

    return {
        "success": True,
        "id": download_id,
        "status": "queued",
        "message": "Resume requested."
    }


# ---------------------------------------------------------
# STATUS
# ---------------------------------------------------------

@app.get(
    "/download/{download_id}"
)
async def get_download(download_id):
    item = downloads.get(download_id)

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Download not found."
        )

    return {
        "success": True,
        "download": {
            "id": item["id"],
            "status": item["status"],
            "filename": item["filename"],
            "downloaded_bytes": item[
                "downloaded_bytes"
            ],
            "total_bytes": item[
                "total_bytes"
            ],
            "progress": item[
                "progress"
            ],
            "speed_bytes": item[
                "speed_bytes"
            ],
            "content_type": item[
                "content_type"
            ],
            "supports_resume": item[
                "supports_resume"
            ],
            "resumed_from_bytes": item[
                "resumed_from_bytes"
            ],
            "file_available": item.get(
                "file_available",
                False
            ),
            "storage": item.get(
                "storage"
            ),
            "error": item["error"]
        }
    }


# ---------------------------------------------------------
# FILE DELIVERY
# ---------------------------------------------------------

@app.get(
    "/download/{download_id}/file"
)
async def get_download_file(download_id):
    item = downloads.get(download_id)

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Download not found."
        )

    if item["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail="Download is not completed yet."
        )

    object_name = item.get("b2_object")

    if not object_name:
        raise HTTPException(
            status_code=404,
            detail="B2 object is unavailable."
        )

    try:
        url = create_b2_download_url(
            object_name,
            item["filename"]
        )

        return RedirectResponse(
            url=url,
            status_code=302
        )

    except (
        BotoCoreError,
        ClientError,
        RuntimeError
    ) as error:
        raise HTTPException(
            status_code=500,
            detail=f"B2 file delivery error: {error}"
        )


# ---------------------------------------------------------
# CANCEL / PAUSE
# ---------------------------------------------------------

@app.post(
    "/download/{download_id}/cancel"
)
async def cancel_download(download_id):
    item = downloads.get(download_id)

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Download not found."
        )

    if item["status"] in {
        "completed",
        "failed",
        "paused"
    }:
        return {
            "success": True,
            "id": download_id,
            "status": item["status"],
            "message": (
                "Download is already stopped."
            )
        }

    item["cancel_requested"] = True

    save_jobs()

    return {
        "success": True,
        "id": download_id,
        "status": "cancelling",
        "message": (
            "Download pause requested."
        )
    }
