import random
from abc import ABC, abstractmethod
from typing import Dict, Any, Generator, List
from cohort.generator import User


def resolve_next_target(node_config: Dict[str, Any]) -> str:
    """
    'transitions' listesindeki weight alanlarina gore agirlikli rastgele
    hedef secimi yapar. Eskiden burada sadece transitions[0]["target"]
    doniyordu; bu, JSON'da weight:0.5/0.5 gibi olasiliksal dallanmalar
    tanimlansa bile hicbir zaman ikinci/ucuncu hedefe gidilmemesine
    (fiilen %100 - %0 davranmasina) sebep oluyordu.

    - transitions bossa -> "exit" doner (eski davranisla ayni).
    - Tek transition varsa -> agirliktan bagimsiz direkt o hedef doner.
    - Birden fazla transition varsa -> weight'lere gore agirlikli secim yapilir.
      weight belirtilmemis girdiler icin varsayilan agirlik 1.0'dir.
    """
    transitions: List[Dict[str, Any]] = node_config.get("transitions", [])
    if not transitions:
        return "exit"
    if len(transitions) == 1:
        return transitions[0]["target"]

    targets = [t["target"] for t in transitions]
    weights = [t.get("weight", 1.0) for t in transitions]

    # Tum agirliklar 0 ise (hatali/bos konfig) esit sansla dagit
    if sum(weights) <= 0:
        weights = [1.0] * len(targets)

    return random.choices(targets, weights=weights, k=1)[0]


class BaseNodeHandler(ABC):
    @abstractmethod
    def execute(self, env, user: User, node_config: Dict[str, Any]) -> Generator[Any, Any, str]:
        """
        Node mantığını SimPy timeout olayları ile çalıştırır 
        ve kullanıcının yönleneceği sonraki target node_id'yi döner.
        """
        pass


class WaitNodeHandler(BaseNodeHandler):
    def execute(self, env, user: User, node_config: Dict[str, Any]):
        wait_hours = node_config.get("duration_hours", 1)
        yield env.timeout(wait_hours)
        return resolve_next_target(node_config)


class PushNodeHandler(BaseNodeHandler):
    def execute(self, env, user: User, node_config: Dict[str, Any]):
        # 1. Consent Validation
        if not user.push_consent:
            return node_config.get("on_consent_blocked", "blocked_exit")

        # 2. Frequency Cap Validation (Varsayılan: Son 24 saatte max 3 push)
        now = env.now
        max_limit = node_config.get("freq_cap_max", 3)
        window = node_config.get("freq_cap_window_hours", 24)

        recent_pushes = [t for t in user.push_history if now - t <= window]
        if len(recent_pushes) >= max_limit:
            return node_config.get("on_freq_cap_blocked", "freq_cap_exit")

        # Koşullar sağlandı: Gönderim gerçekleşir
        user.push_history.append(now)
        yield env.timeout(0.01)  # Gönderim anlık zaman harcar
        return resolve_next_target(node_config)


class EmailNodeHandler(BaseNodeHandler):
    def execute(self, env, user: User, node_config: Dict[str, Any]):
        # 1. Consent Validation
        if not user.email_consent:
            return node_config.get("on_consent_blocked", "blocked_exit")

        # 2. Frequency Cap Validation (Varsayılan: Son 24 saatte max 2 email)
        now = env.now
        max_limit = node_config.get("freq_cap_max", 2)
        window = node_config.get("freq_cap_window_hours", 24)

        recent_emails = [t for t in user.email_history if now - t <= window]
        if len(recent_emails) >= max_limit:
            return node_config.get("on_freq_cap_blocked", "freq_cap_exit")

        user.email_history.append(now)
        yield env.timeout(0.05)
        return resolve_next_target(node_config)


class SMSNodeHandler(BaseNodeHandler):
    def execute(self, env, user: User, node_config: Dict[str, Any]):
        if not user.sms_consent:
            return node_config.get("on_consent_blocked", "blocked_exit")

        now = env.now
        recent_sms = [t for t in user.sms_history if now - t <= 24]
        if len(recent_sms) >= 1:  # SMS için 24h limit = 1
            return node_config.get("on_freq_cap_blocked", "freq_cap_exit")

        user.sms_history.append(now)
        yield env.timeout(0.02)
        return resolve_next_target(node_config)


class ConditionNodeHandler(BaseNodeHandler):
    def execute(self, env, user: User, node_config: Dict[str, Any]):
        attr_name = node_config.get("condition_attribute", "purchase_probability")
        threshold = node_config.get("threshold", 0.5)

        user_val = getattr(user, attr_name, 0)
        yield env.timeout(0)  # Mantıksal karar anlıktır

        if user_val >= threshold:
            return node_config.get("on_true_target", "exit")
        return node_config.get("on_false_target", "exit")


# Node Registry - Yeni kanal/node eklemek istediğinde buraya eklemen yeterli
NODE_REGISTRY: Dict[str, BaseNodeHandler] = {
    "wait": WaitNodeHandler(),
    "push": PushNodeHandler(),
    "email": EmailNodeHandler(),
    "sms": SMSNodeHandler(),
    "condition": ConditionNodeHandler(),
}