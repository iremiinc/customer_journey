from typing import Dict, Any, List
import networkx as nx
from jsonschema import validate, ValidationError, Draft7Validator

from .findings import Finding

# JSON Yapısını Doğrulayan Schema
JOURNEY_SCHEMA = {
    "type": "object",
    "properties": {
        "journey_id": {"type": "string"},
        "initial_node": {"type": "string"},
        "timezone": {"type": "string"},
        "quiet_hours": {
            "type": "object",
            "properties": {
                "start_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                "end_hour": {"type": "integer", "minimum": 0, "maximum": 23}
            }
        },
        "nodes": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["wait", "push", "email", "sms", "condition", "exit", "send_email", "send_sms", "push_notification"]
                    },
                    "duration_hours": {"type": "number", "minimum": 0},
                    "scheduled_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                    "condition_attribute": {"type": "string"},
                    "threshold": {"type": "number"},
                    "on_true_target": {"type": "string"},
                    "on_false_target": {"type": "string"},
                    "on_consent_blocked": {"type": "string"},
                    "on_freq_cap_blocked": {"type": "string"},
                    "transitions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "target": {"type": "string"},
                                "weight": {"type": "number", "minimum": 0, "maximum": 1}
                            },
                            "required": ["target"]
                        }
                    }
                },
                "required": ["type"]
            }
        }
    },
    "required": ["journey_id", "initial_node", "nodes"]
}


class JourneyValidator:
    def __init__(self, journey_data: Dict[str, Any]):
        self.data = journey_data
        self.graph = nx.DiGraph()

    def validate_schema(self) -> bool:
        """JSON alanlarını ve veri tiplerini kontrol eder (Exception fırlatır)."""
        try:
            validate(instance=self.data, schema=JOURNEY_SCHEMA)
            return True
        except ValidationError as e:
            raise ValueError(f"JSON Schema Hasi: {e.message}")

    def build_graph(self):
        """JSON verisinden NetworkX Directed Graph (DiGraph) inşa eder."""
        self.graph.clear()
        nodes = self.data.get("nodes", {})

        for node_id, details in nodes.items():
            self.graph.add_node(node_id, node_type=details.get("type"))
            
            # Normal geçişler (transitions)
            for edge in details.get("transitions", []):
                self.graph.add_edge(node_id, edge["target"], edge_type="transition")
                
            # Condition Node özel hedefleri
            if details.get("on_true_target"):
                self.graph.add_edge(node_id, details["on_true_target"], edge_type="condition_true")
            if details.get("on_false_target"):
                self.graph.add_edge(node_id, details["on_false_target"], edge_type="condition_false")
                
            # Consent ve Frequency Cap engelleri için hedefler
            if details.get("on_consent_blocked"):
                self.graph.add_edge(node_id, details["on_consent_blocked"], edge_type="consent_blocked")
            if details.get("on_freq_cap_blocked"):
                self.graph.add_edge(node_id, details["on_freq_cap_blocked"], edge_type="freq_cap_blocked")

    def validate_graph_structure(self) -> Dict[str, Any]:
        """NetworkX kullanarak grafiğin topolojik geçerliliğini denetler."""
        self.build_graph()
        initial = self.data.get("initial_node")
        nodes_set = set(self.graph.nodes)

        if initial not in nodes_set:
            raise ValueError(f"Başlangıç düğümü '{initial}' nodes listesinde bulunamadı!")

        reachable = nx.descendants(self.graph, initial) | {initial}
        unreachable = list(nodes_set - reachable)

        orphan_nodes = [
            node for node in nodes_set 
            if self.graph.in_degree(node) == 0 and self.graph.out_degree(node) == 0 and node != initial
        ]

        exit_nodes = [node for node, attr in self.graph.nodes(data=True) if attr.get("node_type") == "exit"]
        if not exit_nodes:
            raise ValueError("Grafikte en az bir 'exit' tipli düğüm olmalıdır!")

        has_cycles = not nx.is_directed_acyclic_graph(self.graph)
        cycles = list(nx.simple_cycles(self.graph)) if has_cycles else []

        return {
            "valid": True,
            "has_cycles": has_cycles,
            "cycles": cycles,
            "unreachable_nodes": unreachable,
            "orphan_nodes": orphan_nodes,
            "exit_nodes": exit_nodes
        }

    # ------------------------------------------------------------------
    # FR-15: Structured Validation Findings (Code, Severity, Recommendation)
    # ------------------------------------------------------------------

    def validate_schema_findings(self, journey_version: str = "unknown") -> List[Finding]:
        """FR-02/FR-15: Schema hatalarını Finding listesi olarak toplar."""
        findings: List[Finding] = []
        validator = Draft7Validator(JOURNEY_SCHEMA)
        for err in validator.iter_errors(self.data):
            path = "/".join(str(p) for p in err.path) or "(root)"
            findings.append(Finding(
                code="SCHEMA_VALIDATION_ERROR",
                category="schema",
                severity="Blocker",
                message=err.message,
                journey_version=journey_version,
                field=path,
                recommendation="Belirtilen alanı şemayla uyumlu hale getirin.",
            ))
        return findings

    def validate_graph_findings(self, journey_version: str = "unknown") -> List[Finding]:
        """FR-03/FR-15: Grafik yapısı hatalarını toplar."""
        findings: List[Finding] = []
        self.build_graph()
        nodes_data = self.data.get("nodes", {})
        node_ids = set(self.graph.nodes)
        initial = self.data.get("initial_node")

        if initial not in node_ids:
            findings.append(Finding(
                code="MISSING_ENTRY_NODE", category="graph", severity="Blocker",
                message=f"Başlangıç düğümü '{initial}' nodes içinde tanımlı değil.",
                journey_version=journey_version,
                recommendation="initial_node alanını 'nodes' içinde tanımlı bir düğüme ayarlayın.",
            ))
            return findings

        for node_id, details in nodes_data.items():
            for edge in details.get("transitions", []):
                target = edge.get("target")
                if target and target not in node_ids:
                    findings.append(Finding(
                        code="DANGLING_EDGE", category="graph", severity="Blocker", node_id=node_id,
                        field="transitions", message=f"'{node_id}' -> '{target}' hedefi tanımlı değil.",
                        journey_version=journey_version,
                        recommendation="Hedef düğümü tanımlayın ya da bu transition'ı kaldırın.",
                    ))
            for f_name in ("on_consent_blocked", "on_freq_cap_blocked", "on_true_target", "on_false_target"):
                target = details.get(f_name)
                if target and target not in node_ids:
                    findings.append(Finding(
                        code="DANGLING_EDGE", category="graph", severity="Blocker", node_id=node_id,
                        field=f_name, message=f"'{node_id}'.{f_name} -> '{target}' tanımlı değil.",
                        journey_version=journey_version,
                        recommendation="Hedef düğümü tanımlayın ya da referansı kaldırın.",
                    ))

        reachable = nx.descendants(self.graph, initial) | {initial}
        for n in node_ids - reachable:
            findings.append(Finding(
                code="UNREACHABLE_NODE", category="graph", severity="Warning", node_id=n,
                message=f"'{n}' düğümüne initial_node'dan hiçbir yoldan erişilemiyor.",
                journey_version=journey_version,
                recommendation="Bu düğümü bir transition/on_*_blocked alanına bağlayın ya da kaldırın.",
            ))

        exit_nodes = [n for n, a in self.graph.nodes(data=True) if a.get("node_type") == "exit"]
        if not exit_nodes:
            findings.append(Finding(
                code="NO_TERMINATION", category="graph", severity="Blocker",
                message="Grafikte en az bir 'exit' tipli düğüm yok.",
                journey_version=journey_version, recommendation="En az bir exit node ekleyin.",
            ))

        if not nx.is_directed_acyclic_graph(self.graph):
            for cyc in nx.simple_cycles(self.graph):
                findings.append(Finding(
                    code="CYCLE_DETECTED", category="graph", severity="Information",
                    message=f"Döngü tespit edildi: {' -> '.join(cyc)}",
                    journey_version=journey_version, affected_paths=[" -> ".join(cyc)],
                    recommendation="Döngünün bir sonlanma koşulu/limiti olduğundan emin olun.",
                ))

        for node_id, details in nodes_data.items():
            if details.get("type") == "condition" and not (details.get("on_true_target") and details.get("on_false_target")):
                findings.append(Finding(
                    code="DECISION_MISSING_BRANCH", category="graph", severity="Error", node_id=node_id,
                    message=f"'{node_id}' condition düğümünde on_true_target/on_false_target eksik.",
                    journey_version=journey_version, recommendation="Her iki dalı da tanımlayın.",
                ))

        return findings

    def validate_conditions_findings(self, journey_version: str = "unknown") -> List[Finding]:
        """FR-04: Condition Validation."""
        findings: List[Finding] = []
        nodes_data = self.data.get("nodes", {})
        probability_like = ("probability", "rate", "score")

        for node_id, details in nodes_data.items():
            if details.get("type") != "condition":
                continue

            attr = details.get("condition_attribute", "")
            threshold = details.get("threshold")
            on_true = details.get("on_true_target")
            on_false = details.get("on_false_target")

            if not attr:
                findings.append(Finding(
                    code="CONDITION_MISSING_ATTRIBUTE", category="condition", severity="Error", node_id=node_id,
                    message="condition_attribute tanımlı değil.", journey_version=journey_version,
                    recommendation="Kontrol edilecek kullanıcı alanını belirtin.",
                ))

            if threshold is None or isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
                findings.append(Finding(
                    code="CONDITION_INVALID_THRESHOLD", category="condition", severity="Error", node_id=node_id,
                    message="threshold sayısal değil ya da tanımlı değil.", journey_version=journey_version,
                    recommendation="threshold alanına sayısal bir değer verin.",
                ))

            if on_true and on_false and on_true == on_false:
                findings.append(Finding(
                    code="CONDITION_NO_EFFECT", category="condition", severity="Warning", node_id=node_id,
                    message="on_true_target ve on_false_target aynı düğüme gidiyor.", journey_version=journey_version,
                    recommendation="Dalları farklı düğümlere yönlendirin.",
                ))

            if attr and isinstance(threshold, (int, float)) and any(k in attr.lower() for k in probability_like):
                if threshold <= 0:
                    findings.append(Finding(
                        code="CONDITION_ALWAYS_TRUE", category="condition", severity="Warning", node_id=node_id,
                        message=f"threshold <= 0 iken '{attr}' koşulu her zaman sağlar.",
                        journey_version=journey_version, recommendation="threshold değerini 0'dan büyük yapın.",
                    ))
                elif threshold > 1:
                    findings.append(Finding(
                        code="CONDITION_ALWAYS_FALSE", category="condition", severity="Warning", node_id=node_id,
                        message=f"threshold > 1 iken '{attr}' koşulu hiçbir zaman sağlamaz.",
                        journey_version=journey_version, recommendation="threshold değerini 0-1 aralığında tutun.",
                    ))

        return findings

    def validate_temporal_findings(self, journey_version: str = "unknown", max_total_hours: float = 720) -> List[Finding]:
        """
        FR-05: Temporal & Quiet-Hours Validation.
        """
        findings: List[Finding] = []
        nodes_data = self.data.get("nodes", {})

        if "timezone" not in self.data:
            findings.append(Finding(
                code="MISSING_TIMEZONE", category="temporal", severity="Information", node_id="ROOT", field="timezone",
                message="Journey için 'timezone' alanı tanımlı değil, UTC varsayılacak.",
                journey_version=journey_version,
                recommendation="Journey seviyesinde bir 'timezone' alanı (örn. 'Europe/Istanbul') ekleyin.",
            ))

        quiet_hours = self.data.get("quiet_hours")
        if quiet_hours:
            start_hour = quiet_hours.get("start_hour")
            end_hour = quiet_hours.get("end_hour")
            if start_hour is not None and end_hour is not None:
                if not (0 <= start_hour <= 23) or not (0 <= end_hour <= 23):
                    findings.append(Finding(
                        code="INVALID_QUIET_HOURS", category="temporal", severity="Error", node_id="ROOT", field="quiet_hours",
                        message=f"Geçersiz quiet_hours aralığı: {start_hour} - {end_hour}",
                        journey_version=journey_version,
                        recommendation="Quiet hours saat değerlerini 0-23 arasında tam sayı olarak tanımlayın."
                    ))

        total_wait_hours = 0.0
        for node_id, details in nodes_data.items():
            duration = details.get("duration_hours", 0)
            node_type = details.get("type", "")

            if not isinstance(duration, (int, float)) or isinstance(duration, bool):
                findings.append(Finding(
                    code="INVALID_DURATION_TYPE", category="temporal", severity="Error", node_id=node_id, field="duration_hours",
                    message="duration_hours sayısal değil.", journey_version=journey_version,
                    recommendation="duration_hours alanına sayısal bir değer verin.",
                ))
            elif duration < 0:
                findings.append(Finding(
                    code="NEGATIVE_DELAY", category="temporal", severity="Blocker", node_id=node_id, field="duration_hours",
                    message=f"Node '{node_id}' negatif bekleme süresine sahip: {duration}", journey_version=journey_version,
                    recommendation="duration_hours değerini 0 veya pozitif yapın.",
                ))
            elif node_type == "wait":
                total_wait_hours += duration

            freq_window = details.get("freq_cap_window_hours")
            if freq_window is not None and isinstance(freq_window, (int, float)) and not isinstance(freq_window, bool) and freq_window < 0:
                findings.append(Finding(
                    code="NEGATIVE_FREQ_WINDOW", category="temporal", severity="Error", node_id=node_id, field="freq_cap_window_hours",
                    message="freq_cap_window_hours negatif.", journey_version=journey_version,
                    recommendation="freq_cap_window_hours değerini pozitif yapın.",
                ))

            if node_type in ["push", "email", "sms", "send_email", "send_sms", "push_notification"] and quiet_hours:
                send_time = details.get("scheduled_hour")
                if send_time is not None and quiet_hours.get("start_hour") is not None and quiet_hours.get("end_hour") is not None:
                    s = quiet_hours["start_hour"]
                    e = quiet_hours["end_hour"]
                    in_blackout = (send_time >= s or send_time < e) if s > e else (s <= send_time < e)
                    if in_blackout:
                        findings.append(Finding(
                            code="QUIET_HOURS_VIOLATION", category="temporal", severity="Warning", node_id=node_id, field="scheduled_hour",
                            message=f"Node '{node_id}' sessiz saat aralığında ({s}:00 - {e}:00) bildirim gönderiyor.",
                            journey_version=journey_version,
                            recommendation="Bildirim gönderim saatini quiet_hours dışına kaydırın."
                        ))

        if total_wait_hours > max_total_hours:
            findings.append(Finding(
                code="EXCESSIVE_JOURNEY_DURATION", category="temporal", severity="Warning", node_id="ROOT",
                message=f"Tüm wait düğümlerinin toplamı {total_wait_hours:g} saat, önerilen sınır {max_total_hours:g} saat.",
                journey_version=journey_version,
                recommendation="Journey süresini kısaltmayı ya da bir zaman ufku tanımlamayı düşünün.",
            ))

        return findings

    def get_all_findings(self, journey_version: str = "unknown") -> List[Dict[str, Any]]:
        """
        FR-15: Tüm bulguları güvenli bir şekilde dict formatına çevirerek birleştirir.
        AttributeError ve JSON Serialization hatalarını engeller.
        """
        raw_findings: List[Any] = self.validate_schema_findings(journey_version)
        if isinstance(self.data.get("nodes"), dict) and self.data.get("initial_node"):
            raw_findings += self.validate_graph_findings(journey_version)
            raw_findings += self.validate_conditions_findings(journey_version)
            raw_findings += self.validate_temporal_findings(journey_version)

        formatted_findings = []
        for f in raw_findings:
            if isinstance(f, dict):
                formatted_findings.append(f)
            elif hasattr(f, "to_dict"):
                formatted_findings.append(f.to_dict())
            elif hasattr(f, "model_dump"): # Pydantic v2
                formatted_findings.append(f.model_dump())
            elif hasattr(f, "dict"): # Pydantic v1
                formatted_findings.append(f.dict())
            elif hasattr(f, "__dict__"):
                formatted_findings.append(f.__dict__)
            else:
                formatted_findings.append(str(f))

        return formatted_findings