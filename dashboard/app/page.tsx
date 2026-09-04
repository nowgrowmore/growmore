import { getAllLivePositions, getAllPaperPositions } from "@/lib/db";
import { OverviewClient } from "@/components/OverviewClient";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const [paperPositions, livePositions] = await Promise.all([
    getAllPaperPositions(),
    getAllLivePositions(),
  ]);

  return <OverviewClient paperPositions={paperPositions} livePositions={livePositions} />;
}
