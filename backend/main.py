from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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

app = FastAPI(
    title="SAHN Download Backend",
    version="0.5.0"
)

DOWNLOAD_DIR = Path("/tmp/sahn-downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

JOBS_FILE = DOWNLOAD_DIR / "jobs.json"

downloads = {}


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class DownloadStartRequest(BaseModel):
    url: HttpUrl
    filename: str | None = None


def load_jobs():
    global downloads

    if not JOBS_FILE.exists():
        downloads = {}
        return

    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as file:
            downloads = json.load(file)
    except Exception:
        downloads = {}


def save_jobs():
    temp_file = JOBS_FILE.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as file:
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


@app.get("/")
async def root():
    return {
        "success": True,
        "service": "SAHN Download Backend",
        "status": "online",
        "version": "0.5.0"
    }


@app.get("/health")
async def health():
    return {
        "success": True,
        "status": "healthy"
    }


def extract_filename(response: httpx.Response):
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


def detect_extension(filename: str, content_type: str):
    if filename:
        extension = os.path.splitext(filename)[1]

        if extension:
            return extension.lower().lstrip(".")

    content_type = (content_type or "").lower().split(";")[0]

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


def detect_media_type(content_type: str, filename: str):
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


def safe_filename(filename: str):
    filename = os.path.basename(filename)

    filename = re.sub(
        r'[^a-zA-Z0-9._-]',
        "_",
        filename
    )

    if not filename:
        filename = "download"

    return filename


async def check_range_support(client, url):
    try:
        response = await client.get(
            url,
            headers={
                "Range": "bytes=0-0"
            }
        )

        return response.status_code == 206

    except httpx.HTTPError:
        return False


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


async def run_download(download_id: str, url: str, filename: str):
    item = downloads[download_id]

    file_path = DOWNLOAD_DIR / filename

    item["status"] = "downloading"
    item["started_at"] = time.time()
    save_jobs()

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=None
        ) as client:

            async with client.stream(
                "GET",
                url
            ) as response:

                response.raise_for_status()

                total = response.headers.get(
                    "content-length"
                )

                item["total_bytes"] = (
                    int(total)
                    if total
                    else None
                )

                item["content_type"] = (
                    response.headers.get(
                        "content-type"
                    )
                    or ""
                )

                item["status_code"] = response.status_code

                downloaded = 0
                last_time = time.time()
                last_bytes = 0

                with open(file_path, "wb") as file:

                    async for chunk in response.aiter_bytes(
                        chunk_size=1024 * 256
                    ):

                        if item["cancel_requested"]:
                            item["status"] = "cancelled"
                            save_jobs()

                            try:
                                file_path.unlink(
                                    missing_ok=True
                                )
                            except Exception:
                                pass

                            return

                        file.write(chunk)
                        downloaded += len(chunk)

                        item["downloaded_bytes"] = downloaded

                        now = time.time()

                        if now - last_time >= 1:
                            elapsed = now - last_time

                            speed = (
                                downloaded - last_bytes
                            ) / elapsed

                            item["speed_bytes"] = int(speed)

                            last_time = now
                            last_bytes = downloaded

                        if item["total_bytes"]:
                            item["progress"] = round(
                                (
                                    downloaded
                                    / item["total_bytes"]
                                ) * 100,
                                2
                            )
                        else:
                            item["progress"] = None

                        save_jobs()

                        await asyncio.sleep(0)

                item["status"] = "completed"
                item["progress"] = 100
                item["file_path"] = str(file_path)
                item["completed_at"] = time.time()

                save_jobs()

    except asyncio.CancelledError:
        item["status"] = "cancelled"
        save_jobs()

        try:
            file_path.unlink(
                missing_ok=True
            )
        except Exception:
            pass

    except Exception as error:
        item["status"] = "failed"
        item["error"] = str(error)
        save_jobs()


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
        filename = f"download-{download_id}"

    filename = safe_filename(filename)

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
        "cancel_requested": False,
        "file_path": None,
        "error": None
    }

    save_jobs()

    asyncio.create_task(
        run_download(
            download_id,
            url,
            filename
        )
    )

    return {
        "success": True,
        "id": download_id,
        "status": "queued",
        "filename": filename,
        "message": "Download started."
    }


@app.get("/download/{download_id}")
async def get_download(
    download_id: str
):
    item = downloads.get(download_id)

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Download not found."
        )

    file_path = item.get("file_path")

    file_exists = (
        bool(file_path)
        and Path(file_path).exists()
    )

    return {
        "success": True,
        "download": {
            "id": item["id"],
            "status": item["status"],
            "filename": item["filename"],
            "downloaded_bytes": item["downloaded_bytes"],
            "total_bytes": item["total_bytes"],
            "progress": item["progress"],
            "speed_bytes": item["speed_bytes"],
            "content_type": item["content_type"],
            "supports_resume": False,
            "file_available": file_exists,
            "error": item["error"]
        }
    }


@app.get("/download/{download_id}/file")
async def get_download_file(
    download_id: str
):
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

    file_path = item.get("file_path")

    if not file_path:
        raise HTTPException(
            status_code=404,
            detail="Downloaded file is unavailable."
        )

    path = Path(file_path)

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Downloaded file no longer exists."
        )

    return FileResponse(
        path=str(path),
        filename=item["filename"],
        media_type=item["content_type"]
        or "application/octet-stream"
    )


@app.post("/download/{download_id}/cancel")
async def cancel_download(
    download_id: str
):
    item = downloads.get(download_id)

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Download not found."
        )

    if item["status"] in {
        "completed",
        "failed",
        "cancelled"
    }:
        return {
            "success": True,
            "id": download_id,
            "status": item["status"],
            "message": "Download is already finished."
        }

    item["cancel_requested"] = True
    save_jobs()

    return {
        "success": True,
        "id": download_id,
        "status": "cancelling",
        "message": "Download cancellation requested."
    }
