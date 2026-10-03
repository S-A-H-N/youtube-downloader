const BACKEND_URL =
  "https://sahn-download-backend.onrender.com";

const BACKEND_TIMEOUT_MS = 25000;
const YOUTUBE_TIMEOUT_MS = 15000;

export default {
  async fetch(request) {
    const url = new URL(request.url);

    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type"
    };

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders
      });
    }

    /*
     * HEALTH
     */

    if (
      request.method === "GET" &&
      (url.pathname === "/" ||
        url.pathname === "/health")
    ) {
      return jsonResponse(
        {
          success: true,
          service: "SAHN Download API",
          status: "online",
          version: "0.4.0",
          backend: BACKEND_URL
        },
        corsHeaders
      );
    }

    /*
     * ANALYZE
     */

    if (
      request.method === "POST" &&
      url.pathname === "/analyze"
    ) {
      return handleAnalyze(
        request,
        corsHeaders
      );
    }

    /*
     * DOWNLOAD PROXY
     *
     * Forward download job requests to the Render backend.
     * The /file endpoint is streamed as a binary response.
     */

    if (url.pathname.startsWith("/download/")) {
      return handleDownloadProxy(
        request,
        url,
        corsHeaders
      );
    }

    return jsonResponse(
      {
        success: false,
        error: "Endpoint not found."
      },
      corsHeaders,
      404
    );
  }
};


/*
 * ANALYZE
 */

async function handleAnalyze(
  request,
  corsHeaders
) {
  let body;

  try {
    body = await request.json();
  } catch (error) {
    return jsonResponse(
      {
        success: false,
        error: "Invalid JSON body."
      },
      corsHeaders,
      400
    );
  }

  const videoUrl =
    typeof body.url === "string"
      ? body.url.trim()
      : "";

  if (!videoUrl) {
    return jsonResponse(
      {
        success: false,
        error: "URL is required."
      },
      corsHeaders,
      400
    );
  }

  let parsedUrl;

  try {
    parsedUrl = new URL(videoUrl);
  } catch (error) {
    return jsonResponse(
      {
        success: false,
        error: "Invalid URL."
      },
      corsHeaders,
      400
    );
  }

  if (
    parsedUrl.protocol !== "https:" &&
    parsedUrl.protocol !== "http:"
  ) {
    return jsonResponse(
      {
        success: false,
        error:
          "Only HTTP and HTTPS URLs are supported."
      },
      corsHeaders,
      400
    );
  }

  const hostname =
    parsedUrl.hostname.toLowerCase();


  /*
   * YOUTUBE
   *
   * Metadata-only behavior.
   */

  if (isYouTubeHost(hostname)) {
    const metadata =
      await getYouTubeMetadata(
        parsedUrl.href
      );

    if (metadata) {
      return jsonResponse(
        {
          success: true,
          stage: "metadata_ready",

          source: {
            hostname: hostname,
            url: parsedUrl.href
          },

          metadata: {
            title: metadata.title,
            thumbnail: metadata.thumbnail,
            duration: null
          },

          formats: [],

          message:
            "Video metadata loaded successfully. Download formats require an authorized media provider."
        },
        corsHeaders
      );
    }

    return jsonResponse(
      {
        success: true,
        stage: "url_validated",

        source: {
          hostname: hostname,
          url: parsedUrl.href
        },

        metadata: {
          title: null,
          thumbnail: null,
          duration: null
        },

        formats: [],

        message:
          "URL is valid, but metadata could not be loaded."
      },
      corsHeaders
    );
  }


  /*
   * GENERIC / AUTHORIZED MEDIA URL
   *
   * Forward request to Render backend.
   *
   * IMPORTANT:
   * Cloudflare Worker now has a hard timeout
   * so a slow backend cannot hang the frontend
   * indefinitely.
   */

  try {
    const controller =
      new AbortController();

    const timeoutId =
      setTimeout(
        () => controller.abort(),
        BACKEND_TIMEOUT_MS
      );

    let backendResponse;

    try {
      backendResponse =
        await fetch(
          BACKEND_URL + "/analyze",
          {
            method: "POST",

            headers: {
              "Content-Type":
                "application/json"
            },

            body: JSON.stringify({
              url: parsedUrl.href
            }),

            signal:
              controller.signal
          }
        );
    } finally {
      clearTimeout(timeoutId);
    }


    /*
     * Verify response type.
     */

    const contentType =
      backendResponse.headers.get(
        "content-type"
      ) || "";

    if (
      !contentType.includes(
        "application/json"
      )
    ) {
      return jsonResponse(
        {
          success: false,
          error:
            "Backend returned an unexpected response."
        },
        corsHeaders,
        502
      );
    }


    /*
     * Read backend JSON.
     */

    const data =
      await backendResponse.json();

    return jsonResponse(
      data,
      corsHeaders,
      backendResponse.status
    );

  } catch (error) {

    console.error(
      "Backend connection error:",
      error
    );


    /*
     * Timeout
     */

    if (
      error &&
      error.name === "AbortError"
    ) {
      return jsonResponse(
        {
          success: false,
          error:
            "Backend request timed out. Please try again."
        },
        corsHeaders,
        504
      );
    }


    /*
     * General connection error
     */

    return jsonResponse(
      {
        success: false,
        error:
          "Backend is temporarily unavailable. Please try again."
      },
      corsHeaders,
      502
    );
  }
}


/*
 * DOWNLOAD PROXY
 */

async function handleDownloadProxy(
  request,
  url,
  corsHeaders
) {
  const targetUrl =
    BACKEND_URL +
    url.pathname +
    url.search;

  const headers = new Headers();

  for (const [key, value] of request.headers) {
    if (
      key.toLowerCase() !== "host" &&
      key.toLowerCase() !== "content-length"
    ) {
      headers.set(key, value);
    }
  }

  const options = {
    method: request.method,
    headers
  };

  if (
    request.method !== "GET" &&
    request.method !== "HEAD"
  ) {
    options.body = request.body;
  }

  try {
    const backendResponse =
      await fetch(targetUrl, options);

    const responseHeaders =
      new Headers(backendResponse.headers);

    responseHeaders.set(
      "Access-Control-Allow-Origin",
      corsHeaders["Access-Control-Allow-Origin"]
    );

    responseHeaders.set(
      "Access-Control-Allow-Methods",
      corsHeaders["Access-Control-Allow-Methods"]
    );

    responseHeaders.set(
      "Access-Control-Allow-Headers",
      corsHeaders["Access-Control-Allow-Headers"]
    );

    return new Response(
      backendResponse.body,
      {
        status: backendResponse.status,
        statusText: backendResponse.statusText,
        headers: responseHeaders
      }
    );

  } catch (error) {
    console.error(
      "Download proxy error:",
      error
    );

    return jsonResponse(
      {
        success: false,
        error:
          "Download backend is temporarily unavailable."
      },
      corsHeaders,
      502
    );
  }
}


/*
 * YOUTUBE HOST CHECK
 */

function isYouTubeHost(
  hostname
) {
  return (
    hostname === "youtube.com" ||
    hostname === "www.youtube.com" ||
    hostname === "m.youtube.com" ||
    hostname === "youtu.be" ||
    hostname === "www.youtu.be"
  );
}


/*
 * YOUTUBE METADATA
 */

async function getYouTubeMetadata(
  videoUrl
) {
  const controller =
    new AbortController();

  const timeoutId =
    setTimeout(
      () => controller.abort(),
      YOUTUBE_TIMEOUT_MS
    );

  try {
    const endpoint =
      "https://www.youtube.com/oembed?url=" +
      encodeURIComponent(videoUrl) +
      "&format=json";

    const response =
      await fetch(
        endpoint,
        {
          method: "GET",

          headers: {
            "Accept":
              "application/json"
          },

          signal:
            controller.signal
        }
      );

    if (!response.ok) {
      return null;
    }

    const data =
      await response.json();

    return {
      title:
        typeof data.title === "string"
          ? data.title
          : null,

      thumbnail:
        typeof data.thumbnail_url === "string"
          ? data.thumbnail_url
          : null
    };

  } catch (error) {

    console.error(
      "YouTube metadata error:",
      error
    );

    return null;

  } finally {
    clearTimeout(timeoutId);
  }
}


/*
 * JSON RESPONSE
 */

function jsonResponse(
  data,
  corsHeaders,
  status = 200
) {
  return new Response(
    JSON.stringify(
      data,
      null,
      2
    ),
    {
      status: status,

      headers: {
        "Content-Type":
          "application/json; charset=UTF-8",

        ...corsHeaders
      }
    }
  );
}
