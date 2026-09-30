import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { zodValidator } from "@tanstack/zod-adapter";
import { CloudRain, Flame, Wind, ShieldCheck, ShieldQuestion } from "lucide-react";
import { APP_DATA_MODE, extremesQuery, leadTimesQuery, systemStatusQuery } from "@/features/data/queries";
import type { RegionCode } from "@/features/data/types";
import { SelectControl } from "@/features/shared/Controls";
import { SectionHeading } from "@/features/shared/SectionHeading";
import { TopBar } from "@/features/shared/AppShell";
import { routeHead } from "@/features/shared/RouteMeta";
const schema = z.object({
  region: z.enum(["KWG", "BOB", "IGP"]).catch("BOB"),
  lead: z.coerce.number().catch(72),
});
export const Route = createFileRoute("/extremes")({
  validateSearch: zodValidator(schema),
  head: () =>
    routeHead(
      "Extreme Weather Guidance",
      "View calibrated heavy-rain, heat, and high-wind exceedance probabilities from Synoptiq.",
    ),
  component: Page,
});
const icons = { precipitation: CloudRain, temperature: Flame, wind_speed: Wind };
const formatProbability = (value: number) =>
  value > 0 && value < 0.005 ? "<1%" : `${Math.round(value * 100)}%`;
const formatMeasurement = (value: number, unit: string) => {
  const displayUnit: Record<string, string> = {
    deg_C: "°C",
    "mm/24h": "mm / 24h",
    "km/h": "km/h",
  };
  return `${value.toFixed(1)} ${displayUnit[unit] ?? unit}`;
};
function Page() {
  const s = Route.useSearch(),
    nav = Route.useNavigate();
  const leadsQuery = useQuery(leadTimesQuery);
  const statusQuery = useQuery(systemStatusQuery);
  const systemStatus = statusQuery.data;
  const extremesQueryResult = useQuery({
    ...extremesQuery(s.region, s.lead),
    enabled: APP_DATA_MODE === "mock" || systemStatus?.ready === true,
  });
  const r = extremesQueryResult.data;
  const leads = leadsQuery.data ?? [24, 48, 72, 96, 120];
  const guidance = Array.isArray(r?.guidance) ? r.guidance : [];
  const liveUnavailable = APP_DATA_MODE === "live" && !systemStatus?.ready;
  const set = (p: Partial<typeof s>) => nav({ to: ".", search: (q) => ({ ...q, ...p }) });
  return (
    <>
      <TopBar mode={APP_DATA_MODE} />
      <div className="page">
        <SectionHeading
          eyebrow="Threshold exceedance intelligence"
          title="Extreme weather guidance"
          copy="Compare real forecast values with hazard thresholds; show calibrated probability only when the matching model artifact is available."
        />
        <div className="control-deck compact">
          <SelectControl
            label="Region"
            value={s.region}
            onChange={(v) => set({ region: v as RegionCode })}
            options={["KWG", "BOB", "IGP"].map((v) => ({ value: v, label: v }))}
          />
          <SelectControl
            label="Lead time"
            value={String(s.lead)}
            onChange={(v) => set({ lead: Number(v) })}
            options={leads.map((v) => ({ value: String(v), label: `+${v} hours` }))}
          />
        </div>
        {liveUnavailable ? (
          <div className="page-state" role="status">
            <h2>{statusQuery.isPending ? "CHECKING REAL INPUTS" : "REAL INPUTS UNAVAILABLE"}</h2>
            <p>
              {statusQuery.error instanceof Error
                ? statusQuery.error.message
                : "Extreme guidance is withheld until current real provider inputs pass validation."}
            </p>
            {statusQuery.isError && (
              <button type="button" onClick={() => void statusQuery.refetch()}>
                Retry status check
              </button>
            )}
          </div>
        ) : extremesQueryResult.isError ? (
          <div className="page-state" role="alert">
            <h2>GUIDANCE UNAVAILABLE</h2>
            <p>
              {extremesQueryResult.error instanceof Error
                ? extremesQueryResult.error.message
                : "The real extreme-guidance API could not be reached."}
            </p>
            <button type="button" onClick={() => void extremesQueryResult.refetch()}>
              Retry guidance
            </button>
          </div>
        ) : (
        <div className="threat-grid">
          {guidance.length === 0 ? (
            <div className="page-state">
              No extreme-weather guidance is available for this selection.
            </div>
          ) : (
            guidance.map((g) => {
              const Icon = icons[g.variable];
              const calibrated = g.calibrated
                && g.probability !== null
                && Number.isFinite(g.probability);
              const ratio = g.threshold > 0 ? g.forecast_value / g.threshold : null;
              const meterWidth = ratio === null ? 0 : Math.max(0, Math.min(100, ratio / 1.5 * 100));
              const thresholdState = g.threshold_exceeded
                ? "THRESHOLD EXCEEDED"
                : ratio !== null && ratio >= 0.9
                  ? "NEAR THRESHOLD"
                  : "WITHIN THRESHOLD";
              const level = calibrated
                ? g.probability! >= 0.65
                  ? "calibrated-high"
                  : g.probability! >= 0.35
                    ? "calibrated-medium"
                    : "calibrated-low"
                : g.threshold_exceeded
                  ? "exceeded"
                  : thresholdState === "NEAR THRESHOLD"
                    ? "near"
                    : "within";
              return (
                <section className={`threat panel ${level}`} key={g.variable}>
                  <div className="threat-icon">
                    <Icon />
                  </div>
                  <span className="kicker">{g.variable.replace("_", " ")}</span>
                  <h2 className="threat-value">
                    {calibrated
                      ? formatProbability(g.probability!)
                      : formatMeasurement(g.forecast_value, g.unit)}
                  </h2>
                  <strong className="threat-state">
                    {thresholdState}
                  </strong>
                  <div className="threat-metrics">
                    <span>Forecast: {formatMeasurement(g.forecast_value, g.unit)}</span>
                    <span>Threshold: {formatMeasurement(g.threshold, g.unit)}</span>
                    <span>Threshold exceedance: {g.threshold_exceeded ? "YES" : "NO"}</span>
                  </div>
                  <div className="threshold-meter" aria-label={`${ratio === null ? "No" : Math.round(ratio * 100)} percent of threshold`}>
                    <div className="threshold-meter-track">
                      <i style={{ width: `${meterWidth}%` }} />
                      <b aria-hidden="true" />
                    </div>
                    <div className="threshold-meter-labels">
                      <span>0</span>
                      <strong>{ratio === null ? "—" : `${Math.round(ratio * 100)}% OF LIMIT`}</strong>
                      <span>150%</span>
                    </div>
                  </div>
                  {calibrated && (
                    <div className="threat-gauge">
                      <i style={{ transform: `rotate(${g.probability! * 180 - 90}deg)` }} />
                    </div>
                  )}
                  <div className={`calibration ${calibrated ? "yes" : "no"}`}>
                    {calibrated ? <ShieldCheck /> : <ShieldQuestion />}
                    {calibrated ? (
                      <span>CALIBRATED · REAL MODEL</span>
                    ) : (
                      <span className="calibration-state">
                        <span>PROBABILITY WITHHELD</span>
                        <small>{g.probability_reason ?? "No matching real validation calibrator; showing deterministic threshold comparison."}</small>
                      </span>
                    )}
                  </div>
                </section>
              );
            })
          )}
        </div>
        )}
        {!liveUnavailable && guidance.length > 0 && (
          <div className="guidance-note">
            <ShieldQuestion />
            <div>
              <b>Probability is not certainty.</b>
              <p>
                Probabilities require observed threshold events in the frozen validation set. Where
                there is not enough real evidence, the page shows forecast distance to threshold and
                withholds probability rather than implying certainty.
              </p>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
