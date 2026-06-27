import math
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from app.schemas.trader import PositionItem, TraderStatSchema
from app.services.hyperliquid.info_client import USER_FILLS_BY_TIME_MAX_AVAILABLE
from app.services.hyperliquid.models import Fill

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_XML_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XML_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_XML_EXT_PROPS_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
)
_XML_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_REL_WORKSHEET = f"{_XML_DOC_REL_NS}/worksheet"
_REL_STYLES = f"{_XML_DOC_REL_NS}/styles"
_REL_OFFICE_DOCUMENT = f"{_XML_DOC_REL_NS}/officeDocument"
_REL_CORE_PROPS = f"{_XML_PKG_REL_NS}/metadata/core-properties"
_REL_EXT_PROPS = f"{_XML_DOC_REL_NS}/extended-properties"
_CT_OPENXML = "application/vnd.openxmlformats-officedocument"
_CT_PACKAGE = "application/vnd.openxmlformats-package"

STYLE_DEFAULT = 0
STYLE_TITLE = 1
STYLE_SECTION = 2
STYLE_HEADER = 3
STYLE_NUMBER = 4
STYLE_USD = 5
STYLE_INTEGER = 6


@dataclass(frozen=True)
class XlsxCell:
    value: object
    style: int = STYLE_DEFAULT


@dataclass(frozen=True)
class TradeOrder:
    coin: str
    action: str
    direction: str
    size: float
    avg_px: float
    notional_usd: float
    pnl: float
    fee: float
    first_time: int
    last_time: int
    fill_count: int
    oid: int
    raw_direction: str


def xlsx_media_type() -> str:
    return _XLSX_MIME


def trader_export_filename(display_name: str | None, address: str) -> str:
    name = display_name or address
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", name[:32]).strip("_")
    if not slug:
        slug = address[:10]
    date = datetime.now(UTC).date().isoformat()
    return f"trader_{slug}_{date}.xlsx"


def build_trader_export_workbook(
    *,
    display_name: str | None,
    address: str,
    stats: list[TraderStatSchema],
    open_positions: list[PositionItem],
    fills: list[Fill],
) -> bytes:
    perp_fills = [fill for fill in fills if _is_perp_fill(fill)]
    trade_orders = _trade_orders_from_fills(perp_fills)
    period_stats = {stat.period: stat for stat in stats}

    sheets = [
        _sheet(
            "Summary",
            _summary_rows(
                display_name=display_name,
                address=address,
                period_stats=period_stats,
                open_positions=open_positions,
                perp_fills=perp_fills,
                trade_orders=trade_orders,
            ),
            widths=[24, 22, 18, 18, 18, 16],
        ),
        _sheet(
            "Open Positions",
            _open_position_rows(open_positions),
            widths=[14, 12, 16, 16, 18, 12],
            freeze_row=1,
            auto_filter=True,
        ),
        _sheet(
            "Grouped Trades",
            _trade_order_rows(trade_orders),
            widths=[16, 12, 18, 14, 16, 16, 18, 16, 14, 24, 24, 12, 16],
            freeze_row=1,
            auto_filter=True,
        ),
    ]
    return _write_workbook(sheets)


def _cell(value: object, style: int = STYLE_DEFAULT) -> XlsxCell:
    return XlsxCell(value=value, style=style)


def _is_perp_fill(fill: Fill) -> bool:
    return "Long" in fill.dir or "Short" in fill.dir


def _trade_orders_from_fills(fills: list[Fill]) -> list[TradeOrder]:
    by_order: dict[tuple[int, str, str], list[Fill]] = defaultdict(list)
    for fill in fills:
        by_order[(fill.oid, fill.coin, fill.dir)].append(fill)

    orders: list[TradeOrder] = []
    for group in by_order.values():
        total_sz = sum((fill.sz for fill in group), Decimal("0"))
        total_pnl = sum((fill.closed_pnl for fill in group), Decimal("0"))
        total_fee = sum((fill.fee for fill in group), Decimal("0"))
        weighted_px = sum((fill.sz * fill.px for fill in group), Decimal("0"))
        avg_px = float(weighted_px / total_sz) if total_sz else 0.0
        action = _trade_action(group[0].dir)
        direction = _trade_direction(group[0].dir)
        orders.append(
            TradeOrder(
                coin=group[0].coin,
                action=action,
                direction=direction,
                size=float(total_sz),
                avg_px=avg_px,
                notional_usd=float(weighted_px),
                pnl=float(total_pnl),
                fee=float(total_fee),
                first_time=min(fill.time for fill in group),
                last_time=max(fill.time for fill in group),
                fill_count=len(group),
                oid=group[0].oid,
                raw_direction=group[0].dir,
            )
        )

    orders.sort(key=lambda order: order.last_time, reverse=True)
    return orders


def _trade_action(direction: str) -> str:
    if direction.startswith("Open"):
        return "open"
    if direction.startswith("Close"):
        return "close"
    if ">" in direction:
        return "flip"
    return "trade"


def _trade_direction(direction: str) -> str:
    if "Long > Short" in direction:
        return "long_to_short"
    if "Short > Long" in direction:
        return "short_to_long"
    if "Long" in direction:
        return "long"
    if "Short" in direction:
        return "short"
    return ""


def _summary_rows(
    *,
    display_name: str | None,
    address: str,
    period_stats: dict[str, TraderStatSchema],
    open_positions: list[PositionItem],
    perp_fills: list[Fill],
    trade_orders: list[TradeOrder],
) -> list[list[object]]:
    all_time = period_stats.get("allTime")
    total_realized_pnl = sum((fill.closed_pnl for fill in perp_fills), Decimal("0"))
    total_volume = sum((fill.px * fill.sz for fill in perp_fills), Decimal("0"))
    total_fees = sum((fill.fee for fill in perp_fills), Decimal("0"))
    first_fill = min((fill.time for fill in perp_fills), default=None)
    last_fill = max((fill.time for fill in perp_fills), default=None)

    rows: list[list[object]] = [
        [_cell("Trader Portfolio Export", STYLE_TITLE)],
        [],
        [_cell("Trader", STYLE_HEADER), display_name or address],
        [_cell("Address", STYLE_HEADER), address],
        [
            _cell("Exported At (UTC)", STYLE_HEADER),
            _format_ms(int(datetime.now(UTC).timestamp() * 1000)),
        ],
        [_cell("First Perp Fill (UTC)", STYLE_HEADER), _format_ms(first_fill)],
        [_cell("Last Perp Fill (UTC)", STYLE_HEADER), _format_ms(last_fill)],
        [
            _cell("Fill History Scope", STYLE_HEADER),
            (
                "Hyperliquid userFillsByTime; up to "
                f"{USER_FILLS_BY_TIME_MAX_AVAILABLE:,} most recent fills available"
            ),
        ],
        [],
        [_cell("All-Time Perp Summary", STYLE_SECTION)],
        [_cell("Metric", STYLE_HEADER), _cell("Value", STYLE_HEADER)],
        ["ROI %", _cell(all_time.roi_pct if all_time else None, STYLE_NUMBER)],
        ["Realized PnL USD", _cell(float(total_realized_pnl), STYLE_USD)],
        ["Volume USD", _cell(float(total_volume), STYLE_USD)],
        ["Fees USD", _cell(float(total_fees), STYLE_USD)],
        ["Open Positions", _cell(len(open_positions), STYLE_INTEGER)],
        ["Grouped Trades", _cell(len(trade_orders), STYLE_INTEGER)],
        [
            "Closed Trades",
            _cell(
                sum(1 for order in trade_orders if order.action == "close"),
                STYLE_INTEGER,
            ),
        ],
        ["Source Perp Fills", _cell(len(perp_fills), STYLE_INTEGER)],
        [],
        [_cell("Period Metrics", STYLE_SECTION)],
        [
            _cell("Period", STYLE_HEADER),
            _cell("ROI %", STYLE_HEADER),
            _cell("PnL USD", STYLE_HEADER),
            _cell("Volume USD", STYLE_HEADER),
            _cell("Trades", STYLE_HEADER),
            _cell("Win Rate %", STYLE_HEADER),
        ],
    ]

    for period in ("day", "week", "month", "allTime"):
        stat = period_stats.get(period)
        if stat is None:
            continue
        rows.append(
            [
                period,
                _cell(stat.roi_pct, STYLE_NUMBER),
                _cell(stat.pnl_usd, STYLE_USD),
                _cell(stat.volume_usd, STYLE_USD),
                _cell(stat.trade_count, STYLE_INTEGER),
                _cell(stat.win_rate_pct, STYLE_NUMBER),
            ]
        )

    return rows


def _open_position_rows(positions: list[PositionItem]) -> list[list[object]]:
    rows: list[list[object]] = [
        [
            _cell("Coin", STYLE_HEADER),
            _cell("Side", STYLE_HEADER),
            _cell("Size", STYLE_HEADER),
            _cell("Entry Price", STYLE_HEADER),
            _cell("Unrealized PnL", STYLE_HEADER),
            _cell("Leverage", STYLE_HEADER),
        ]
    ]
    for position in sorted(positions, key=lambda p: p.coin):
        rows.append(
            [
                position.coin,
                position.side,
                _cell(position.size, STYLE_NUMBER),
                _cell(position.entry_px, STYLE_NUMBER),
                _cell(position.unrealized_pnl, STYLE_USD),
                _cell(position.leverage, STYLE_INTEGER),
            ]
        )
    return rows


def _trade_order_rows(trade_orders: list[TradeOrder]) -> list[list[object]]:
    rows: list[list[object]] = [
        [
            _cell("Coin", STYLE_HEADER),
            _cell("Action", STYLE_HEADER),
            _cell("Direction", STYLE_HEADER),
            _cell("Size", STYLE_HEADER),
            _cell("Avg Price", STYLE_HEADER),
            _cell("Notional USD", STYLE_HEADER),
            _cell("PnL", STYLE_HEADER),
            _cell("Fee", STYLE_HEADER),
            _cell("First Fill (UTC)", STYLE_HEADER),
            _cell("Last Fill (UTC)", STYLE_HEADER),
            _cell("Fills", STYLE_HEADER),
            _cell("OID", STYLE_HEADER),
            _cell("Raw Direction", STYLE_HEADER),
        ]
    ]
    for order in trade_orders:
        rows.append(
            [
                order.coin,
                order.action,
                order.direction,
                _cell(order.size, STYLE_NUMBER),
                _cell(order.avg_px, STYLE_NUMBER),
                _cell(order.notional_usd, STYLE_USD),
                _cell(order.pnl, STYLE_USD),
                _cell(order.fee, STYLE_USD),
                _format_ms(order.first_time),
                _format_ms(order.last_time),
                _cell(order.fill_count, STYLE_INTEGER),
                _cell(order.oid, STYLE_INTEGER),
                order.raw_direction,
            ]
        )
    return rows


def _format_ms(ms: int | None) -> str:
    if ms is None:
        return ""
    return (
        datetime.fromtimestamp(ms / 1000, tz=UTC)
        .replace(tzinfo=None)
        .isoformat(sep=" ", timespec="seconds")
    )


@dataclass(frozen=True)
class _Sheet:
    name: str
    rows: list[list[object]]
    widths: list[int]
    freeze_row: int | None = None
    auto_filter: bool = False


def _sheet(
    name: str,
    rows: list[list[object]],
    widths: list[int],
    freeze_row: int | None = None,
    auto_filter: bool = False,
) -> _Sheet:
    return _Sheet(
        name=name,
        rows=rows,
        widths=widths,
        freeze_row=freeze_row,
        auto_filter=auto_filter,
    )


def _write_workbook(sheets: list[_Sheet]) -> bytes:
    created = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    sheet_names = [sheet.name for sheet in sheets]
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml(len(sheets)))
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("docProps/core.xml", _core_xml(created))
        archive.writestr("docProps/app.xml", _app_xml(sheet_names))
        archive.writestr("xl/workbook.xml", _workbook_xml(sheet_names))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(len(sheets)))
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, sheet in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(sheet))
    return out.getvalue()


def _worksheet_xml(sheet: _Sheet) -> str:
    max_cols = max([len(row) for row in sheet.rows] + [1])
    max_rows = max(len(sheet.rows), 1)
    last_cell = f"{_col_name(max_cols)}{max_rows}"
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<worksheet xmlns="{_XML_MAIN_NS}"',
        f' xmlns:r="{_XML_DOC_REL_NS}">',
        f'<dimension ref="A1:{last_cell}"/>',
        _sheet_views_xml(sheet.freeze_row),
        _cols_xml(sheet.widths),
        "<sheetData>",
    ]
    for row_idx, row in enumerate(sheet.rows, start=1):
        cells = []
        for col_idx, raw_cell in enumerate(row, start=1):
            cell = raw_cell if isinstance(raw_cell, XlsxCell) else _cell(raw_cell)
            if cell.value is None and cell.style == STYLE_DEFAULT:
                continue
            cells.append(_cell_xml(cell, _cell_ref(row_idx, col_idx)))
        parts.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    parts.append("</sheetData>")
    if sheet.auto_filter and len(sheet.rows) > 1:
        parts.append(f'<autoFilter ref="A1:{last_cell}"/>')
    parts.append("</worksheet>")
    return "".join(parts)


def _sheet_views_xml(freeze_row: int | None) -> str:
    if not freeze_row:
        return '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
    top_left = f"A{freeze_row + 1}"
    return (
        '<sheetViews><sheetView workbookViewId="0">'
        f'<pane ySplit="{freeze_row}" topLeftCell="{top_left}" '
        'activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
    )


def _cols_xml(widths: list[int]) -> str:
    if not widths:
        return ""
    cols = [
        f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>'
        for idx, width in enumerate(widths, start=1)
    ]
    return f"<cols>{''.join(cols)}</cols>"


def _cell_xml(cell: XlsxCell, ref: str) -> str:
    style = f' s="{cell.style}"' if cell.style else ""
    value = cell.value
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{style}><v>{1 if value else 0}</v></c>'
    if isinstance(value, int | float | Decimal) and _is_finite_number(value):
        return f'<c r="{ref}"{style}><v>{_number_text(value)}</v></c>'
    text = escape("" if value is None else str(value))
    return f'<c r="{ref}" t="inlineStr"{style}><is><t>{text}</t></is></c>'


def _is_finite_number(value: int | float | Decimal) -> bool:
    if isinstance(value, Decimal):
        return value.is_finite()
    if isinstance(value, float):
        return math.isfinite(value)
    return True


def _number_text(value: int | float | Decimal) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _cell_ref(row_idx: int, col_idx: int) -> str:
    return f"{_col_name(col_idx)}{row_idx}"


def _col_name(col_idx: int) -> str:
    name = ""
    while col_idx:
        col_idx, rem = divmod(col_idx - 1, 26)
        name = chr(65 + rem) + name
    return name


def _workbook_xml(sheet_names: list[str]) -> str:
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{_XML_MAIN_NS}"'
        f' xmlns:r="{_XML_DOC_REL_NS}">'
        f"<sheets>{sheets}</sheets>"
        "</workbook>"
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    rels = [
        (
            f'<Relationship Id="rId{idx}" '
            f'Type="{_REL_WORKSHEET}" '
            f'Target="worksheets/sheet{idx}.xml"/>'
        )
        for idx in range(1, sheet_count + 1)
    ]
    rels.append(
        f'<Relationship Id="rId{sheet_count + 1}" '
        f'Type="{_REL_STYLES}" '
        'Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_XML_PKG_REL_NS}">'
        f"{''.join(rels)}"
        "</Relationships>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_XML_PKG_REL_NS}">'
        '<Relationship Id="rId1" '
        f'Type="{_REL_OFFICE_DOCUMENT}" '
        'Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" '
        f'Type="{_REL_CORE_PROPS}" '
        'Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" '
        f'Type="{_REL_EXT_PROPS}" '
        'Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def _content_types_xml(sheet_count: int) -> str:
    sheet_overrides = "".join(
        (
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            f'ContentType="{_CT_OPENXML}.spreadsheetml.worksheet+xml"/>'
        )
        for idx in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{_XML_CONTENT_TYPES_NS}">'
        f'<Default Extension="rels" ContentType="{_CT_PACKAGE}.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        f'ContentType="{_CT_OPENXML}.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        f'ContentType="{_CT_OPENXML}.spreadsheetml.styles+xml"/>'
        f"{sheet_overrides}"
        '<Override PartName="/docProps/core.xml" '
        f'ContentType="{_CT_PACKAGE}.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" '
        f'ContentType="{_CT_OPENXML}.extended-properties+xml"/>'
        "</Types>"
    )


def _core_xml(created: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        "<cp:coreProperties "
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:creator>copy-trade</dc:creator>"
        "<cp:lastModifiedBy>copy-trade</cp:lastModifiedBy>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def _app_xml(sheet_names: list[str]) -> str:
    titles = "".join(f"<vt:lpstr>{escape(name)}</vt:lpstr>" for name in sheet_names)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Properties xmlns="{_XML_EXT_PROPS_NS}" '
        f'xmlns:vt="{_XML_VT_NS}">'
        "<Application>copy-trade</Application>"
        f"<Worksheets>{len(sheet_names)}</Worksheets>"
        "<TitlesOfParts>"
        f'<vt:vector size="{len(sheet_names)}" baseType="lpstr">{titles}</vt:vector>'
        "</TitlesOfParts>"
        "</Properties>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<styleSheet xmlns="{_XML_MAIN_NS}">'
        '<numFmts count="2">'
        '<numFmt numFmtId="164" formatCode="#,##0.00"/>'
        '<numFmt numFmtId="165" formatCode="$#,##0.00;[Red]-$#,##0.00"/>'
        "</numFmts>"
        '<fonts count="3">'
        '<font><sz val="11"/><color theme="1"/>'
        '<name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="14"/><color rgb="FF111827"/>'
        '<name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/>'
        '<name val="Calibri"/><family val="2"/></font>'
        "</fonts>"
        '<fills count="4">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid">'
        '<fgColor rgb="FFE5E7EB"/><bgColor indexed="64"/>'
        "</patternFill></fill>"
        '<fill><patternFill patternType="solid">'
        '<fgColor rgb="FF111827"/><bgColor indexed="64"/>'
        "</patternFill></fill>"
        "</fills>"
        '<borders count="2">'
        "<border><left/><right/><top/><bottom/><diagonal/></border>"
        '<border><left style="thin"><color rgb="FFD1D5DB"/></left>'
        '<right style="thin"><color rgb="FFD1D5DB"/></right>'
        '<top style="thin"><color rgb="FFD1D5DB"/></top>'
        '<bottom style="thin"><color rgb="FFD1D5DB"/></bottom><diagonal/></border>'
        "</borders>"
        '<cellStyleXfs count="1">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        "</cellStyleXfs>"
        '<cellXfs count="7">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" '
        'applyFont="1"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" '
        'applyFont="1" applyFill="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="3" borderId="1" xfId="0" '
        'applyFont="1" applyFill="1" applyBorder="1"/>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" '
        'applyNumberFormat="1"/>'
        '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" '
        'applyNumberFormat="1"/>'
        '<xf numFmtId="1" fontId="0" fillId="0" borderId="0" xfId="0" '
        'applyNumberFormat="1"/>'
        "</cellXfs>"
        '<cellStyles count="1">'
        '<cellStyle name="Normal" xfId="0" builtinId="0"/>'
        "</cellStyles>"
        "</styleSheet>"
    )
