from pydantic import BaseModel, Field


class BudgetInput(BaseModel):
    dataset_id: int
    total_epsilon: float | None = Field(default=None, gt=0, le=10)
    total_delta: float | None = Field(default=None, ge=0, le=1e-2)


class ReleaseInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    dataset_id: int
    epsilon: float = Field(ge=0.05, le=2.0)
    delta: float = Field(default=0.0, ge=0, le=1e-3)
    min_support: float = Field(default=0.12, ge=0.05, le=0.5)
    min_confidence: float = Field(default=0.65, ge=0.2, le=0.95)
