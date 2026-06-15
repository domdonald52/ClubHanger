from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter
def currency(value):
    """Format a decimal/float as '1,234.56' (no $ sign — caller supplies that)."""
    try:
        return f'{Decimal(str(value)):,.2f}'
    except (TypeError, InvalidOperation, ValueError):
        return '0.00'


@register.simple_tag
def budget_val(entries, aircraft_id, fy_year, month):
    """Return budgeted hours from the dict keyed by (aircraft_id, fy_year, month), or '' if absent."""
    v = entries.get((aircraft_id, fy_year, month))
    if v is None:
        return ''
    return f'{v:g}'
