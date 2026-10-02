export default {
  async fetch(request) {
    const url = new URL(request.url);

    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type"
    };

    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders
      });
    }

    // Health check
    if (
      request.method === "GET" &&
      (url.pathname === "/" || url.pathname === "/health")
    ) {
      return jsonResponse(
        {
          success: true,
          service: "SAHN Download API",
          status: "online",
          version: "0.1.0"
        },
        corsHeaders
      );
    }

    // Analyze URL
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

      // Only HTTP / HTTPS
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
            "URL received successfully. Metadata provider is not connected yet."
        },
        corsHeaders
      );
    }

    // Unknown endpoint
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


function jsonResponse(
  data,
  corsHeaders,
  status = 200
) {
  return new Response(
    JSON.stringify(data, null, 2),
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