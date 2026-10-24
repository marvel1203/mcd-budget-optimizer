"""mcd-budget-optimizer：麦当劳省钱点餐助手。"""

__version__ = "1.0.0"

from .models import CouponRule, Item, OrderSpec, Plan, PlanLine, load_menu
from .pricing import build_plan, price_lines
from .solver import InfeasibleError, SolveResult, solve

__all__ = [
    "CouponRule", "Item", "OrderSpec", "Plan", "PlanLine", "load_menu",
    "build_plan", "price_lines", "InfeasibleError", "SolveResult", "solve",
    "__version__",
]
