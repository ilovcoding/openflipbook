import { NextResponse } from "next/server";
import { listNodesBySession } from "@/lib/db";
import { hasBlobStorage, publicBlobUrl, readServerEnv } from "@/lib/env";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface Params {
  params: Promise<{ id: string }>;
}

export async function GET(req: Request, { params }: Params) {
  const { id } = await params;
  const env = readServerEnv();
  if (!env.MONGODB_URI || !env.MONGODB_DB || !hasBlobStorage(env)) {
    return NextResponse.json(
      { error: "persistence not configured" },
      { status: 503 }
    );
  }

  const url = new URL(req.url);
  const cursor = url.searchParams.get("cursor");
  const limitParam = url.searchParams.get("limit");
  const limit = limitParam ? Number(limitParam) : 200;

  const { rows, next_cursor } = await listNodesBySession(id, {
    cursor,
    limit: Number.isFinite(limit) ? limit : 200,
  });

  return NextResponse.json({
    session_id: id,
    next_cursor,
    nodes: rows.map((row) => ({
      id: row.id,
      parent_id: row.parent_id,
      session_id: row.session_id,
      query: row.query,
      page_title: row.page_title,
      image_url: publicBlobUrl(env, row.image_key),
      image_model: row.image_model,
      prompt_author_model: row.prompt_author_model,
      aspect_ratio: row.aspect_ratio,
      click_in_parent: row.click_in_parent,
      created_at: row.created_at,
    })),
  });
}
