import { NextResponse } from "next/server";
import { getNode } from "@/lib/db";
import { hasBlobStorage, publicBlobUrl, readServerEnv } from "@/lib/env";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface Params {
  params: Promise<{ id: string }>;
}

export async function GET(_req: Request, { params }: Params) {
  const { id } = await params;
  const env = readServerEnv();
  if (!env.MONGODB_URI || !env.MONGODB_DB || !hasBlobStorage(env)) {
    return NextResponse.json({ error: "persistence not configured" }, { status: 503 });
  }

  const row = await getNode(id);
  if (!row) return NextResponse.json({ error: "not found" }, { status: 404 });

  const imageUrl = publicBlobUrl(env, row.image_key);
  if (!imageUrl) {
    return NextResponse.json({ error: "blob storage not configured" }, { status: 503 });
  }
  return NextResponse.json({
    id: row.id,
    parent_id: row.parent_id,
    session_id: row.session_id,
    query: row.query,
    page_title: row.page_title,
    image_url: imageUrl,
    image_model: row.image_model,
    prompt_author_model: row.prompt_author_model,
    aspect_ratio: row.aspect_ratio,
    click_in_parent: row.click_in_parent,
    created_at: row.created_at,
  });
}
