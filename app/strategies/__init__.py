"""Strategy registry — maps ATS type to strategy class."""

from app.models import ATSStrategy
from .base import BaseStrategy
from .workday import WorkdayStrategy
from .greenhouse import GreenhouseStrategy
from .icims import ICIMSStrategy
from .lever import LeverStrategy
from .ultipro import UltiProStrategy
from .videsktop import ViDesktopStrategy
from .florecruit import FloRecruitStrategy
from .direct import DirectStrategy

STRATEGY_MAP: dict[ATSStrategy, BaseStrategy] = {
    ATSStrategy.WORKDAY: WorkdayStrategy(),
    ATSStrategy.GREENHOUSE: GreenhouseStrategy(),
    ATSStrategy.ICIMS: ICIMSStrategy(),
    ATSStrategy.LEVER: LeverStrategy(),
    ATSStrategy.ULTIPRO: UltiProStrategy(),
    ATSStrategy.VIDESKTOP: ViDesktopStrategy(),
    ATSStrategy.FLORECRUIT: FloRecruitStrategy(),
    ATSStrategy.DIRECT: DirectStrategy(),
}


def get_strategy(strategy_type: ATSStrategy) -> BaseStrategy:
    return STRATEGY_MAP[strategy_type]
