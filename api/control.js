function fail(response, status, message) {
  response.status(status).json({ error: message });
}

export default async function handler(request, response) {
  const apiBase = process.env.ARBX_CONTROL_API_URL;
  const proxyToken = process.env.ENGINE_PROXY_TOKEN;
  if (!apiBase || !proxyToken) return fail(response, 503, "Account and exchange controls are not configured.");
  if (!["GET", "POST", "DELETE"].includes(request.method)) {
    response.setHeader("Allow", "GET, POST, DELETE");
    return fail(response, 405, "Method not allowed");
  }
  if (request.method !== "GET" && request.headers.origin) {
    const forwardedHost = request.headers["x-forwarded-host"] || request.headers.host;
    const forwardedProto = request.headers["x-forwarded-proto"] || "https";
    try {
      const origin = new URL(request.headers.origin);
      if (origin.host !== forwardedHost || origin.protocol !== forwardedProto + ":") {
        return fail(response, 403, "Cross-origin control requests are not allowed");
      }
    } catch {
      return fail(response, 403, "Invalid request origin");
    }
  }

  const route = typeof request.query?.path === "string" ? request.query.path : "";
  if (!route.startsWith("/api/v1/") || route.includes("..") || route.includes("\\")) {
    return fail(response, 400, "Invalid control API path");
  }

  let target;
  try {
    const base = new URL(apiBase);
    if (base.protocol !== "https:" && base.hostname !== "localhost" && base.hostname !== "127.0.0.1") {
      return fail(response, 503, "The control API must use HTTPS");
    }
    target = new URL(route, base);
    if (target.origin !== base.origin) return fail(response, 400, "Invalid control API destination");
  } catch {
    return fail(response, 503, "The control API URL is invalid");
  }

  const upstreamHeaders = {
    "X-Engine-Token": proxyToken,
    Accept: "application/json",
  };
  if (request.headers.cookie) upstreamHeaders.Cookie = request.headers.cookie;
  if (request.method !== "GET" && request.body !== undefined) upstreamHeaders["Content-Type"] = "application/json";

  let upstream;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: upstreamHeaders,
      body: request.method === "GET" || request.body === undefined ? undefined : JSON.stringify(request.body),
      redirect: "error",
      signal: AbortSignal.timeout(55000),
    });
  } catch {
    return fail(response, 502, "Control service is unreachable");
  }

  response.status(upstream.status);
  response.setHeader("Cache-Control", "no-store, max-age=0");
  response.setHeader("X-Content-Type-Options", "nosniff");
  for (const header of ["content-type", "set-cookie", "www-authenticate"]) {
    const value = upstream.headers.get(header);
    if (value) response.setHeader(header, value);
  }
  const body = await upstream.text();
  return response.send(body);
}

