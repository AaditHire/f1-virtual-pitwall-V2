from fastapi import APIRouter

from f1_pitwall.api.replay import Lap
from f1_pitwall.api.routes import H, Round, Year
from f1_pitwall.domain.simulation import (
    CounterfactualActionOutcome,
    CounterfactualComparison,
    ShortHorizonRequest,
)

router = APIRouter(tags=["Short-horizon simulation"])


@router.post("/api/v1/simulation/short-horizon", response_model=CounterfactualActionOutcome)
async def short_horizon(request: ShortHorizonRequest, h: H):
    return await h.simulation.simulate(
        request.year, request.round, request.lap, request.driver_id, request.action
    )


@router.get(
    "/api/v1/strategy/{year}/{round}/{lap}/drivers/{driver_id}/counterfactuals",
    response_model=CounterfactualComparison,
)
async def counterfactuals(year: Year, round: Round, lap: Lap, driver_id: str, h: H):
    return await h.simulation.compare(year, round, lap, driver_id)
