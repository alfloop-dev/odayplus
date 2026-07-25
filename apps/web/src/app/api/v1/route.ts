import type { NextRequest } from "next/server";
import { proxyApiRequest } from "../../../lib/auth/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function handler(request: NextRequest): Promise<Response> {
  return proxyApiRequest(request, "/api/v1");
}

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
export const HEAD = handler;
export const OPTIONS = handler;

