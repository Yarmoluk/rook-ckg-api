from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

app = FastAPI(title="Rook → CKG Semantic Bridge", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

HEALTH_ONTOLOGY = {
    "CardiovascularLoad": {"pillar": "physical", "relates_to": ["RecoveryReadiness", "StressResponse", "SleepQuality"]},
    "PhysicalActivity": {"pillar": "physical", "relates_to": ["CardiovascularLoad", "StressResponse", "CalorieExpenditure"]},
    "StressResponse": {"pillar": "physical", "relates_to": ["SleepQuality", "RecoveryReadiness"]},
    "OxygenEfficiency": {"pillar": "physical", "relates_to": ["CardiovascularLoad", "RecoveryReadiness"]},
    "MovementVolume": {"pillar": "physical", "relates_to": ["PhysicalActivity", "CalorieExpenditure"]},
    "CalorieExpenditure": {"pillar": "physical", "relates_to": ["NutritionBalance", "BodyComposition"]},
    "SleepArchitecture": {"pillar": "sleep", "relates_to": ["RecoveryReadiness", "CardiovascularLoad", "StressResponse"]},
    "SleepQuality": {"pillar": "sleep", "relates_to": ["RecoveryReadiness", "StressResponse"]},
    "NocturnalPhysiology": {"pillar": "sleep", "relates_to": ["CardiovascularLoad", "OxygenEfficiency", "RecoveryReadiness"]},
    "RecoveryReadiness": {"pillar": "sleep", "relates_to": ["PhysicalActivity", "SleepQuality", "CardiovascularLoad"]},
    "BodyComposition": {"pillar": "body", "relates_to": ["NutritionBalance", "CardiovascularLoad"]},
    "MetabolicMarkers": {"pillar": "body", "relates_to": ["NutritionBalance", "PhysicalActivity"]},
    "CardiovascularMarkers": {"pillar": "body", "relates_to": ["CardiovascularLoad", "StressResponse"]},
    "HydrationStatus": {"pillar": "body", "relates_to": ["PhysicalActivity", "BodyComposition"]},
    "NutritionBalance": {"pillar": "body", "relates_to": ["CalorieExpenditure", "BodyComposition", "MetabolicMarkers"]},
}

@dataclass
class CKGNode:
    node_type: str
    user_id: str
    date: str
    source: str
    document_version: int
    properties: Dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

class CKGStore:
    def __init__(self):
        self._graph: Dict = {}
    def upsert(self, node: CKGNode) -> bool:
        ug = self._graph.setdefault(node.user_id, {})
        dg = ug.setdefault(node.date, {})
        existing = dg.get(node.node_type)
        if existing and existing.document_version >= node.document_version:
            return False
        dg[node.node_type] = node
        return True
    def get_bundle(self, user_id: str, date: str) -> Dict:
        nodes_raw = self._graph.get(user_id, {}).get(date, {})
        nodes, edges, seen = [], [], set()
        for nt, n in nodes_raw.items():
            nodes.append({"id": f"{user_id}:{date}:{nt}", "type": nt, "pillar": HEALTH_ONTOLOGY.get(nt, {}).get("pillar"), "properties": n.properties, "source": n.source})
            for rel in HEALTH_ONTOLOGY.get(nt, {}).get("relates_to", []):
                key = tuple(sorted([nt, rel]))
                if key not in seen and rel in nodes_raw:
                    edges.append({"from": nt, "to": rel, "relation": "CORRELATES_WITH"})
                    seen.add(key)
        summary = "Health CKG context: " + " | ".join(f"{n['type']}({', '.join(f'{k}={v}' for k,v in n['properties'].items() if v is not None)})" for n in nodes)
        return {"user_id": user_id, "date": date, "node_count": len(nodes), "edge_count": len(edges), "nodes": nodes, "edges": edges, "ai_summary": summary}
    def stats(self):
        return {"total_users": len(self._graph), "total_nodes": sum(len(n) for u in self._graph.values() for n in u.values())}

store = CKGStore()

def extract_date(meta):
    try:
        return meta.get("datetime_string", "")[:10]
    except:
        return datetime.utcnow().strftime("%Y-%m-%d")

def map_and_store(payload: Dict) -> Dict:
    ds = payload.get("data_structure", "")
    uid = payload.get("user_id", "unknown")
    dv = payload.get("document_version", 1)
    written = 0
    try:
        if ds == "physical_summary":
            s = payload["physical_health"]["summary"]["physical_summary"]
            date = extract_date(s.get("metadata", {}))
            src = ", ".join(s.get("metadata", {}).get("sources_of_data_array", ["unknown"]))
            mappings = [
                ("CardiovascularLoad", s.get("heart_rate", {}), {"hr_avg": "hr_avg_bpm_int", "hr_max": "hr_maximum_bpm_int", "hr_resting": "hr_resting_bpm_int", "hrv_rmssd": "hrv_avg_rmssd_float", "hrv_sdnn": "hrv_avg_sdnn_float"}),
                ("PhysicalActivity", s.get("activity", {}), {"active_seconds": "active_seconds_int", "moderate_intensity": "moderate_intensity_seconds_int", "vigorous_intensity": "vigorous_intensity_seconds_int"}),
                ("StressResponse", s.get("stress", {}), {"avg_stress": "stress_avg_level_int", "stress_max": "stress_maximum_level_int", "high_stress_duration": "high_stress_duration_seconds_int"}),
                ("OxygenEfficiency", s.get("oxygenation", {}), {"spo2_avg": "saturation_avg_percentage_int", "vo2max": "vo2max_mL_per_min_per_kg_int"}),
                ("MovementVolume", s.get("distance", {}), {"steps": "steps_int", "distance_meters": "traveled_distance_meters_float"}),
                ("CalorieExpenditure", s.get("calories", {}), {"calories_burned": "calories_expenditure_kcal_float"}),
            ]
            for node_type, data, field_map in mappings:
                if data:
                    props = {k: data.get(v) for k, v in field_map.items()}
                    if store.upsert(CKGNode(node_type, uid, date, src, dv, props)): written += 1
        elif ds == "sleep_summary":
            s = payload["sleep_health"]["summary"]["sleep_summary"]
            date = extract_date(s.get("metadata", {}))
            src = ", ".join(s.get("metadata", {}).get("sources_of_data_array", ["unknown"]))
            dur = s.get("duration", {})
            scores = s.get("scores", {})
            hr = s.get("heart_rate", {})
            br = s.get("breathing", {})
            if dur and store.upsert(CKGNode("SleepArchitecture", uid, date, src, dv, {"total_sleep_seconds": dur.get("sleep_duration_seconds_int"), "rem_sleep": dur.get("rem_sleep_duration_seconds_int"), "deep_sleep": dur.get("deep_sleep_duration_seconds_int"), "light_sleep": dur.get("light_sleep_duration_seconds_int")})): written += 1
            if scores and store.upsert(CKGNode("SleepQuality", uid, date, src, dv, {"quality_score": scores.get("sleep_quality_rating_1_5_score_int"), "efficiency_score": scores.get("sleep_efficiency_1_100_score_int")})): written += 1
            if hr and store.upsert(CKGNode("NocturnalPhysiology", uid, date, src, dv, {"hr_avg": hr.get("hr_avg_bpm_int"), "hrv_rmssd": hr.get("hrv_avg_rmssd_float"), "spo2_avg": br.get("saturation_avg_percentage_int") if br else None})): written += 1
            if scores and dur:
                q = scores.get("sleep_quality_rating_1_5_score_int", 0) or 0
                e = scores.get("sleep_efficiency_1_100_score_int", 0) or 0
                h = (dur.get("sleep_duration_seconds_int", 0) or 0) / 3600
                score = round((q/5*0.4) + (e/100*0.4) + (min(h/8,1.0)*0.2), 3)
                if store.upsert(CKGNode("RecoveryReadiness", uid, date, src, dv, {"composite_recovery_score": score})): written += 1
        elif ds == "body_summary":
            s = payload["body_health"]["summary"]["body_summary"]
            date = extract_date(s.get("metadata", {}))
            src = ", ".join(s.get("metadata", {}).get("sources_of_data_array", ["unknown"]))
            bm = s.get("body_metrics", {})
            if bm and store.upsert(CKGNode("BodyComposition", uid, date, src, dv, {"weight_kg": bm.get("weight_kg_float"), "bmi": bm.get("bmi_float"), "muscle_pct": bm.get("muscle_composition_percentage_int")})): written += 1
            bg = s.get("blood_glucose", {})
            if bg and store.upsert(CKGNode("MetabolicMarkers", uid, date, src, dv, {"blood_glucose_avg": bg.get("blood_glucose_avg_mg_per_dL_int")})): written += 1
            bp = s.get("blood_pressure", {})
            if bp:
                avg = bp.get("blood_pressure_avg_object", {})
                if avg and store.upsert(CKGNode("CardiovascularMarkers", uid, date, src, dv, {"systolic": avg.get("systolic_mmHg_int"), "diastolic": avg.get("diastolic_mmHg_int")})): written += 1
            hyd = s.get("hydration", {})
            if hyd and store.upsert(CKGNode("HydrationStatus", uid, date, src, dv, {"water_intake_ml": hyd.get("water_total_consumption_mL_int")})): written += 1
            nut = s.get("nutrition", {})
            if nut and store.upsert(CKGNode("NutritionBalance", uid, date, src, dv, {"calories_intake": nut.get("calories_intake_kcal_float"), "protein_g": nut.get("protein_intake_g_float")})): written += 1
    except Exception as e:
        return {"status": "error", "error": str(e), "nodes_written": written}
    return {"status": "processed", "data_structure": ds, "user_id": uid, "nodes_written": written}

@app.get("/")
def root():
    return {"service": "Rook → CKG Semantic Bridge", "by": "Graphify.md", "docs": "/docs", "demo": "/health/sandbox-demo"}

@app.post("/webhook/rook")
async def webhook(request: Request):
    payload = await request.json()
    return map_and_store(payload)

@app.get("/context/{user_id}/ai-bundle")
def ai_bundle(user_id: str, date: str):
    bundle = store.get_bundle(user_id, date)
    if not bundle["nodes"]:
        return {"error": f"No data for {user_id} on {date}"}
    return bundle

@app.get("/health")
def health():
    return {"status": "ok", "stats": store.stats()}

@app.post("/health/sandbox-demo")
def sandbox_demo():
    import json
    payloads = [
        {"client_uuid":"demoClientUUID","user_id":"demoUserId","version":2,"document_version":1,"data_structure":"physical_summary","physical_health":{"summary":{"physical_summary":{"metadata":{"datetime_string":"2022-10-28T10:00:00.000000Z","sources_of_data_array":["Garmin"],"user_id_string":"demoUserId"},"activity":{"active_seconds_int":4200,"low_intensity_seconds_int":1800,"moderate_intensity_seconds_int":1800,"vigorous_intensity_seconds_int":600,"inactivity_seconds_int":50000},"heart_rate":{"hr_avg_bpm_int":68,"hr_maximum_bpm_int":155,"hr_minimum_bpm_int":48,"hr_resting_bpm_int":52,"hrv_avg_rmssd_float":42.5,"hrv_avg_sdnn_float":55.2},"stress":{"stress_avg_level_int":28,"stress_maximum_level_int":72,"high_stress_duration_seconds_int":3600,"low_stress_duration_seconds_int":14400},"oxygenation":{"saturation_avg_percentage_int":97,"vo2max_mL_per_min_per_kg_int":48},"distance":{"steps_int":8750,"traveled_distance_meters_float":6800.0,"floors_climbed_float":12.0},"calories":{"calories_expenditure_kcal_float":2350.0,"calories_net_active_kcal_float":450.0}}}}},
        {"client_uuid":"demoClientUUID","user_id":"demoUserId","version":2,"document_version":1,"data_structure":"sleep_summary","sleep_health":{"summary":{"sleep_summary":{"metadata":{"datetime_string":"2022-10-28T07:30:00.000000Z","sources_of_data_array":["Oura"],"user_id_string":"demoUserId"},"duration":{"sleep_duration_seconds_int":25200,"light_sleep_duration_seconds_int":9000,"rem_sleep_duration_seconds_int":7200,"deep_sleep_duration_seconds_int":5400,"time_awake_during_sleep_seconds_int":1800,"time_to_fall_asleep_seconds_int":600},"scores":{"sleep_quality_rating_1_5_score_int":4,"sleep_efficiency_1_100_score_int":87,"sleep_continuity_1_5_score_int":3},"heart_rate":{"hr_avg_bpm_int":54,"hrv_avg_rmssd_float":58.3},"breathing":{"breaths_avg_per_min_int":14,"saturation_avg_percentage_int":96,"snoring_duration_total_seconds_int":240}}}}},
        {"client_uuid":"demoClientUUID","user_id":"demoUserId","version":2,"document_version":1,"data_structure":"body_summary","body_health":{"summary":{"body_summary":{"metadata":{"datetime_string":"2022-10-28T08:00:00.000000Z","sources_of_data_array":["Withings"],"user_id_string":"demoUserId"},"body_metrics":{"weight_kg_float":82.4,"height_cm_int":178,"bmi_float":26.0,"muscle_composition_percentage_int":42,"water_composition_percentage_int":58},"blood_glucose":{"blood_glucose_avg_mg_per_dL_int":94},"blood_pressure":{"blood_pressure_avg_object":{"systolic_mmHg_int":118,"diastolic_mmHg_int":76}},"hydration":{"water_total_consumption_mL_int":2200},"nutrition":{"calories_intake_kcal_float":2100.0,"protein_intake_g_float":145.0,"carbohydrates_intake_g_float":210.0,"fat_intake_g_float":72.0}}}}}
    ]
    results = [map_and_store(p) for p in payloads]
    return {"processing": results, "ckg_output": store.get_bundle("demoUserId", "2022-10-28")}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
