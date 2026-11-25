import { getSearchIndex } from "@/lib/server/docs";

export const dynamic = "force-static";

export async function GET() {
  return Response.json(await getSearchIndex(), {
    headers: { "Cache-Control": "public, max-age=3600" },
  });
}
