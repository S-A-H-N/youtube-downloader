import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import boto3
import httpx
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel


APP_VERSION = "0.8.0"

DOWNLOAD_DIR = Path("/tmp/sahn-downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ["DATABASE_URL"]

B2_BUCKET_NAME = os.environ["B2_BUCKET_NAME"]
B2_REGION = os.environ.get("B2_REGION", "us-west-004")
B2_KEY_ID = os.environ["B2_KEY_ID"]
B2_APPLICATION_KEY = os.environ["B2_APPLICATION_KEY"]

B2_ENDPOINT = f"https://s3.{B2_REGION}.backblazeb2.com"

app = FastAPI(title="SAHN Download Backend", version=APP_VERSION)

active_tasks: dict[str, asyncio.Task] = {}
cancel_flags: dict[str, bool] = {}


class AnalyzeRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    filename: Optional[str] = None


def db_connect():
    return psycopg.connect(DATABASE_URL)


def init_db():
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    downloaded_bytes BIGINT NOT NULL DEFAULT 0,
                    total_bytes BIGINT,
                    progress DOUBLE PRECISION NOT NULL DEFAULT 0,
                    speed_bytes BIGINT NOT NULL DEFAULT 0,
                    content_type TEXT,
                    supports_resume BOOLEAN NOT NULL DEFAULT FALSE,
                    resumed_from_bytes BIGINT NOT NULL DEFAULT 0,
                    file_available BOOLEAN NOT NULL DEFAULT FALSE,
                    storage TEXT,
                    b2_bucket TEXT,
                    b2_object TEXT,
                    error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        conn.commit()


@app.on_event("startup")
async def startup():
    init_db()


def db_insert_job(job: dict):
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO downloads (
                    id, url, status, filename, downloaded_bytes,
                    total_bytes, progress, speed_bytes, content_type,
                    supports_resume, resumed_from_bytes, file_available,
                    storage, b2_bucket, b2_object, error
                )
                VALUES (
                    %(id)s, %(url)s, %(status)s, %(filename)s, %(downloaded_bytes)s,
                    %(total_bytes)s, %(progress)s, %(speed_bytes)s, %(content_type)s,
                    %(supports_resume)s, %(resumed_from_bytes)s, %(file_available)s,
                    %(storage)s, %(b2_bucket)s, %(b2_object)s, %(error)s
                )
                """,
                job,
            )
        conn.commit()


def db_update_job(download_id: str, **fields):
    if not fields:
        return

    fields["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    allowed = {
        "status",
        "filename",
        "downloaded_bytes",
        "total_bytes",
        "progress",
        "speed_bytes",
        "content_type",
        "supports_resume",
        "resumed_from_bytes",
        "file_available",
        "storage",
        "b2_bucket",
        "b2_object",
        "error",
        "updated_at",
    }

    fields = {k: v for k, v in fields.items() if k in allowed}

    assignments = ", ".join(f"{key} = %({key})s" for key in fields)

    query = f"""
        UPDATE downloads
        SET {assignments}
        WHERE id = %(download_id)s
    """

    fields["download_id"] = download_id

    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, fields)
        conn.commit()


def db_get_job(download_id: str):
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id, url, status, filename, downloaded_bytes,
                    total_bytes, progress, speed_bytes, content_type,
                    supports_resume, resumed_from_bytes, file_available,
                    storage, b2_bucket, b2_object, error,
                    created_at, updated_at
                FROM downloads
                WHERE id = %s
                """,
                (download_id,),
            )

            row = cur.fetchone()

            if not row:
                return None

            columns = [desc.name for desc in cur.description]
            return dict(zip(columns, row))


def sanitize_filename(filename: str) -> str:
    filename = os.path.basename(filename)
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)

    if not filename:
        filename = "download.bin"

    return filename[:180]


def filename_from_url(url: str) -> str:
    path = url.split("?", 1)[0].rstrip("/")
    name = path.rsplit("/", 1)[-1]

    if not name or "." not in name:
        name = "download.bin"

    return sanitize_filename(name)


def get_b2_client():
    return boto3.client(
        "s3",
        endpoint_url=B2_ENDPOINT,
        aws_access_key_id=B2_KEY_ID,
        aws_secret_access_key=B2_APPLICATION_KEY,
        region_name=B2_REGION,
    )


def upload_to_b2(file_path: Path, object_name: str, content_type: str):
    client = get_b2_client()

    client.upload_file(
        str(file_path),
        B2_BUCKET_NAME,
        object_name,
        ExtraArgs={
            "ContentType": content_type or "application/octet-stream"
        },
    )


def create_b2_download_url(object_name: str, filename: str):
    client = get_b2_client()

    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": B2_BUCKET_NAME,
            "Key": object_name,
            "ResponseContentDisposition": (
                f'attachment; filename="{quote(filename)}"'
            ),
        },
        ExpiresIn=900,
    )


async def analyze_url(url: str):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Only HTTP and HTTPS URLs are supported.",
        )

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=20,
    ) as client:
        try:
            response = await client.head(url)

            if response.status_code >= 400:
                response = await client.get(
                    url,
                    headers={"Range": "bytes=0-0"},
                )

        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Unable to access URL: {exc}",
            )

    content_type = response.headers.get(
        "content-type",
        "application/octet-stream",
    )

    content_length = response.headers.get("content-length")

    total_bytes = (
        int(content_length)
        if content_length and content_length.isdigit()
        else None
    )

    filename = filename_from_url(str(response.url))

    supports_range = response.headers.get(
        "accept-ranges",
        "",
    ).lower() == "bytes"

    return {
        "success": True,
        "stage": "analyzed",
        "source": {
            "url": url,
            "hostname": response.url.host,
        },
        "media": {
            "type": (
                "video"
                if content_type.startswith("video/")
                else "audio"
                if content_type.startswith("audio/")
                else "image"
                if content_type.startswith("image/")
                else "document"
            ),
            "content_type": content_type,
            "extension": filename.rsplit(".", 1)[-1]
            if "." in filename
            else None,
            "filename": filename,
            "size_bytes": total_bytes,
            "content_length": content_length,
        },
        "download": {
            "accessible": response.status_code < 400,
            "status_code": response.status_code,
            "supports_range": supports_range,
            "supports_resume": supports_range,
        },
        "message": "Media URL analyzed successfully.",
    }


@app.get("/")
async def root():
    return {
        "success": True,
        "service": "SAHN Download Backend",
        "status": "online",
        "version": APP_VERSION,
        "storage": "backblaze-b2",
        "database": "postgresql",
    }


@app.get("/health")
async def health():
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

        return {
            "success": True,
            "status": "healthy",
            "version": APP_VERSION,
            "storage": "backblaze-b2",
            "database": "postgresql",
        }

    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "status": "unhealthy",
                "database": "postgresql",
                "error": str(exc),
            },
        )


@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    return await analyze_url(request.url)


async def run_download(
    download_id: str,
    url: str,
    filename: str,
    resume: bool = False,
):
    file_path = DOWNLOAD_DIR / f"{download_id}_{filename}"

    try:
        existing_bytes = (
            file_path.stat().st_size
            if resume and file_path.exists()
            else 0
        )

        resumed_from = existing_bytes

        db_update_job(
            download_id,
            status="downloading",
            resumed_from_bytes=resumed_from,
            downloaded_bytes=existing_bytes,
            progress=0,
            error=None,
        )

        headers = {}

        if existing_bytes > 0:
            headers["Range"] = f"bytes={existing_bytes}-"

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=None,
        ) as client:

            async with client.stream(
                "GET",
                url,
                headers=headers,
            ) as response:

                response.raise_for_status()

                content_type = response.headers.get(
                    "content-type",
                    "application/octet-stream",
                )

                content_length = response.headers.get("content-length")

                if response.status_code == 206:
                    total_bytes = (
                        existing_bytes + int(content_length)
                        if content_length and content_length.isdigit()
                        else None
                    )
                    mode = "ab"
                    supports_resume = True

                else:
                    if existing_bytes > 0:
                        existing_bytes = 0
                        resumed_from = 0

                    total_bytes = (
                        int(content_length)
                        if content_length and content_length.isdigit()
                        else None
                    )

                    mode = "wb"
                    supports_resume = (
                        response.headers.get(
                            "accept-ranges",
                            "",
                        ).lower()
                        == "bytes"
                    )

                db_update_job(
                    download_id,
                    content_type=content_type,
                    total_bytes=total_bytes,
                    supports_resume=supports_resume,
                    resumed_from_bytes=resumed_from,
                )

                downloaded = existing_bytes
                started_at = time.monotonic()
                last_db_update = started_at
                last_downloaded = downloaded

                with open(file_path, mode) as output:

                    async for chunk in response.aiter_bytes(
                        chunk_size=1024 * 256
                    ):
                        if cancel_flags.get(download_id):
                            db_update_job(
                                download_id,
                                status="paused",
                                downloaded_bytes=downloaded,
                                progress=(
                                    downloaded / total_bytes * 100
                                    if total_bytes
                                    else 0
                                ),
                                error=None,
                            )
                            return

                        output.write(chunk)
                        downloaded += len(chunk)

                        now = time.monotonic()

                        if now - last_db_update >= 1.0:
                            elapsed = max(
                                now - started_at,
                                0.001,
                            )

                            speed = int(
                                (downloaded - last_downloaded)
                                / max(
                                    now - last_db_update,
                                    0.001,
                                )
                            )

                            progress = (
                                downloaded / total_bytes * 100
                                if total_bytes
                                else 0
                            )

                            db_update_job(
                                download_id,
                                downloaded_bytes=downloaded,
                                progress=progress,
                                speed_bytes=speed,
                            )

                            last_db_update = now
                            last_downloaded = downloaded

        db_update_job(
            download_id,
            status="uploading",
            downloaded_bytes=downloaded,
            progress=100 if total_bytes else 0,
            speed_bytes=0,
        )

        object_name = (
            f"downloads/{download_id}/{filename}"
        )

        await asyncio.to_thread(
            upload_to_b2,
            file_path,
            object_name,
            content_type,
        )

        db_update_job(
            download_id,
            status="completed",
            downloaded_bytes=downloaded,
            total_bytes=total_bytes or downloaded,
            progress=100,
            speed_bytes=0,
            storage="backblaze-b2",
            b2_bucket=B2_BUCKET_NAME,
            b2_object=object_name,
            file_available=True,
            error=None,
        )

        try:
            file_path.unlink()
        except FileNotFoundError:
            pass

    except asyncio.CancelledError:
        db_update_job(
            download_id,
            status="paused",
            error=None,
        )
        raise

    except Exception as exc:
        db_update_job(
            download_id,
            status="error",
            error=str(exc),
            file_available=False,
        )

    finally:
        active_tasks.pop(download_id, None)
        cancel_flags.pop(download_id, None)


@app.post("/download/start")
async def start_download(request: DownloadRequest):
    try:
        result = await analyze_url(request.url)

        filename = sanitize_filename(
            request.filename
            or result["media"]["filename"]
        )

        download_id = uuid.uuid4().hex[:12]

        job = {
            "id": download_id,
            "url": request.url,
            "status": "queued",
            "filename": filename,
            "downloaded_bytes": 0,
            "total_bytes": result["media"]["size_bytes"],
            "progress": 0,
            "speed_bytes": 0,
            "content_type": result["media"]["content_type"],
            "supports_resume": result["download"]["supports_resume"],
            "resumed_from_bytes": 0,
            "file_available": False,
            "storage": None,
            "b2_bucket": None,
            "b2_object": None,
            "error": None,
        }

        db_insert_job(job)

        cancel_flags[download_id] = False

        task = asyncio.create_task(
            run_download(
                download_id,
                request.url,
                filename,
                resume=False,
            )
        )

        active_tasks[download_id] = task

        return {
            "success": True,
            "id": download_id,
            "status": "queued",
            "filename": filename,
            "message": "Download started.",
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.get("/download/{download_id}")
async def get_download(download_id: str):
    job = db_get_job(download_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Download not found.",
        )

    return {
        "success": True,
        "download": job,
    }


@app.post("/download/{download_id}/cancel")
async def cancel_download(download_id: str):
    job = db_get_job(download_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Download not found.",
        )

    if job["status"] not in {
        "queued",
        "downloading",
        "uploading",
    }:
        return {
            "success": True,
            "status": job["status"],
        }

    cancel_flags[download_id] = True

    return {
        "success": True,
        "id": download_id,
        "status": "pausing",
        "message": "Download pause requested.",
    }


@app.post("/download/{download_id}/resume")
async def resume_download(download_id: str):
    job = db_get_job(download_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Download not found.",
        )

    if job["status"] == "completed":
        return {
            "success": True,
            "id": download_id,
            "status": "completed",
            "message": "Download is already completed.",
        }

    if download_id in active_tasks:
        return {
            "success": True,
            "id": download_id,
            "status": "downloading",
            "message": "Download is already running.",
        }

    file_path = (
        DOWNLOAD_DIR
        / f"{download_id}_{job['filename']}"
    )

    if not file_path.exists():
        raise HTTPException(
            status_code=409,
            detail=(
                "Partial file is not available on this server. "
                "A persistent partial-storage system is required "
                "to resume after a server restart."
            ),
        )

    cancel_flags[download_id] = False

    task = asyncio.create_task(
        run_download(
            download_id,
            job["url"],
            job["filename"],
            resume=True,
        )
    )

    active_tasks[download_id] = task

    db_update_job(
        download_id,
        status="queued",
        error=None,
    )

    return {
        "success": True,
        "id": download_id,
        "status": "queued",
        "message": "Download resume started.",
    }


@app.get("/download/{download_id}/file")
async def download_file(download_id: str):
    job = db_get_job(download_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Download not found.",
        )

    if not job["file_available"]:
        raise HTTPException(
            status_code=409,
            detail="File is not available yet.",
        )

    if (
        job["storage"] != "backblaze-b2"
        or not job["b2_object"]
    ):
        raise HTTPException(
            status_code=500,
            detail="File storage information is missing.",
        )

    try:
        url = create_b2_download_url(
            job["b2_object"],
            job["filename"],
        )

        return RedirectResponse(
            url=url,
            status_code=302,
        )

    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"B2 file delivery error: {exc}",
        )
