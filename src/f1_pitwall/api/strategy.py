from fastapi import APIRouter

from f1_pitwall.api.replay import Lap
from f1_pitwall.api.routes import H, Round, Year
from f1_pitwall.domain.strategy import ActionComparison, FullGridStrategy, StrategyDecision

router = APIRouter(prefix="/api/v1/strategy", tags=["Causal strategy"])


@router.get("/{year}/{round}/{lap}/drivers/{driver_id}", response_model=StrategyDecision)
async def driver(year: Year, round: Round, lap: Lap, driver_id: str, h: H):
    return await h.strategy.recommend_driver_action(year, round, lap, driver_id)


@router.get("/{year}/{round}/{lap}/all", response_model=FullGridStrategy)
async def all_drivers(year: Year, round: Round, lap: Lap, h: H):
    return await h.strategy.recommend_full_grid(year, round, lap)


@router.get(
    "/{year}/{round}/{lap}/drivers/{driver_id}/actions", response_model=ActionComparison
)
async def actions(year: Year, round: Round, lap: Lap, driver_id: str, h: H):
    return await h.strategy.compare_actions(year, round, lap, driver_id)
