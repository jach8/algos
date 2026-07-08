from __future__ import annotations


class bcolors:
    PURPLE = "\033[95m"
    BLUE = "\033[94m"
    LBLUE = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"

    def print_text(self, text: str, color: str, bold: bool = False, underline: bool = False) -> str:
        color_code = getattr(self, color.upper(), self.ENDC)
        bold_code = self.BOLD if bold else ""
        underline_code = self.UNDERLINE if underline else ""
        return f"{bold_code}{underline_code}{color_code}{text}{self.ENDC}"


COLORS = bcolors()


def color_side_and_exposure(side: str, qty: float | None) -> tuple[str, str]:
    side_upper = side.upper()
    if side_upper == "BUY":
        side_str = COLORS.print_text(side, "GREEN", bold=True)
    elif side_upper == "SELL":
        side_str = COLORS.print_text(side, "RED", bold=True)
    else:
        side_str = side

    if qty is None:
        return side_str, "FLAT"
    if qty > 0:
        return side_str, COLORS.print_text("LONG", "GREEN")
    if qty < 0:
        return side_str, COLORS.print_text("SHORT", "RED")
    return side_str, "FLAT"
