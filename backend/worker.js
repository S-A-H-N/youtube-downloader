const BACKEND_URL =
  "https://sahn-download-backend.onrender.com";

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

    if (
      request.method === "GET" &&
      (url.pathname === "/" || url.pathname === "/health")
    ) {
      return jsonResponse(
        {
          success: true,
          service: "SAHN Download API",
          status: "online",
          version: "0.3.0",
          backend: BACKEND_URL
        },
        corsHeaders
      );
    }

    if (
      request.method === "POST" &&
      url.pathname === "/analyze"
    ) {
      return handleAnalyze(request, corsHeaders);
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
        error: "Only HTTP and HTTPS URLs are supported."
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
   * Keep metadata-only behavior.
   */

  if (isYouTubeHost(hostname)) {
    const metadata =
      await getYouTubeMetadata(parsedUrl.href);

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
   * Send the request to Render Backend.
   */

  try {
    const backendResponse =
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
          })
        }
      );

    const contentType =
      backendResponse.headers.get(
        "content-type"
      ) || "";

    if (!contentType.includes("application/json")) {
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
 * YOUTUBE HOST CHECK
 */

function isYouTubeHost(hostname) {
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

async function getYouTubeMetadata(videoUrl) {
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
          }
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
