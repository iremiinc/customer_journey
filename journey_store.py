import hashlib
import json
from typing import Dict, Any, Optional

class JourneyVersionExistsError(Exception):
    """Versiyon zaten mevcut olduğunda fırlatılır."""
    pass

class JourneyNotFoundError(Exception):
    """Sorgulanan journey veya versiyon bulunamadığında fırlatılır."""
    pass


class JourneyStore:
    def __init__(self):
        # Format: { journey_id: { version: { "definition": dict, "checksum": str, "created_at": str } } }
        self._store: Dict[str, Dict[str, Dict[str, Any]]] = {}

    @staticmethod
    def calculate_checksum(data: Dict[str, Any]) -> str:
        """JSON verisinin SHA-256 checksum'ını hesaplar."""
        serialized = json.dumps(data, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def save_journey(self, journey_data: Dict[str, Any], overwrite: bool = False) -> Dict[str, Any]:
        """FR-01: Journey'i kaydeder. Overwrite False ise aynı versiyonun üzerine yazmayı engeller."""
        journey_id = journey_data.get("journey_id")
        version = journey_data.get("version", "1.0.0")

        if not journey_id:
            raise ValueError("Journey verisinde 'journey_id' eksik.")

        checksum = self.calculate_checksum(journey_data)

        if journey_id not in self._store:
            self._store[journey_id] = {}

        if version in self._store[journey_id] and not overwrite:
            raise JourneyVersionExistsError(
                f"Journey '{journey_id}' versiyon '{version}' zaten mevcut! Üzerine yazılamaz."
            )

        self._store[journey_id][version] = {
            "definition": journey_data,
            "checksum": checksum,
            "version": version
        }

        return {
            "journey_id": journey_id,
            "version": version,
            "checksum": checksum,
            "status": "stored"
        }

    def get_journey(self, journey_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """Belirtilen journey ve versiyonu getirir."""
        if journey_id not in self._store:
            raise JourneyNotFoundError(f"Journey '{journey_id}' bulunamadı.")

        versions = self._store[journey_id]
        if not versions:
            raise JourneyNotFoundError(f"Journey '{journey_id}' için sürüm bulunamadı.")

        target_version = version or max(versions.keys())
        if target_version not in versions:
            raise JourneyNotFoundError(f"Journey '{journey_id}' için '{target_version}' sürümü bulunamadı.")

        return versions[target_version]["definition"]


# Singleton instance
journey_store = JourneyStore()