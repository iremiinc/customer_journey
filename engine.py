import simpy
from typing import Dict, Any, List
from cohort.generator import User
from simulation.nodes import NODE_REGISTRY

class JourneySimulationEngine:
    def __init__(self, journey_config: Dict[str, Any], cohort: List[User]):
        self.config = journey_config
        self.cohort = cohort
        self.event_logs: List[Dict[str, Any]] = []
        self.metrics = {
            "total_users": len(cohort),
            "conversions": 0,
            "dropoffs": 0,
            "consent_blocked": 0,
            "freq_cap_blocked": 0,
            "segment_stats": {},
            "node_visits": {k: 0 for k in journey_config.get("nodes", {}).keys()}
        }

    def _user_runner(self, env: simpy.Environment, user: User):
        current_node_id = self.config.get("initial_node")
        
        while current_node_id:
            node = self.config["nodes"].get(current_node_id)
            if not node:
                break

            self.metrics["node_visits"][current_node_id] += 1
            node_type = node.get("type")

            # 2. Event Log Kaydı (SimPy zaman damgalı)
            if len(self.event_logs) < 100:  # İlk 100 log örneğini tut
                self.event_logs.append({
                    "timestamp": f"{env.now:.1f}h",
                    "user_id": user.user_id,
                    "segment": user.segment,
                    "node": current_node_id,
                    "type": node_type
                })

            if node_type == "exit":
                if "conversion" in current_node_id:
                    self.metrics["conversions"] += 1
                    self.metrics["segment_stats"][user.segment]["conversions"] += 1
                else:
                    self.metrics["dropoffs"] += 1
                break

            handler = NODE_REGISTRY.get(node_type)
            if not handler:
                break

            next_node_id = yield from handler.execute(env, user, node)

            # Engelleme Sayaçları
            # ESKİ (hatalı) yöntem: "blocked" / "freq" kelimesinin next_node_id
            # içinde geçip geçmediğine bakıyordu. Bu, sadece düğüm isimleri
            # "blocked_exit" / "freq_cap_exit" gibi belirli bir kalıba uyduğunda
            # çalışıyordu. Journey "consent_exit" gibi farklı bir isim kullanırsa
            # (örn. dashboard.py'deki varsayılan journey), consent_blocked hiç
            # sayılmıyor, bloklanan kullanıcılar sessizce dropoff'a düşüyordu.
            # YENİ yöntem: next_node_id'yi doğrudan bu düğümün kendi
            # on_consent_blocked / on_freq_cap_blocked hedefleriyle karşılaştırır
            # (nodes.py handler'larındaki varsayılanlarla birebir aynı fallback
            # değerleri kullanarak), isimlendirme kuralından bağımsız çalışır.
            if node_type in ("push", "email", "sms"):
                consent_blocked_target = node.get("on_consent_blocked", "blocked_exit")
                freq_cap_blocked_target = node.get("on_freq_cap_blocked", "freq_cap_exit")

                if next_node_id == consent_blocked_target:
                    self.metrics["consent_blocked"] += 1
                elif next_node_id == freq_cap_blocked_target:
                    self.metrics["freq_cap_blocked"] += 1

            current_node_id = next_node_id

    def run(self) -> Dict[str, Any]:
        env = simpy.Environment()

        # Segment istatistik tablosunu sıfırla
        for u in self.cohort:
            if u.segment not in self.metrics["segment_stats"]:
                self.metrics["segment_stats"][u.segment] = {"total": 0, "conversions": 0}
            self.metrics["segment_stats"][u.segment]["total"] += 1

        for user in self.cohort:
            env.process(self._user_runner(env, user))

        env.run()

        total = self.metrics["total_users"]
        self.metrics["conversion_rate"] = round((self.metrics["conversions"] / total) * 100, 2) if total > 0 else 0
        self.metrics["event_logs"] = self.event_logs
        return self.metrics
