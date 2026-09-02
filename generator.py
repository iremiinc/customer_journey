from dataclasses import dataclass, field
import random
import uuid
from typing import List

@dataclass
class User:
    user_id: str
    segment: str
    country: str
    push_consent: bool
    email_consent: bool
    sms_consent: bool
    purchase_probability: float
    push_history: List[float] = field(default_factory=list)
    email_history: List[float] = field(default_factory=list)
    sms_history: List[float] = field(default_factory=list)

class CohortGenerator:
    SEGMENTS = {
        "VIP": {"prob": 0.15, "purchase_beta": (5, 2), "consent_rate": 0.90},
        "New User": {"prob": 0.35, "purchase_beta": (2, 5), "consent_rate": 0.70},
        "Churn Risk": {"prob": 0.25, "purchase_beta": (1, 4), "consent_rate": 0.40},
        "Inactive": {"prob": 0.25, "purchase_beta": (1, 8), "consent_rate": 0.30}
    }

    @classmethod
    def generate_cohort(cls, count: int) -> List[User]:
        cohort = []
        segment_keys = list(cls.SEGMENTS.keys())
        segment_probs = [cls.SEGMENTS[k]["prob"] for k in segment_keys]

        for _ in range(count):
            seg = random.choices(segment_keys, weights=segment_probs)[0]
            seg_config = cls.SEGMENTS[seg]
            
            # Consent oranlarını segmente göre simüle et
            push_c = random.random() < seg_config["consent_rate"]
            email_c = random.random() < (seg_config["consent_rate"] + 0.1)
            sms_c = random.random() < (seg_config["consent_rate"] - 0.2)

            # Eşik değerine göre satın alma ihtimali
            a, b = seg_config["purchase_beta"]
            p_prob = round(random.betavariate(alpha=a, beta=b), 2)

            # Frequency Cap test edebilmek için bazı kullanıcılara geçmiş bildirim ekle
            push_hist = [0.0, 1.0, 2.0] if random.random() < 0.25 else []

            user = User(
                user_id=f"usr_{uuid.uuid4().hex[:6]}",
                segment=seg,
                country=random.choice(["TR", "US", "DE"]),
                push_consent=push_c,
                email_consent=email_c,
                sms_consent=sms_c,
                purchase_probability=p_prob,
                push_history=push_hist
            )
            cohort.append(user)
        return cohort