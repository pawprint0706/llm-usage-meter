"""Light/dark palette derived from the OS colour scheme."""

import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication

WARN_THRESHOLD = 70.0
DANGER_THRESHOLD = 90.0


@dataclass(frozen=True)
class Palette:
    dark: bool
    window: str
    card: str
    border: str
    text: str
    subtle: str
    faint: str
    track: str
    hover: str
    ok: str
    warn: str
    danger: str

    def usage_color(self, percent: float) -> str:
        if percent >= DANGER_THRESHOLD:
            return self.danger
        if percent >= WARN_THRESHOLD:
            return self.warn
        return self.ok

    def qcolor(self, value: str, alpha: float = 1.0) -> QColor:
        color = QColor(value)
        if alpha < 1.0:
            color.setAlphaF(alpha)
        return color


DARK = Palette(
    dark=True,
    window="#1B1D21",
    card="#24262C",
    border="#33363E",
    text="#ECEDEF",
    subtle="#A2A7B0",
    faint="#727783",
    track="#343841",
    hover="#2E3138",
    ok="#3FB950",
    warn="#D29922",
    danger="#F85149",
)

LIGHT = Palette(
    dark=False,
    window="#FFFFFF",
    card="#F6F7F9",
    border="#E2E5E9",
    text="#1F2328",
    subtle="#5B6570",
    faint="#8A929C",
    track="#E3E7EC",
    hover="#EDEFF2",
    ok="#1F883D",
    warn="#BF8700",
    danger="#CF222E",
)


def is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    scheme = hints.colorScheme() if hints else Qt.ColorScheme.Unknown
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    window = QGuiApplication.palette().window().color()
    return window.lightness() < 128


def current() -> Palette:
    return DARK if is_dark() else LIGHT


def tray_needs_light_ink() -> bool:
    """True when the tray background is dark, so the icon must be drawn light.

    macOS recolours template images itself, so this only matters on Windows —
    where the taskbar follows ``SystemUsesLightTheme``, not the app-window mode.
    """
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                0,
                winreg.KEY_READ,
            ) as key:
                value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
                return value != 1
        except OSError:
            return True
    return is_dark()
