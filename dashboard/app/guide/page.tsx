import { getBotConfigs } from "@/lib/db";
import { GuideClient } from "@/components/GuideClient";

export const dynamic = "force-dynamic";

export default async function GuidePage() {
  const configs = await getBotConfigs();

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Strategy guide</h2>
      <p className="max-w-2xl text-sm text-[color:var(--text-secondary)]">
        How each strategy actually decides to buy or sell, in plain language and pictures — an
        illustrative example first, then the real thing for any strategy currently running.
      </p>
      <GuideClient configs={configs} />
    </div>
  );
}
