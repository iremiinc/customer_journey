from typing import Optional, List
from pydantic import BaseModel

class Finding(BaseModel):
    code: str
    category: str  # "schema", "graph", "condition", "temporal"
    severity: str  # "Blocker", "Error", "Warning", "Information"
    message: str
    journey_version: str = "unknown"
    node_id: Optional[str] = None
    field: Optional[str] = None
    recommendation: Optional[str] = None
    affected_paths: Optional[List[str]] = None

    def to_dict(self) -> dict:
        """FastAPI ve JSON serileştirme uyumluluğu için dict dönüşümü"""
        if hasattr(self, "model_dump"):
            return self.model_dump()
        return self.dict()