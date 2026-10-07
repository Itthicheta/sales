// Sales Web live data door — v1 (2026-10-06). Deployed on Supabase as "sales-data"; repo copy.
// Serves the baked payload in sales_web.app_cache (id=1) verbatim. Deploy via the
// Supabase MCP/dashboard, not from here. Mirrors dashboard_app/edge/dashboard-data.ts.
import { Client } from "https://deno.land/x/postgres@v0.19.3/mod.ts";

const TOKEN = "16f16024e7808bddbc57cf52d841a66239e8d555d78aeee4";
const ORIGIN = "https://mamapook-sales.pages.dev";
const CORS = {
  "Access-Control-Allow-Origin": ORIGIN,
  "Access-Control-Allow-Headers": "x-dash-token, content-type",
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "max-age=120",
};

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  if (req.headers.get("x-dash-token") !== TOKEN) {
    return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401, headers: CORS });
  }
  const client = new Client(Deno.env.get("SUPABASE_DB_URL")!);
  await client.connect();
  try {
    const res = await client.queryObject(
      "select payload from sales_web.app_cache where id = 1");
    const payload = (res.rows[0] as { payload?: string } | undefined)?.payload;
    if (!payload) {
      return new Response(JSON.stringify({ error: "cache empty" }), { status: 503, headers: CORS });
    }
    return new Response(payload, { headers: CORS });
  } finally {
    await client.end();
  }
});
