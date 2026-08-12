"""Localized formatting of durations, timestamps and money amounts."""

from datetime import datetime

from .i18n import current_lang, tr


def duration(seconds: float) -> str:
    """Compact localized duration: '3h 39m' / '3시간 39분'."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return tr("1분 미만", "<1m")
    minutes = seconds // 60
    if minutes < 60:
        return tr(f"{minutes}분", f"{minutes}m")
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        if not minutes:
            return tr(f"{hours}시간", f"{hours}h")
        return tr(f"{hours}시간 {minutes}분", f"{hours}h {minutes}m")
    days, hours = divmod(hours, 24)
    if not hours:
        return tr(f"{days}일", f"{days}d")
    return tr(f"{days}일 {hours}시간", f"{days}d {hours}h")


def timestamp(value: datetime) -> str:
    """Absolute local wall-clock time, in the OS language's usual shape."""
    local = value.astimezone()
    if current_lang() == "ko":
        period = "오전" if local.hour < 12 else "오후"
        hour = local.hour % 12 or 12
        return f"{local.year}. {local.month}. {local.day}. {period} {hour}:{local.minute:02d}"
    return local.strftime("%Y-%m-%d %H:%M")


def clock(value: float | None = None) -> str:
    """Local 'mm-dd HH:MM:SS' used for the 'updated at' line."""
    moment = datetime.fromtimestamp(value) if value is not None else datetime.now()
    return moment.strftime("%m-%d %H:%M:%S")


def money(amount: float, decimals: int = 2) -> str:
    return f"${amount:,.{decimals}f}"


def money_compact(amount: float) -> str:
    """Drop cents for round plan limits so '$30' does not read as '$30.00'."""
    if float(amount).is_integer():
        return f"${int(amount):,}"
    return money(amount)


def percent(value: float, decimals: int = 0) -> str:
    return f"{value:.{decimals}f}%"
