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
          version: "0.2.0"
        },
        corsHeaders
      );
    }

    if (
      request.method === "POST" &&
      url.pathname === "/analyze"
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
       */

      if (
        hostname === "youtube.com" ||
        hostname === "www.youtube.com" ||
        hostname === "m.youtube.com" ||
        hostname === "youtu.be" ||
        hostname === "www.youtu.be"
      ) {
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
                "Video metadata loaded successfully. Format provider is not connected yet."
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
       * GENERIC URL
       */

      const genericMetadata =
        await getGenericMetadata(parsedUrl.href);

      return jsonResponse(
        {
          success: true,

          stage:
            genericMetadata
              ? "metadata_ready"
              : "url_validated",

          source: {
            hostname: hostname,
            url: parsedUrl.href
          },

          metadata: {
            title:
              genericMetadata?.title || null,

            thumbnail:
              genericMetadata?.thumbnail || null,

            duration: null
          },

          formats: [],

          message:
            genericMetadata
              ? "Page metadata loaded successfully."
              : "URL received successfully. Metadata was not available."
        },
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
 * YOUTUBE METADATA
 */

async function getYouTubeMetadata(videoUrl) {
  try {
    const endpoint =
      "https://www.youtube.com/oembed?url=" +
      encodeURIComponent(videoUrl) +
      "&format=json";

    const response =
      await fetch(endpoint, {
        method: "GET",
        headers: {
          "Accept": "application/json"
        }
      });

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
 * GENERIC PAGE METADATA
 */

async function getGenericMetadata(pageUrl) {
  try {
    const response =
      await fetch(pageUrl, {
        method: "GET",
        headers: {
          "User-Agent":
            "Mozilla/5.0 (compatible; SAHN-Download/0.2)"
        }
      });

    if (!response.ok) {
      return null;
    }

    const contentType =
      response.headers.get("content-type") || "";

    if (
      !contentType.includes("text/html")
    ) {
      return null;
    }

    const html =
      await response.text();

    const title =
      extractMeta(
        html,
        "og:title"
      ) ||
      extractTitle(html);

    const thumbnail =
      extractMeta(
        html,
        "og:image"
      );

    return {
      title: title,
      thumbnail: thumbnail
    };

  } catch (error) {
    console.error(
      "Generic metadata error:",
      error
    );

    return null;
  }
}


/*
 * META TAG PARSER
 */

function extractMeta(
  html,
  property
) {
  const escaped =
    property.replace(
      /[.*+?^${}()|[\]\\]/g,
      "\\$&"
    );

  const patterns = [
    new RegExp(
      '<meta[^>]+property=["\']' +
      escaped +
      '["\'][^>]+content=["\']([^"\']+)["\']',
      "i"
    ),

    new RegExp(
      '<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']' +
      escaped +
      '["\']',
      "i"
    ),

    new RegExp(
      '<meta[^>]+name=["\']' +
      escaped +
      '["\'][^>]+content=["\']([^"\']+)["\']',
      "i"
    ),

    new RegExp(
      '<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']' +
      escaped +
      '["\']',
      "i"
    )
  ];

  for (
    const pattern of patterns
  ) {
    const match =
      html.match(pattern);

    if (match && match[1]) {
      return decodeHtml(
        match[1].trim()
      );
    }
  }

  return null;
}


/*
 * HTML TITLE
 */

function extractTitle(html) {
  const match =
    html.match(
      /<title[^>]*>([\s\S]*?)<\/title>/i
    );

  if (!match) {
    return null;
  }

  return decodeHtml(
    match[1].trim()
  );
}


/*
 * HTML ENTITY DECODER
 */

function decodeHtml(value) {
  return value
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#x27;/gi, "'")
    .replace(/&#x2F;/gi, "/");
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
