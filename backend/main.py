from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
import httpx
from urllib.parse import urlparse, unquote
import os
import re

app = FastAPI(
    title="SAHN Download Backend",
    version="0.2.0"
)


class AnalyzeRequest(BaseModel):
    url: HttpUrl


@app.get("/")
async def root():
    return {
        "success": True,
        "service": "SAHN Download Backend",
        "status": "online",
        "version": "0.2.0"
    }


@app.get("/health")
async def health():
    return {
        "success": True,
        "status": "healthy"
    }


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

    video_ext = {
        ".mp4", ".m4v", ".webm", ".mkv",
        ".mov", ".avi", ".wmv", ".flv"
    }

    audio_ext = {
        ".mp3", ".m4a", ".aac", ".wav",
        ".ogg", ".opus", ".flac"
    }

    image_ext = {
        ".jpg", ".jpeg", ".png", ".gif",
        ".webp", ".bmp", ".svg"
    }

    document_ext = {
        ".pdf", ".zip", ".rar", ".7z",
        ".doc", ".docx", ".xls", ".xlsx"
    }

    extension = os.path.splitext(name)[1]

    if extension in video_ext:
        return "video"

    if extension in audio_ext:
        return "audio"

    if extension in image_ext:
        return "image"

    if extension in document_ext:
        return "document"

    return "unknown"


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

    content_type = (content_type or "").lower()

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

                "message": (
                    "Media URL analyzed successfully."
                )
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
