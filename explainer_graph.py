import os
import json
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List

from validator.graph_validator import JourneyValidator

load_dotenv()

# --- PYDANTIC MODELLERİ ---
class JourneyTransition(BaseModel):
    target: str = Field(description="Hedef düğüm adı")
    weight: float = Field(default=1.0, description="Geçiş olasılık ağırlığı")

class JourneyNode(BaseModel):
    type: str = Field(description="Düğüm tipi: wait, push, email, sms, condition, exit")
    duration_hours: float = Field(default=0.0, description="Bekleme süresi (saat)")
    on_consent_blocked: str = Field(default="consent_exit", description="İzin yoksa gidilecek düğüm")
    on_freq_cap_blocked: str = Field(default="freq_exit", description="Sıklık sınırında gidilecek düğüm")
    # NOT: condition tipi düğümler bu alanlar olmadan üretilemiyordu (şema eksikti) -> eklendi.
    condition_attribute: str = Field(default="", description="Condition düğümü için kontrol edilecek kullanıcı alanı (örn. purchase_probability)")
    threshold: float = Field(default=0.0, description="Condition düğümü için eşik değer")
    on_true_target: str = Field(default="", description="Condition true ise gidilecek düğüm")
    on_false_target: str = Field(default="", description="Condition false ise gidilecek düğüm")
    transitions: list[JourneyTransition] = Field(default_factory=list, description="Geçiş kurguları")

class OptimizedJourneySchema(BaseModel):
    journey_id: str = Field(description="Journey kimliği")
    initial_node: str = Field(description="Başlangıç düğümünün adı")
    nodes: dict[str, JourneyNode] = Field(description="Tüm düğüm tanımları")

class OptimizedJourneyResponse(BaseModel):
    diagnosis: str = Field(description="Mevcut journey'deki darboğazların ve dropoff nedenlerinin özeti")
    recommended_actions: list[str] = Field(description="Yapılan somut iyileştirme adımları")
    optimized_journey: OptimizedJourneySchema = Field(description="İyileştirilmiş yeni Journey kurgusu")

# --- OPENROUTER LLM AYARI ---
llm = ChatOpenAI(
    model="openai/gpt-4o-mini", # OpenRouter üzerindeki dilediğiniz model adını yazabilirsiniz
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    openai_api_base="https://openrouter.ai/api/v1",
    temperature=0.2
)

# OpenRouter uyumu için method="json_mode" olarak ayarlanmıştır
structured_llm = llm.with_structured_output(OptimizedJourneyResponse, method="json_mode")

OPTIMIZER_PROMPT = """
Sen uzman bir Customer Journey Optimizer Agent'ısın.
Görevin sana verilen Journey JSON kurgusunu ve Simülasyon Metriklerini analiz ederek dönüşüm oranını (Conversion Rate) artırmaktır.

Mevcut Journey:
{journey_json}

Simülasyon Metrikleri:
{metrics_json}

Aşağıdaki adımları uygula:
1. Metriklerdeki engelleri (Consent, Frequency Cap, Dropoff) ve yüksek bekleme sürelerini tespit et.
2. Mantıksal iyileştirmeler yap (Örn: Wait sürelerini düşür, SMS yerine alternatif yollar sun veya exit kollarını bağla).
3. Geçerli, hatasız ve düğümleri eksiksiz bağlanmış YENİ BİR JOURNEY kurgusu üret.

ZORUNLU BAĞLANTI KURALLARI (çok önemli, ihlal etme):
- "nodes" altında tanımladığın HER düğüme, initial_node'dan başlayarak en az bir "transitions", "on_consent_blocked", "on_freq_cap_blocked", "on_true_target" veya "on_false_target" alanı aracılığıyla erişilebilmelidir. Grafikte hiçbir yerden referans verilmeyen "yetim" düğüm BIRAKMA.
- Yeni bir alternatif kanal düğümü ekliyorsan (örn. SMS, email), bu düğümü mutlaka başka bir düğümün "on_consent_blocked" veya "on_freq_cap_blocked" hedefi olarak (ya da normal bir "transitions" hedefi olarak) journey'e bağla. Sadece düğümü tanımlayıp hiçbir yerden çağırmadan bırakma.
- Her düğümün "transitions", "on_consent_blocked", "on_freq_cap_blocked", "on_true_target", "on_false_target" alanlarındaki hedefler de "nodes" sözlüğünde tanımlı olmalı (var olmayan bir düğüme referans verme).
- "type": "exit" olan düğümlerin "transitions" listesi boş olmalıdır.
- "type": "condition" olan düğümler "condition_attribute", "threshold", "on_true_target" ve "on_false_target" alanlarını doldurmalıdır.
{feedback_section}
Aşağıdaki JSON şemasına BİREBİR UYGUN bir yanıt döndür:
{schema}
"""

MAX_VALIDATION_RETRIES = 2


def _find_dangling_targets(journey: dict) -> List[str]:
    """'nodes' sözlüğünde tanımlı olmayan hedeflere işaret eden referansları bulur."""
    nodes = journey.get("nodes", {})
    node_ids = set(nodes.keys())
    dangling = set()
    for details in nodes.values():
        for edge in details.get("transitions", []):
            target = edge.get("target")
            if target and target not in node_ids:
                dangling.add(target)
        for field_name in ("on_consent_blocked", "on_freq_cap_blocked", "on_true_target", "on_false_target"):
            target = details.get(field_name)
            if target and target not in node_ids:
                dangling.add(target)
    return sorted(dangling)


def _build_feedback_section(problems: List[str]) -> str:
    if not problems:
        return ""
    lines = ["\nÖNCEKİ DENEMENDE ŞU HATALAR TESPİT EDİLDİ, LÜTFEN DÜZELT:"]
    for p in problems:
        lines.append(f"- {p}")
    return "\n".join(lines) + "\n"


def _validate_optimized_journey(optimized_dict: dict) -> List[str]:
    """
    graph_validator.py'yi (aynı validasyon mantığını) kullanarak LLM'in
    ürettiği journey'i denetler. Bulunan sorunları insan-okunur mesaj
    listesi olarak döner; liste boşsa journey temiz demektir.

    FR-04 (condition) ve FR-05 (temporal) bulgularından sadece
    Blocker/Error seviyesindekiler buraya dahil edilir - Warning/
    Information seviyesi (örn. "timezone eksik") LLM'i gereksiz
    yere yeniden denemeye zorlamasın diye retry-loop'u tetiklemez.
    """
    problems: List[str] = []

    dangling = _find_dangling_targets(optimized_dict)
    if dangling:
        problems.append(f"Tanımsız düğümlere referans veriliyor: {dangling}")

    try:
        validator = JourneyValidator(optimized_dict)
        validator.validate_schema()
        structure = validator.validate_graph_structure()
        unreachable = structure.get("unreachable_nodes", [])
        if unreachable:
            problems.append(
                f"Şu düğümler initial_node'dan erişilemez durumda (yetim kaldı): {unreachable}"
            )

        blocking_findings = [
            f for f in (validator.validate_conditions_findings() + validator.validate_temporal_findings())
            if f.severity in ("Blocker", "Error")
        ]
        for f in blocking_findings:
            node_ref = f" (düğüm: {f.node_id})" if f.node_id else ""
            problems.append(f"[{f.code}] {f.message}{node_ref}")
    except Exception as e:
        problems.append(f"Şema/graf doğrulama hatası: {e}")

    return problems


def run_journey_optimizer_agent(journey: dict, metrics: dict) -> dict:
    prompt = ChatPromptTemplate.from_template(OPTIMIZER_PROMPT)
    chain = prompt | structured_llm

    feedback_section = ""
    last_response = None
    last_problems: List[str] = []

    for attempt in range(MAX_VALIDATION_RETRIES + 1):
        last_response = chain.invoke({
            "journey_json": json.dumps(journey, indent=2),
            "metrics_json": json.dumps(metrics, indent=2),
            "schema": json.dumps(OptimizedJourneyResponse.model_json_schema(), indent=2),
            "feedback_section": feedback_section
        })

        optimized_dict = last_response.optimized_journey.model_dump()
        last_problems = _validate_optimized_journey(optimized_dict)

        if not last_problems:
            result = last_response.model_dump()
            result["validation_warnings"] = []
            return result

        if attempt < MAX_VALIDATION_RETRIES:
            feedback_section = _build_feedback_section(last_problems)
            continue

    # MAX_VALIDATION_RETRIES tükendi, LLM sorunu düzeltemedi.
    # Journey'i sessizce kırık göndermek yerine, en son cevabı
    # 'validation_warnings' alanıyla birlikte döndürüyoruz ki
    # API tüketicisi (dashboard) bunu kullanıcıya gösterebilsin.
    result = last_response.model_dump()
    result["validation_warnings"] = last_problems
    return result
