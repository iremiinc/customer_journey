from typing import Dict, Any, List, Optional
import numpy as np
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

# Proje İçi Modül Importları
from journey_store import journey_store, JourneyVersionExistsError, JourneyNotFoundError
from validator.graph_validator import JourneyValidator
from simulation.engine import JourneySimulationEngine
from cohort.generator import CohortGenerator, User
from ai.explainer_graph import run_journey_optimizer_agent

app = FastAPI(title="Enterprise Customer Journey Simulation & AI Platform API")


# --- REQUEST / RESPONSE MODELLERİ ---

class ImportJourneyRequest(BaseModel):
    journey: Dict[str, Any]
    overwrite: Optional[bool] = False

class ValidateRequest(BaseModel):
    journey: Dict[str, Any]

class SimulationRequest(BaseModel):
    journey: Dict[str, Any]
    cohort_size: int = Field(default=10000, ge=100)
    monte_carlo_runs: int = Field(default=10, ge=1, le=50)
    user_segments: Optional[Dict[str, float]] = {
        "VIP": 0.20,
        "New User": 0.40,
        "Inactive": 0.25,
        "Churn Risk": 0.15
    }

class CompareRequest(BaseModel):
    journey_a: Dict[str, Any]
    journey_b: Dict[str, Any]
    cohort_size: int = Field(default=10000, ge=100)
    monte_carlo_runs: int = Field(default=5, ge=1, le=50)
    user_segments: Optional[Dict[str, float]] = {
        "VIP": 0.20,
        "New User": 0.40,
        "Inactive": 0.25,
        "Churn Risk": 0.15
    }

class OptimizeRequest(BaseModel):
    journey: Dict[str, Any]
    metrics: Dict[str, Any]

# --- KANAL MALİYET VARSAYIMLARI ---
CHANNEL_COST = {"push": 0.01, "email": 0.02, "sms": 0.05}


# --- YARDIMCI SİMÜLASYON FONKSİYONLARI ---

def _channel_counts_from_visits(journey: Dict[str, Any], node_visits: Dict[str, int]) -> Dict[str, int]:
    counts = {"push": 0, "email": 0, "sms": 0}
    nodes = journey.get("nodes", {})
    for node_id, visits in node_visits.items():
        node_type = nodes.get(node_id, {}).get("type")
        if node_type in counts:
            counts[node_type] += visits
    return counts


def _run_single_simulation(journey: Dict[str, Any], cohort_size: int, user_segments: Dict[str, float]) -> Dict[str, Any]:
    cohort: List[User] = CohortGenerator.generate_cohort(cohort_size)
    engine = JourneySimulationEngine(journey_config=journey, cohort=cohort)
    return engine.run()


def execute_simulation_logic(journey: Dict[str, Any], cohort_size: int, monte_carlo_runs: int, user_segments: Dict[str, float]):
    mc_runs = []
    first_traces = []
    first_segment_stats = {}

    for run_id in range(1, monte_carlo_runs + 1):
        metrics = _run_single_simulation(journey, cohort_size, user_segments)
        channel_counts = _channel_counts_from_visits(journey, metrics["node_visits"])

        run_result = {
            "run_id": run_id,
            "converted_users": metrics["conversions"],
            "dropoff_users": metrics["dropoffs"],
            "consent_blocked": metrics["consent_blocked"],
            "freq_cap_blocked": metrics["freq_cap_blocked"],
            "push_sent": channel_counts["push"],
            "email_sent": channel_counts["email"],
            "sms_sent": channel_counts["sms"],
            "segment_counts": {seg: stats["total"] for seg, stats in metrics["segment_stats"].items()},
            "conversion_rate": round(metrics["conversions"] / cohort_size * 100, 2) if cohort_size else 0,
        }
        mc_runs.append(run_result)

        if run_id == 1:
            first_traces = metrics["event_logs"]
            first_segment_stats = metrics["segment_stats"]

    avg_converted = int(np.mean([r["converted_users"] for r in mc_runs]))
    avg_dropoff = int(np.mean([r["dropoff_users"] for r in mc_runs]))
    avg_consent = int(np.mean([r["consent_blocked"] for r in mc_runs]))
    avg_freq = int(np.mean([r["freq_cap_blocked"] for r in mc_runs]))

    avg_push = int(np.mean([r["push_sent"] for r in mc_runs]))
    avg_email = int(np.mean([r["email_sent"] for r in mc_runs]))
    avg_sms = int(np.mean([r["sms_sent"] for r in mc_runs]))

    total_cost = (avg_push * CHANNEL_COST["push"]) + (avg_email * CHANNEL_COST["email"]) + (avg_sms * CHANNEL_COST["sms"])
    cost_per_conversion = (total_cost / avg_converted) if avg_converted > 0 else 0.0

    summary_metrics = {
        "total_users": cohort_size,
        "converted_users": avg_converted,
        "dropoff_users": avg_dropoff,
        "consent_blocked": avg_consent,
        "freq_cap_blocked": avg_freq,
        "conversion_rate": round(avg_converted / cohort_size, 4) if cohort_size else 0,
        "channel_metrics": {
            "push_sent": avg_push,
            "email_sent": avg_email,
            "sms_sent": avg_sms
        },
        "financials": {
            "total_cost_usd": round(total_cost, 2),
            "cost_per_conversion_usd": round(cost_per_conversion, 2)
        }
    }

    segment_summary = []
    for seg_name, count in first_segment_stats.items():
        segment_summary.append({
            "Persona / Segment": seg_name,
            "Kullanıcı Sayısı": count["total"],
            "Cohort Payı": f"%{round((count['total'] / cohort_size) * 100, 1)}" if cohort_size else "%0"
        })

    return {
        "summary_metrics": summary_metrics,
        "monte_carlo_runs": mc_runs,
        "segment_analysis": segment_summary,
        "event_traces": first_traces
    }


# --- ENDPOINT'LER ---

@app.post("/journey/import", status_code=status.HTTP_201_CREATED)
async def import_journey(request: ImportJourneyRequest):
    """
    FR-01: Journey JSON tanımını import eder, validasyondan geçirir ve versiyonlar.
    - Blocker seviyesinde hata varsa kaydı reddeder.
    - Severity okumalarını nesne veya sözlük yapısına uygun biçimde güvenli yapar.
    """
    journey_data = request.journey
    version = journey_data.get("version", "1.0.0")

    # 1. Tümüyle Validate Et (FR-02, FR-03, FR-04, FR-05 & Şema)
    validator = JourneyValidator(journey_data)
    findings = validator.get_all_findings(journey_version=version)

    def get_severity(item):
        if isinstance(item, dict):
            return item.get("severity")
        return getattr(item, "severity", None)

    blockers = [f for f in findings if get_severity(f) == "Blocker"]

    if blockers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Journey doğrulama hatası! Blocker seviyesinde bulgular var.",
                "blockers": blockers,
                "all_findings": findings
            }
        )

    # 2. Version Store'a Kaydet
    try:
        result = journey_store.save_journey(journey_data, overwrite=request.overwrite)
        result["validation_summary"] = {
            "total_findings": len(findings),
            "warnings": len([f for f in findings if get_severity(f) == "Warning"])
        }
        return result
    except JourneyVersionExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.get("/journey/{journey_id}")
async def get_journey(journey_id: str, version: Optional[str] = None):
    """FR-01: Belirtilen id ve versiyondaki Journey'i getirir."""
    try:
        return journey_store.get_journey(journey_id, version)
    except JourneyNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.post("/journey/validate")
def validate_journey(req: ValidateRequest):
    try:
        validator = JourneyValidator(req.journey)
        validator.validate_schema()
        return {
            "status": "success",
            "data": validator.validate_graph_structure(),
            "findings": validator.get_all_findings()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/journey/simulate")
def run_simulation(req: SimulationRequest):
    try:
        sim_output = execute_simulation_logic(req.journey, req.cohort_size, req.monte_carlo_runs, req.user_segments)
        return {
            "status": "success",
            **sim_output
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/journey/compare")
def compare_journeys(req: CompareRequest):
    try:
        res_a = execute_simulation_logic(req.journey_a, req.cohort_size, req.monte_carlo_runs, req.user_segments)
        res_b = execute_simulation_logic(req.journey_b, req.cohort_size, req.monte_carlo_runs, req.user_segments)

        conv_a = res_a["summary_metrics"]["conversion_rate"]
        conv_b = res_b["summary_metrics"]["conversion_rate"]

        cost_a = res_a["summary_metrics"]["financials"]["total_cost_usd"]
        cost_b = res_b["summary_metrics"]["financials"]["total_cost_usd"]

        conv_diff = round((conv_b - conv_a) * 100, 2)
        cost_diff = round(cost_b - cost_a, 2)

        winner = "Journey B" if conv_b >= conv_a else "Journey A"

        return {
            "status": "success",
            "winner": winner,
            "metrics_diff": {
                "conversion_diff_percent": conv_diff,
                "cost_diff_dollar": cost_diff,
                "winner_conversion_rate": round(max(conv_a, conv_b) * 100, 2)
            },
            "comparison_table": [
                {
                    "Metric": "Conversion Rate (%)",
                    "Journey A": f"%{round(conv_a * 100, 2)}",
                    "Journey B": f"%{round(conv_b * 100, 2)}"
                },
                {
                    "Metric": "Total Cost ($)",
                    "Journey A": f"${cost_a}",
                    "Journey B": f"${cost_b}"
                },
                {
                    "Metric": "Converted Users",
                    "Journey A": res_a["summary_metrics"]["converted_users"],
                    "Journey B": res_b["summary_metrics"]["converted_users"]
                },
                {
                    "Metric": "Dropoff Users",
                    "Journey A": res_a["summary_metrics"]["dropoff_users"],
                    "Journey B": res_b["summary_metrics"]["dropoff_users"]
                }
            ],
            "journey_a_results": res_a["summary_metrics"],
            "journey_b_results": res_b["summary_metrics"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/journey/optimize")
def optimize_journey(req: OptimizeRequest):
    try:
        result = run_journey_optimizer_agent(req.journey, req.metrics)
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))