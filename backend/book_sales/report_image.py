from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


_WIDTH = 1080
_MARGIN = 10
_COLUMN_WIDTHS = (560, 250, 250)
_HEADER_HEIGHT = 62
_ROW_HEIGHT = 56
_BLACK = "#111111"
_WHITE = "#FFFFFF"
_GREEN = "#10E500"
_CYAN = "#00DDEB"
_CREAM = "#FBE5D0"
_YELLOW = "#FFF000"
_ORANGE = "#FF9800"
_LAVENDER = "#D8CFE8"
_CURRENCY_FONT = Path(__file__).resolve().parents[2] / "assets" / "Lato-Regular.ttf"


def _font(size, bold=False):
    root = Path("C:/Windows/Fonts")
    candidates = (
        root / ("arialbd.ttf" if bold else "arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if bold
        else Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf")
        if bold
        else Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
        if bold
        else Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default(size=size)


def _format_number(value):
    formatted = f"{float(value):,.2f}"
    return formatted.rstrip("0").rstrip(".")


def _format_currency(value):
    return f"\u20b9{float(value):,.2f}"


def _draw_cell(draw, bounds, text, fill, font, align="center", padding=14):
    left, top, right, bottom = bounds
    draw.rectangle(bounds, fill=fill, outline=_BLACK, width=3)
    text = str(text)
    text_bounds = draw.textbbox((0, 0), text, font=font)
    text_width = text_bounds[2] - text_bounds[0]
    text_height = text_bounds[3] - text_bounds[1]
    if align == "right":
        x = right - padding - text_width
    elif align == "left":
        x = left + padding
    else:
        x = left + (right - left - text_width) / 2
    y = top + (bottom - top - text_height) / 2 - text_bounds[1]
    draw.text((x, y), text, fill=_BLACK, font=font)


def render_distribution_report_jpeg(report):
    rows_per_table = len(report["daily"])
    data_rows = 2 * (rows_per_table + 1) + 4
    height = (
        2 * _MARGIN
        + 2 * _HEADER_HEIGHT
        + data_rows * _ROW_HEIGHT
    )
    image = Image.new("RGB", (_WIDTH, height), _WHITE)
    draw = ImageDraw.Draw(image)
    regular = _font(25)
    bold = _font(27, bold=True)
    small_bold = _font(23, bold=True)
    currency_font = ImageFont.truetype(str(_CURRENCY_FONT), size=25)

    x0 = _MARGIN
    x1 = x0 + _COLUMN_WIDTHS[0]
    x2 = x1 + _COLUMN_WIDTHS[1]
    x3 = x2 + _COLUMN_WIDTHS[2]
    y = _MARGIN

    try:
        report_day = date.fromisoformat(str(report["date"]))
        date_title = f"{report_day:%A %B} {report_day.day}"
    except ValueError:
        date_title = str(report["date"])

    def table_header(title, color):
        nonlocal y
        _draw_cell(draw, (x0, y, x1, y + _HEADER_HEIGHT), title, color, bold)
        _draw_cell(draw, (x1, y, x2, y + _HEADER_HEIGHT), "Book Count", color, bold)
        _draw_cell(draw, (x2, y, x3, y + _HEADER_HEIGHT), "Points", color, bold)
        y += _HEADER_HEIGHT

    def category_table(rows, title, header_color, total_color, copies_total, points_total):
        nonlocal y
        table_header(title, header_color)
        for row in rows:
            _draw_cell(
                draw,
                (x0, y, x1, y + _ROW_HEIGHT),
                row["category"],
                _WHITE,
                regular,
                align="right",
            )
            _draw_cell(
                draw,
                (x1, y, x2, y + _ROW_HEIGHT),
                f'{int(row["copies"]):,}',
                _CREAM,
                regular,
            )
            _draw_cell(
                draw,
                (x2, y, x3, y + _ROW_HEIGHT),
                _format_number(row["points"]),
                _WHITE,
                regular,
            )
            y += _ROW_HEIGHT
        _draw_cell(
            draw,
            (x0, y, x1, y + _ROW_HEIGHT),
            "Total",
            total_color,
            bold,
            align="right",
        )
        _draw_cell(
            draw,
            (x1, y, x2, y + _ROW_HEIGHT),
            f"{int(copies_total):,}",
            total_color,
            bold,
        )
        _draw_cell(
            draw,
            (x2, y, x3, y + _ROW_HEIGHT),
            _format_number(points_total),
            total_color,
            bold,
        )
        y += _ROW_HEIGHT

    category_table(
        report["daily"],
        date_title,
        _GREEN,
        _GREEN,
        report["daily_total_copies"],
        report["daily_total_points"],
    )
    category_table(
        report["overall"],
        "Overall Summary",
        _CYAN,
        _CYAN,
        report["overall_total_copies"],
        report["overall_total_points"],
    )

    _draw_cell(
        draw,
        (x0, y, x1, y + _ROW_HEIGHT),
        "Target Points",
        _YELLOW,
        bold,
        align="left",
    )
    _draw_cell(
        draw,
        (x1, y, x3, y + _ROW_HEIGHT),
        _format_number(report["target_points"]),
        _YELLOW,
        bold,
    )
    y += _ROW_HEIGHT

    _draw_cell(
        draw,
        (x0, y, x1, y + _ROW_HEIGHT),
        "Total Book Points",
        _GREEN,
        bold,
        align="left",
    )
    _draw_cell(
        draw,
        (x1, y, x2, y + _ROW_HEIGHT),
        _format_number(report["total_book_points"]),
        _GREEN,
        bold,
    )
    percentage = report["target_progress_pct"]
    percentage_text = f"{percentage:.2f} %" if percentage is not None else "N/A"
    _draw_cell(
        draw,
        (x2, y, x3, y + _ROW_HEIGHT),
        percentage_text,
        _GREEN,
        bold,
    )
    y += _ROW_HEIGHT

    for label, amount, color in (
        (
            "Shastra Daan Collected",
            report["shastra_daan_collected"],
            _ORANGE,
        ),
        (
            "Total Lakshmi Collected",
            report["total_lakshmi_collected"],
            _LAVENDER,
        ),
    ):
        _draw_cell(
            draw,
            (x0, y, x1, y + _ROW_HEIGHT),
            label,
            color,
            small_bold,
            align="left",
        )
        _draw_cell(
            draw,
            (x1, y, x3, y + _ROW_HEIGHT),
            _format_currency(amount),
            color,
            currency_font,
        )
        y += _ROW_HEIGHT

    output = BytesIO()
    image.save(output, format="JPEG", quality=94, optimize=True)
    return output.getvalue()
