import { ClientOnly } from "@tanstack/react-router";
import { useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { lazy, Suspense, useCallback } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Database,
  Eye,
  Gauge,
  RadioTower,
  ShieldAlert,
} from "lucide-react";
import {
  APP_DATA_MODE,
  blendQuery,
  leadTimesQuery,
  regionsQuery,
  systemStatusQuery,
  variablesQuery,
} from "@/features/data/queries";
import type { RegionCode, VariableName } from "@/features/data/types";
import { SelectControl } from "@/features/shared/Controls";
import { DataStatus } from "@/features/shared/DataStatus";
import { TopBar } from "@/features/shared/AppShell";
import { Meter, Metric } from "@/features/shared/Metric";
import "./provider-status.css";
const RegionMap = lazy(() => import("./RegionMap"));
const SOURCE_MODELS = ["GFS", "IFS", "AIFS"] as const;
function pct(n: number) {
  return `${Math.round(n * 100)}%`;
}
function formatProviderTimestamp(value: string | null) {
  if (!value) return "No recorded run";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Invalid timestamp"
    : `${date.toISOString().replace("T", " ").replace(".000Z", " UTC")}`;
}
export function ForecastView({
  region,
  variable,
  lead,
  onRegion,
  onVariable,
  onLead,
}: {
  region: RegionCode;
  variable: VariableName;
  lead: number;
  onRegion: (v: RegionCode) => void;
  onVariable: (v: VariableName) => void;
  onLead: (v: number) => void;
}) {
  const regions = useSuspenseQuery(regionsQuery);
  const variables = useSuspenseQuery(variablesQuery);
  const leads = useSuspenseQuery(leadTimesQuery);
  const systemStatus = useSuspenseQuery(systemStatusQuery).data;
  const mode = APP_DATA_MODE;
  const realReady = mode === "live" && systemStatus.mode === "real" && systemStatus.ready;
  const liveSourceCount = Object.values(systemStatus.providers).filter(
    (provider) => provider.status === "LIVE" && provider.is_real,
  ).length;
  const { data: d } = useQuery({
    ...blendQuery(region, variable, lead),
    enabled: mode === "mock" || systemStatus.mode === "real",
  });
  const mapSelect = useCallback((v: RegionCode) => onRegion(v), [onRegion]);
  return (
    <>
      <TopBar mode={mode} />
      <div className="control-deck">
        <SelectControl
          label="Pilot region"
          value={region}
          onChange={(v) => onRegion(v as RegionCode)}
          options={regions.data.map((r) => ({ value: r.code, label: `${r.code} · ${r.name}` }))}
        />
        <SelectControl
          label="Forecast variable"
          value={variable}
          onChange={(v) => onVariable(v as VariableName)}
          options={variables.data.map((v) => ({
            value: v.variable,
            label: v.variable.replace("_", " "),
          }))}
        />
        <SelectControl
          label="Lead time"
          value={String(lead)}
          onChange={(v) => onLead(Number(v))}
          options={leads.data.map((l) => ({ value: String(l), label: `+${l} hours` }))}
        />
        {mode === "mock" ? (
          <DataStatus mode={mode} />
        ) : (
          <span className={`operational-status ${realReady ? "live" : "unavailable"}`}>
            {realReady
              ? liveSourceCount === 3
                ? "REAL LIVE · 3/3 SOURCES"
                : `DEGRADED REAL · ${liveSourceCount}/3 SOURCES`
              : "DEGRADED REAL · HISTORICAL REAL_12M"}
          </span>
        )}
      </div>
      <section className="provider-status" aria-label="Provider validation status">
        <div className="provider-status-head">
          <div>
            <span className="kicker">
              {mode === "mock" ? "DEMO SOURCE MODE" : "LIVE SOURCE VALIDATION"}
            </span>
            <h2>
              {mode === "mock"
                ? "DEMO INPUTS"
                : realReady
                  ? "REAL LIVE"
                  : "DEGRADED REAL · HISTORICAL REAL_12M"}
            </h2>
          </div>
          <span>
            {mode === "mock"
              ? "SYNTHETIC DATA"
              : realReady
                ? liveSourceCount === 3
                  ? "REAL LIVE · 3/3 SOURCES"
                  : `DEGRADED REAL · ${liveSourceCount}/3 SOURCES`
                : "NO SYNTHETIC FALLBACK"}
          </span>
        </div>
        <p>
          {mode === "mock"
            ? "Synthetic demonstration inputs are active because DEMO mode was explicitly selected."
            : realReady
              ? `The forecast uses ${liveSourceCount} fresh real sources; unavailable sources are excluded from the blend.`
                : "Live provider cycles are stale or unavailable; this view is using validated historical REAL_12M output with no synthetic fallback."}
        </p>
        <div className="provider-status-grid">
          {SOURCE_MODELS.map((model) => {
            const provider = systemStatus.providers[model];
            return (
              <article
                className={`provider-status-item ${provider.status.toLowerCase()}`}
                key={model}
              >
                <div>
                  <strong>{model}</strong>
                  <span>{mode === "mock" ? "DEMO" : provider.status}</span>
                </div>
                <time dateTime={provider.run_time ?? undefined}>
                  Run {formatProviderTimestamp(provider.run_time)}
                </time>
                <time dateTime={provider.latest_available_time ?? undefined}>
                  Latest {formatProviderTimestamp(provider.latest_available_time)}
                </time>
                <p>
                  {mode === "mock"
                    ? "Provider checks are disabled in DEMO mode."
                    : provider.validation_reason}
                </p>
                <small>
                  {provider.validation_result} · {provider.age_minutes ?? "—"} MIN · {provider.is_real ? "REAL SOURCE" : "NOT REAL"}
                </small>
              </article>
            );
          })}
        </div>
      </section>
      {!realReady && mode === "live" ? (
        <section className="page-state" role="status">
          <h2>REAL FORECAST UNAVAILABLE</h2>
          <p>
            Current provider inputs have not passed validation. Historical prototype values are
            withheld from the operational forecast.
          </p>
        </section>
      ) : d ? (
      <>
      <div className="forecast-grid">
        <section className="map-panel panel">
          <div className="panel-head">
            <div>
              <span className="kicker">PILOT ZONE</span>
              <h2>{d.region_name}</h2>
            </div>
            <span className="coordinate">
              {regions.data.find((r) => r.code === region)?.lat}°N ·{" "}
              {regions.data.find((r) => r.code === region)?.lon}°E
            </span>
          </div>
          <ClientOnly fallback={<div className="map-skeleton" />}>
            <Suspense fallback={<div className="map-skeleton" />}>
              <RegionMap regions={regions.data} selected={region} onSelect={mapSelect} />
            </Suspense>
          </ClientOnly>
          <div className="map-legend">
            {regions.data.map((r) => (
              <button
                key={r.code}
                onClick={() => onRegion(r.code)}
                className={r.code === region ? "active" : ""}
              >
                <i />
                {r.code}
              </button>
            ))}
          </div>
        </section>
        <section className={`forecast-readout panel ${d.abstain ? "abstain" : ""}`}>
          <div className="panel-head">
            <div>
              <span className="kicker">BLENDED OUTPUT</span>
              <h2>
                {d.abstain
                  ? "Human review advised"
                  : mode === "mock"
                    ? "Demo forecast"
                    : realReady
                      ? "Forecast cleared"
                      : "Historical prototype output"}
              </h2>
            </div>
            {d.abstain ? <ShieldAlert /> : <CheckCircle2 />}
          </div>
          <div className="final-value">
            <strong>{d.final_value.toFixed(1)}</strong>
            <span>{d.unit}</span>
          </div>
          <div className="forecast-pipeline">
            <div>
              <small>RAW BLEND</small>
              <b>{d.raw_blend_value.toFixed(2)}</b>
            </div>
            <ArrowRight />
            <div>
              <small>BIAS CORRECTED</small>
              <b>{d.bias_corrected_value.toFixed(2)}</b>
            </div>
            <ArrowRight />
            <div>
              <small>FINAL</small>
              <b>{d.final_value.toFixed(2)}</b>
            </div>
          </div>
          <div className="decision-strip">
            <span>
              <RadioTower />+{d.lead_hours}H
            </span>
            <span>
              <Gauge />
              {d.regime.replaceAll("_", " ")}
            </span>
            <span>
              <AlertTriangle />
              {pct(d.bust_probability)} bust risk
            </span>
          </div>
          {d.abstain && (
            <div className="abstain-note">
              Low confidence · preserve the numeric output for context, but escalate operational use
              to a forecaster.
            </div>
          )}
        </section>
        <section className="source-panel panel">
          <div className="panel-head">
            <div>
              <span className="kicker">SOURCE ATTRIBUTION</span>
              <h2>Models in the blend</h2>
            </div>
            <span className="micro-copy">Weight · value · historical skill</span>
          </div>
          <div className="source-list">
            {d.sources.map((s) => (
              <div
                className={`source-row ${(s.weight ?? 0) === 0 ? "excluded" : ""}`}
                key={s.model}
              >
                <div className="source-name">
                  <i className={`signal-${s.model.toLowerCase()}`} />
                  <b>{s.model}</b>
                  {s.weight === 0 && <span>EXCLUDED</span>}
                </div>
                <div className="source-bar">
                  <i style={{ transform: `scaleX(${s.weight ?? 0})` }} />
                </div>
                <strong>{pct(s.weight ?? 0)}</strong>
                <span>
                  {s.forecast_value.toFixed(1)} {d.unit}
                </span>
                <span>skill {(s.historical_skill ?? 0).toFixed(3)}</span>
              </div>
            ))}
          </div>
        </section>
        <section className="trust-panel panel">
          <div className="panel-head">
            <div>
              <span className="kicker">TRUST COMPOSITION</span>
              <h2>Five signals, one decision</h2>
            </div>
            <div className="trust-orbit">
              <strong>{pct(d.trust.trust_score)}</strong>
              <span>TRUST</span>
            </div>
          </div>
          <div className="trust-grid">
            {Object.entries(d.trust)
              .filter(([k]) => k !== "trust_score")
              .map(([k, v]) => (
                <Meter
                  key={k}
                  value={v * 100}
                  tone={v < 0.35 ? "warning" : "primary"}
                  label={k.replace("_component", "").replaceAll("_", " ")}
                />
              ))}
          </div>
        </section>
        <section className="regime-panel panel">
          <div className="panel-head">
            <div>
              <span className="kicker">REGIME CLASSIFIER</span>
              <h2>{d.regime.replaceAll("_", " ")}</h2>
            </div>
            <Eye />
          </div>
          <div className="prob-bars">
            {Object.entries(d.regime_probs)
              .sort((a, b) => b[1] - a[1])
              .map(([name, val]) => (
                <Meter
                  key={name}
                  value={val * 100}
                  tone={name === d.regime ? "primary" : "honest"}
                  label={name.replaceAll("_", " ")}
                />
              ))}
          </div>
        </section>
        <section className="explain-panel panel">
          <div className="panel-head">
            <div>
              <span className="kicker">EXPLANATION BACKEND</span>
              <h2>Why this blend?</h2>
            </div>
            <span className="micro-copy">Signed contribution</span>
          </div>
          <div className="explanation-list">
            {d.explanation.map((x, index) => (
              <div key={`${x.feature}-${index}`}>
                <div className="explain-row">
                  <b>{x.feature.replaceAll("_", " ")}</b>
                  <span className={x.contribution >= 0 ? "positive" : "negative"}>
                    {x.contribution >= 0 ? "+" : ""}
                    {x.contribution.toFixed(3)}
                  </span>
                </div>
                <div className="contribution-track">
                  <i
                    className={x.contribution >= 0 ? "positive" : "negative"}
                    style={{ width: `${Math.min(50, Math.abs(x.contribution) * 220)}%` }}
                  />
                </div>
                <p>{x.detail}</p>
              </div>
            ))}
          </div>
        </section>
      </div>
      <footer className="provenance">
        <Database />
        <span>PROVENANCE</span>
        {Object.entries(d.provenance).map(([k, v]) => (
          <span key={k}>
            <b>{k}</b> {v}
          </span>
        ))}
      </footer>
        </>
      ) : (
        <div className="page-state" role="status">Loading forecast response.</div>
      )}
    </>
  );
}
