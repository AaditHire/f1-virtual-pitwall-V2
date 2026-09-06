from fastapi import APIRouter

from f1_pitwall.api.replay import Lap
from f1_pitwall.api.routes import H, Round, Year
from f1_pitwall.domain.analysis import DriverAnalysis, PairAnalysis, TrafficAnalysis, TyreAnalysis

router = APIRouter(prefix="/api/v1/analysis", tags=["Deterministic analysis"])


@router.get("/{year}/{round}/{lap}/drivers/{driver_id}", response_model=DriverAnalysis)
async def driver(year: Year, round: Round, lap: Lap, driver_id: str, h: H):
    return await h.analysis.analyze_driver(year, round, lap, driver_id)


@router.get("/{year}/{round}/{lap}/drivers/{driver_id}/tyres", response_model=TyreAnalysis)
async def tyres(year: Year, round: Round, lap: Lap, driver_id: str, h: H):
    return await h.analysis.analyze_tyres(year, round, lap, driver_id)


@router.get("/{year}/{round}/{lap}/drivers/{driver_id}/traffic", response_model=TrafficAnalysis)
async def traffic(year: Year, round: Round, lap: Lap, driver_id: str, h: H):
    return await h.analysis.analyze_traffic(year, round, lap, driver_id)


@router.get("/{year}/{round}/{lap}/undercut", response_model=PairAnalysis)
async def undercut(year: Year, round: Round, lap: Lap, attacker: str, target: str, h: H):
    return await h.analysis.analyze_undercut(year, round, lap, attacker, target)


@router.get("/{year}/{round}/{lap}/overcut", response_model=PairAnalysis)
async def overcut(year: Year, round: Round, lap: Lap, driver: str, target: str, h: H):
    return await h.analysis.analyze_overcut(year, round, lap, driver, target)
