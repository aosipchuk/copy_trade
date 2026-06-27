from decimal import Decimal
from zipfile import ZipFile

from app.schemas.trader import PositionItem, TraderStatSchema
from app.services.analytics.export_workbook import (
    build_trader_export_workbook,
    trader_export_filename,
    xlsx_media_type,
)
from app.services.hyperliquid.models import Fill


def _fill(
    *,
    coin: str,
    direction: str,
    closed_pnl: str,
    oid: int,
    time: int,
) -> Fill:
    return Fill(
        coin=coin,
        px=Decimal("100"),
        sz=Decimal("2"),
        side="B",
        time=time,
        closedPnl=Decimal(closed_pnl),
        dir=direction,
        oid=oid,
        fee=Decimal("0.05"),
    )


def test_build_trader_export_workbook_contains_only_perp_history(tmp_path) -> None:
    workbook = build_trader_export_workbook(
        display_name="Alpha Trader",
        address="0xabc",
        stats=[
            TraderStatSchema(
                period="allTime",
                pnl_usd=23.0,
                roi_pct=11.5,
                volume_usd=400.0,
            )
        ],
        open_positions=[
            PositionItem(
                coin="BTC",
                side="long",
                size=0.25,
                entry_px=100000.0,
                unrealized_pnl=125.0,
                leverage=5,
            )
        ],
        fills=[
            _fill(
                coin="BTC",
                direction="Open Long",
                closed_pnl="0",
                oid=1,
                time=1_700_000_000_000,
            ),
            _fill(
                coin="BTC",
                direction="Open Long",
                closed_pnl="0",
                oid=1,
                time=1_700_000_001_000,
            ),
            _fill(
                coin="BTC",
                direction="Close Long",
                closed_pnl="23",
                oid=2,
                time=1_700_000_100_000,
            ),
            _fill(
                coin="BTC",
                direction="Close Long",
                closed_pnl="12",
                oid=2,
                time=1_700_000_101_000,
            ),
            _fill(
                coin="PURR",
                direction="Buy",
                closed_pnl="5",
                oid=3,
                time=1_700_000_200_000,
            ),
        ],
    )

    output = tmp_path / "export.xlsx"
    output.write_bytes(workbook)

    with ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "[Content_Types].xml" in names
        assert "xl/workbook.xml" in names
        assert "xl/worksheets/sheet1.xml" in names
        assert "xl/worksheets/sheet3.xml" in names
        assert "xl/worksheets/sheet4.xml" not in names

        workbook_xml = archive.read("xl/workbook.xml").decode()
        assert "Summary" in workbook_xml
        assert "Open Positions" in workbook_xml
        assert "Grouped Trades" in workbook_xml
        assert "All Perp Fills" not in workbook_xml

        trades_xml = archive.read("xl/worksheets/sheet3.xml").decode()
        assert trades_xml.count("<row ") == 3  # header + open order + close order
        assert trades_xml.count("Open Long") == 1
        assert trades_xml.count("Close Long") == 1
        assert "PURR" not in trades_xml
        assert "Buy" not in trades_xml

        summary_xml = archive.read("xl/worksheets/sheet1.xml").decode()
        assert "Realized PnL USD" in summary_xml
        assert "Source Perp Fills" in summary_xml
        assert "<v>35.0</v>" in summary_xml


def test_export_metadata_helpers() -> None:
    assert xlsx_media_type().endswith("spreadsheetml.sheet")
    assert trader_export_filename("Trader / One", "0xabc").startswith(
        "trader_Trader_One_"
    )
    assert trader_export_filename("Trader / One", "0xabc").endswith(".xlsx")
