from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
import httpx

app = FastAPI(
    title="SAHN Download Backend",
    version="0.1.1"
)


class AnalyzeRequest(BaseModel):
    url: HttpUrl


@app.get("/")
async def root():
    return {
        "success": True,
        "service": "SAHN Download Backend",
        "status": "online",
        "version": "0.1.1"
    }


@app.get("/health")
async def health():
    return {
        "success": True,
        "status": "healthy"
    }


@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    url = str(request.url)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0
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

            return {
                "success": True,
                "stage": "analyzed",

                "source": {
                    "url": final_url,
                    "hostname": hostname
                },

                "media": {
                    "content_type": content_type,
                    "content_length": content_length
                },

                "message": "URL analyzed successfully."
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
